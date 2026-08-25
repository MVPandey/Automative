/**
 * Automative enforcement as a native DeepSeek Harness plugin.
 *
 * It sits on dsh's typed interception points and asks the automative CLI what to do:
 *
 * | dsh point              | CLI event        | effect                                            |
 * |------------------------|------------------|---------------------------------------------------|
 * | `agent/session-start`  | `session-start`  | inject the brief                                  |
 * | `agent/pre-step`       | `prompt-submit`  | append the one-line run status to the prompt      |
 * | `tools/pre-execute`    | `pre-tool-use`   | `deny` edits outside scope, protected files, git   |
 * | `tools/post-execute`   | `post-tool-use`  | `block` after tampering (files restored, run halted)|
 * | `tools/post-execute`   | `post-tool-batch`| wall-clock guard, reported as context              |
 * | `agent/turn-stopping`  | `stop`           | `steer()` the agent back into the loop             |
 *
 * The policy stays in one place (the Python CLI) on purpose: it is the same code that hashes the
 * protected files, checks the heartbeat, and refuses a stale `try`. What this plugin adds over the
 * generic `dsh-hooks-claude-code` bridge is typed decisions on dsh's own seams, the wall-clock guard
 * (Claude Code's `PostToolBatch` has no bridge mapping), and a heartbeat on every point.
 *
 * @module automative-dsh
 */

import { existsSync } from 'node:fs'
import { join } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import type { Agent, PreStepDecision } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import type { ContentBlock, MessageSource, UserMessage } from '@deepseek-ai/dsh-llm'
import type {} from '@deepseek-ai/dsh-shell'
import type { PostToolDecision, PreToolDecision, ToolExecution } from '@deepseek-ai/dsh-tools'
import { hookCommand, hookPayload, parseAnswer, toolPayload, type HookAnswer, type HookEvent } from './bridge.ts'

export const name = 'automative'
export const inject = ['shell']

/** Plugin config. Every field is optional; the defaults follow the CLI's own conventions. */
export interface Config {
  /** The automative executable. Default: `$AUTOMATIVE_PLUGIN_ROOT/.venv/bin/automative` if present, else `automative` on PATH. */
  cli?: string
  /** The Automative checkout; exported as `AUTOMATIVE_PLUGIN_ROOT` so the CLI can protect its own files. */
  pluginRoot?: string
  /** Working directory for the CLI. Default: the session's cwd. */
  projectDir?: string
  /** Per-call timeout for the CLI. Default: 20 s. */
  timeoutMs?: number
}

export const Config: z<Config> = z.object({
  cli: z.string(),
  pluginRoot: z.string(),
  projectDir: z.string(),
  timeoutMs: z.number().default(20_000),
})

const SOURCE: MessageSource = { kind: 'plugin', plugin: 'automative' }

/** Pick the CLI binary: explicit config, then the checkout's venv, then PATH. */
export function resolveCli(config: Config, env: NodeJS.ProcessEnv): string {
  if (config.cli) return config.cli
  const root = config.pluginRoot ?? env['AUTOMATIVE_PLUGIN_ROOT']
  if (root) {
    const candidate = join(root, '.venv', 'bin', 'automative')
    if (existsSync(candidate)) return candidate
  }
  return 'automative'
}

function textMessage(text: string): UserMessage {
  const content: ContentBlock[] = [{ type: 'text', text }]
  return createUserMessage({ content, source: SOURCE })
}

export function apply(ctx: Context, config: Config): void {
  const cli = resolveCli(config, process.env)
  const timeoutMs = config.timeoutMs ?? 20_000
  const disposed = new AbortController()
  ctx.effect(() => () => disposed.abort(), 'automative: abort in-flight CLI calls')

  /** Run one hook event through the CLI in the session workspace. Never throws: a broken CLI must not take the agent down. */
  async function call(event: HookEvent, agent: Agent | undefined, extra: Record<string, unknown>, signal?: AbortSignal): Promise<HookAnswer> {
    const cwd = config.projectDir ?? agent?.session.header.cwd ?? process.cwd()
    const payload = hookPayload(event, { sessionId: agent?.session.header.id ?? '', cwd }, extra)
    const env: Record<string, string> = {}
    const root = config.pluginRoot ?? process.env['AUTOMATIVE_PLUGIN_ROOT']
    if (root) env['AUTOMATIVE_PLUGIN_ROOT'] = root
    try {
      const spec = ctx.shell.resolve({
        command: hookCommand(cli, event),
        workdir: cwd,
        stdin: JSON.stringify(payload) + '\n',
        timeoutMs,
        env,
        signal: signal ?? disposed.signal,
      })
      const result = await ctx.shell.run(spec)
      return parseAnswer(result.stdout.text, result.stderr.text, result.exitCode)
    } catch (error: unknown) {
      ctx.logger.warn(`automative: ${event} hook failed: ${String(error)}`)
      return { exitCode: 1, stderr: String(error) }
    }
  }

  // The brief on session start, injected into the first request.
  ctx.on('agent/session-start', ({ agent, source }) => {
    void call('session-start', agent, { source }).then((answer) => {
      if (answer.text) agent.inject(textMessage(answer.text))
    })
  })

  // One status line on every prompt, appended after the user's own messages.
  ctx.on('agent/pre-step', async ({ agent, messages, signal }, next): Promise<PreStepDecision> => {
    const downstream = await next()
    if (messages.length === 0 || downstream.kind !== 'enter') return downstream
    const prompt = messages.flatMap(m => m.content).filter((b): b is Extract<ContentBlock, { type: 'text' }> => b.type === 'text').map(b => b.text).join('')
    const answer = await call('prompt-submit', agent, { prompt }, signal)
    if (!answer.text) return downstream
    return { kind: 'enter', messages: [...downstream.messages, textMessage(answer.text)] }
  })

  // Scope, protected files, git writes, and harness config: refused before the tool runs.
  ctx.on('tools/pre-execute', async (exec: ToolExecution, next): Promise<PreToolDecision> => {
    const payload = toolPayload(exec.name, exec.arguments)
    if (payload === undefined) return next()
    const answer = await call('pre-tool-use', exec.agent, { ...payload, tool_use_id: exec.callId }, exec.signal)
    if (answer.decision === 'deny') return { kind: 'deny', reason: answer.reason ?? 'blocked by automative' }
    return next()
  })

  // Tampering detection after the call, then the wall-clock guard.
  ctx.on('tools/post-execute', async (exec: ToolExecution, result, next): Promise<PostToolDecision> => {
    const payload = toolPayload(exec.name, exec.arguments)
    if (payload === undefined) return next()
    const response = result.content.filter((b): b is Extract<ContentBlock, { type: 'text' }> => b.type === 'text').map(b => b.text).join('')
    const answer = await call('post-tool-use', exec.agent, { ...payload, tool_use_id: exec.callId, tool_response: response }, exec.signal)
    if (answer.decision === 'block') {
      return { kind: 'block', feedback: [{ type: 'text', text: answer.reason ?? 'blocked by automative' }] }
    }
    const downstream = await next()
    const clock = await call('post-tool-batch', exec.agent, {}, exec.signal)
    if (!clock.stderr.trim()) return downstream
    const notice = textMessage(clock.stderr.trim())
    return { ...downstream, additionalContexts: [notice, ...downstream.additionalContexts ?? []] }
  })

  // "Do not stop" as a steer at the stopping boundary; the CLI caps consecutive blocks itself.
  ctx.on('agent/turn-stopping', async ({ agent, signal }): Promise<void> => {
    const answer = await call('stop', agent, { stop_hook_active: false }, signal)
    if (answer.decision === 'block' && answer.reason) agent.steer(textMessage(answer.reason))
  })
}

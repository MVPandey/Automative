/**
 * Pure mapping between dsh's typed tool calls and the automative hook contract.
 *
 * The policy itself lives in the Python CLI (`automative hook <event>`), which reads the hook JSON on
 * stdin and answers on stdout. This module only builds those payloads and reads those answers, so it
 * can be tested without a running harness.
 */

/** The subset of Claude Code hook events the CLI understands. */
export type HookEvent =
  | 'session-start'
  | 'prompt-submit'
  | 'pre-tool-use'
  | 'post-tool-use'
  | 'post-tool-batch'
  | 'stop'

/** A parsed CLI answer: a decision, or plain text meant for the model, or nothing. */
export interface HookAnswer {
  /** `deny` (pre-tool) or `block` (post-tool, stop). */
  readonly decision?: 'deny' | 'block'
  /** Why, in the CLI's words; shown to the model. */
  readonly reason?: string
  /** Free text the CLI wants injected as context (session start, prompt submit). */
  readonly text?: string
  /** The CLI's exit code (2 means "block" for hook contracts that use exit codes). */
  readonly exitCode: number
  /** stderr, when any; the wall-clock guard reports through it. */
  readonly stderr: string
}

/** Tool argument shapes dsh registers for its file and shell tools. */
interface EditArgs { readonly file_path?: unknown }
interface BashArgs { readonly command?: unknown }

/**
 * Map a dsh tool call to the `{tool_name, tool_input}` pair the CLI expects.
 * dsh's `edit`/`write` become Claude Code's `Edit`/`Write`, `bash` becomes `Bash`, and `read` becomes
 * `Read` (the CLI seals the held-out directory against reads). Any other tool (search, web, subagents)
 * returns `undefined`: the CLI has nothing to say about it.
 */
export function toolPayload(name: string, args: unknown): { tool_name: string; tool_input: Record<string, unknown> } | undefined {
  const record = (args !== null && typeof args === 'object') ? args as Record<string, unknown> : {}
  switch (name) {
    case 'edit':
    case 'write': {
      const filePath = (record as EditArgs).file_path
      return { tool_name: name === 'edit' ? 'Edit' : 'Write', tool_input: { file_path: typeof filePath === 'string' ? filePath : '' } }
    }
    case 'bash': {
      const command = (record as BashArgs).command
      return { tool_name: 'Bash', tool_input: { command: typeof command === 'string' ? command : '' } }
    }
    case 'read': {
      const filePath = (record as EditArgs).file_path
      return { tool_name: 'Read', tool_input: { file_path: typeof filePath === 'string' ? filePath : '' } }
    }
    default:
      return undefined
  }
}

/** The stdin document for one hook call, in Claude Code's field names (the CLI's dialect). */
export function hookPayload(
  event: HookEvent,
  base: { sessionId: string; cwd: string },
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return { session_id: base.sessionId, cwd: base.cwd, hook_event_name: event, ...extra }
}

/**
 * Read the CLI's answer. `pre-tool-use` denies through `hookSpecificOutput.permissionDecision`;
 * `post-tool-use` and `stop` block through a top-level `decision`; `session-start` and `prompt-submit`
 * print plain text. Anything unparseable is treated as text.
 */
export function parseAnswer(stdout: string, stderr: string, exitCode: number | null): HookAnswer {
  const code = exitCode ?? 1
  const trimmed = stdout.trim()
  if (trimmed.length === 0) return { exitCode: code, stderr }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return { text: trimmed, exitCode: code, stderr }
  }
  if (parsed === null || typeof parsed !== 'object') return { text: trimmed, exitCode: code, stderr }
  const doc = parsed as Record<string, unknown>
  const specific = doc['hookSpecificOutput']
  if (specific !== null && typeof specific === 'object') {
    const inner = specific as Record<string, unknown>
    if (inner['permissionDecision'] === 'deny') {
      return { decision: 'deny', reason: stringOr(inner['permissionDecisionReason'], 'blocked by automative'), exitCode: code, stderr }
    }
  }
  if (doc['decision'] === 'block') {
    return { decision: 'block', reason: stringOr(doc['reason'], 'blocked by automative'), exitCode: code, stderr }
  }
  return { exitCode: code, stderr }
}

function stringOr(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.length > 0 ? value : fallback
}

/** The shell command that runs one hook event through the CLI. */
export function hookCommand(cli: string, event: HookEvent): string {
  return `${shellQuote(cli)} hook ${event}`
}

/** Quote a path for bash unless it is already a bare word. */
export function shellQuote(value: string): string {
  return /^[A-Za-z0-9_./-]+$/.test(value) ? value : `'${value.replace(/'/g, `'\\''`)}'`
}

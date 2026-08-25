import assert from 'node:assert/strict'
import { test } from 'node:test'
import { hookCommand, hookPayload, parseAnswer, shellQuote, toolPayload } from '../src/bridge.ts'

test('tool payloads map dsh tools onto the CLI dialect', () => {
  assert.deepEqual(toolPayload('edit', { file_path: 'src/a.py', old_string: 'x', new_string: 'y' }), {
    tool_name: 'Edit',
    tool_input: { file_path: 'src/a.py' },
  })
  assert.deepEqual(toolPayload('write', { file_path: 'bench.py', content: '' }), {
    tool_name: 'Write',
    tool_input: { file_path: 'bench.py' },
  })
  assert.deepEqual(toolPayload('bash', { command: 'git commit -m x' }), {
    tool_name: 'Bash',
    tool_input: { command: 'git commit -m x' },
  })
  assert.equal(toolPayload('read', { file_path: 'x' }), undefined)
  assert.equal(toolPayload('bash', null)?.tool_input['command'], '')
})

test('answers: deny, block, text, empty, junk', () => {
  const deny = parseAnswer(JSON.stringify({ hookSpecificOutput: { permissionDecision: 'deny', permissionDecisionReason: 'no' } }), '', 0)
  assert.equal(deny.decision, 'deny')
  assert.equal(deny.reason, 'no')
  const block = parseAnswer(JSON.stringify({ decision: 'block', reason: 'keep going' }), '', 0)
  assert.equal(block.decision, 'block')
  assert.equal(block.reason, 'keep going')
  const text = parseAnswer('AUTOMATIVE run r-1: ACTIVE\nGoal: x', '', 0)
  assert.equal(text.text, 'AUTOMATIVE run r-1: ACTIVE\nGoal: x')
  assert.equal(text.decision, undefined)
  const empty = parseAnswer('', 'wall clock exhausted', 0)
  assert.equal(empty.text, undefined)
  assert.equal(empty.stderr, 'wall clock exhausted')
  const allow = parseAnswer(JSON.stringify({ hookSpecificOutput: { permissionDecision: 'allow' } }), '', 0)
  assert.equal(allow.decision, undefined)
  assert.equal(parseAnswer('{"decision":"block"}', '', null).reason, 'blocked by automative')
})

test('payload and command shapes', () => {
  assert.deepEqual(hookPayload('pre-tool-use', { sessionId: 's', cwd: '/p' }, { tool_name: 'Bash' }), {
    session_id: 's',
    cwd: '/p',
    hook_event_name: 'pre-tool-use',
    tool_name: 'Bash',
  })
  assert.equal(hookCommand('automative', 'stop'), 'automative hook stop')
  assert.equal(hookCommand('/My Dir/.venv/bin/automative', 'stop'), "'/My Dir/.venv/bin/automative' hook stop")
  assert.equal(shellQuote("it's"), "'it'\\''s'")
})

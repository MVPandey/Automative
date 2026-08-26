"""Claude Code hook handlers: the deterministic controls that a prompt cannot reason around.

Each handler takes the hook's stdin JSON and returns a :class:`HookResponse`; the CLI serializes it. Every
handler is a no-op unless the working directory belongs to a project with an active run.
"""

import contextlib
import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import automative
from automative import budget as budget_rules
from automative import ledger as ledger_io
from automative import lock as lock_io
from automative import strategies as strategy_io
from automative import trace as trace_io
from automative.errors import AutomativeError
from automative.heartbeat import write_heartbeat
from automative.paths import DOTDIR
from automative.runloop import Project, RunLoop
from automative.scope import Classification, classify
from automative.state import RunState, RunStatus
from automative.views import render_brief

__all__ = ['HookResponse', 'handle']

EDIT_TOOLS = frozenset({'Edit', 'Write', 'MultiEdit', 'NotebookEdit'})
READ_TOOLS = frozenset({'Read'})
PRIVILEGE_RE = re.compile(r'(^|[\s;&|(`])(sudo|doas|su)\b')
TOKEN_SPLIT_RE = re.compile(r'[\s"\'`;|&()<>=]+')
STALL_BLOCKS = 3

GIT_WRITE_RE = re.compile(
    r'\bgit\b[^|;&]*\b(commit|revert|reset|stash|rebase|merge|push|cherry-pick|update-ref|notes)\b'
    r'|\bgit\b[^|;&]*\bcheckout\b[^|;&]*--'
    r'|\bgit\b[^|;&]*\badd\b[^|;&]*(\s-A\b|\s\.(\s|$)|\s--all\b)'
)
NO_VERIFY_RE = re.compile(r'--no-verify\b')
HOOK_KILL_RE = re.compile(
    r'disableAllHooks|\bclaude\s+(plugin|config)\b|uv\s+tool\s+install[^|;&]*automative|pip\s+install[^|;&]*automative'
)
REDIRECT_RE = re.compile(r'(?:(?<![\d&>])>{1,2}|&>)\s*["\']?([^\s"\'|;&]+)')
WRITE_TOOLS = frozenset({'tee', 'mv', 'cp', 'rm', 'patch', 'truncate', 'chmod', 'ln', 'install', 'rsync', 'dd'})
SEGMENT_SPLIT_RE = re.compile(r'\|\||&&|;|\|')
NOTES_RE = re.compile(r'^\.automative/runs/[^/]+/notes\.md$')
SETTINGS_RE = re.compile(
    r'(^|/)\.claude/settings[^/]*\.json$|(^|/)hooks\.json$|(^|/)\.git/(hooks|config)(/|$)|(^|/)\.claude/(hooks|plugins)(/|$)'
)


@dataclass(frozen=True, slots=True)
class HookResponse:
    """What to emit for a hook: JSON on stdout, exit code, optional stderr text."""

    payload: dict[str, object] | None = None
    exit_code: int = 0
    stderr: str = ''
    stdout_text: str | None = None
    marker: str = ''
    extra: tuple[str, ...] = field(default=())


def _deny(reason: str) -> HookResponse:
    return HookResponse(
        payload={
            'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'deny',
                'permissionDecisionReason': reason,
            }
        }
    )


def _load(cwd: Path) -> tuple[RunLoop, RunState] | None:
    try:
        project = Project.load(cwd)
    except AutomativeError:
        return None
    loop = RunLoop(project)
    try:
        state = loop.load_state()
    except AutomativeError:
        return None
    if state is None:
        return None
    return loop, state


def _rel(loop: RunLoop, raw: str) -> str | None:
    path = Path(raw)
    if not path.is_absolute():
        path = loop.paths.root / path
    try:
        return path.resolve().relative_to(loop.paths.root.resolve()).as_posix()
    except ValueError:
        return None


def _protected_roots() -> tuple[str, ...]:
    roots = [str(Path(automative.__file__).resolve().parent)]
    plugin_root = os.environ.get('AUTOMATIVE_PLUGIN_ROOT') or os.environ.get('CLAUDE_PLUGIN_ROOT')
    if plugin_root:
        roots.append(str(Path(plugin_root).resolve()))
    roots.append(str((Path.home() / '.claude').resolve()))
    return tuple(roots)


def _is_harness_path(raw: str) -> bool:
    resolved = str(Path(raw).expanduser().resolve()) if raw else ''
    if any(resolved.startswith(root) for root in _protected_roots()):
        return True
    return bool(SETTINGS_RE.search(raw))


def _write_targets(cmd: str) -> tuple[str, ...]:
    """Paths a shell command writes to: redirect targets and arguments of known write tools."""
    targets: list[str] = []
    for raw_segment in SEGMENT_SPLIT_RE.split(cmd):
        segment = raw_segment.strip()
        if not segment:
            continue
        targets.extend(m.group(1) for m in REDIRECT_RE.finditer(segment))
        words = segment.replace('"', ' ').replace("'", ' ').split()
        if words and words[0] == 'sudo':
            words = words[1:]
        if not words:
            continue
        tool = words[0].rsplit('/', 1)[-1]
        if tool in WRITE_TOOLS:
            targets.extend(w for w in words[1:] if not w.startswith('-'))
        elif tool == 'sed' and any(w.startswith('-i') for w in words[1:]):
            args = [w for w in words[1:] if not w.startswith('-')]
            targets.extend(args[1:])  # the first non-flag argument is the sed expression
    return tuple(t for t in targets if t not in ('/dev/null', '/dev/stderr', '/dev/stdout'))


def _touches(target: str, protected: tuple[str, ...], root: Path) -> bool:
    """Whether a write target names a protected file, harness state, or a harness install path."""
    clean = target.strip()
    while clean.startswith('./'):
        clean = clean[2:]
    if NOTES_RE.match(clean):
        return False  # the agent's scratchpad is the one harness path it may write
    if clean == DOTDIR or clean.startswith(f'{DOTDIR}/'):
        return True
    if any(clean == p or clean.endswith('/' + p) for p in protected):
        return True
    absolute = str((root / clean).resolve()) if not clean.startswith('/') else str(Path(clean).resolve())
    return any(absolute.startswith(r) for r in _protected_roots()) or bool(SETTINGS_RE.search(clean))


def _is_sealed(rel: str, sealed: tuple[str, ...]) -> bool:
    """Whether a project-relative path is one the contract sealed against reads (a file or a directory of them)."""
    clean = rel.strip().rstrip('/')
    while clean.startswith('./'):
        clean = clean[2:]
    if not clean or not sealed:
        return False
    for pattern in sealed:
        if fnmatch.fnmatchcase(clean, pattern) or fnmatch.fnmatchcase(clean, pattern.replace('**/', '')):
            return True
        base = pattern.split('*', 1)[0].rstrip('/')
        if base and (clean == base or clean.startswith(base + '/')):
            return True
    return False


def _sealed_tokens(cmd: str, sealed: tuple[str, ...], loop: RunLoop) -> tuple[str, ...]:
    """Tokens of a shell command that name a sealed path."""
    hits = []
    for token in TOKEN_SPLIT_RE.split(cmd):
        if not token or token.startswith('-'):
            continue
        rel = _rel(loop, token) if token.startswith('/') else token
        if rel is not None and _is_sealed(rel, sealed):
            hits.append(token)
    return tuple(hits)


def _record_denial(loop: RunLoop, state: RunState, reason: str, **data: object) -> None:
    state.denied_tool_calls += 1
    loop._event(state.run_id, 'denied', reason, **data)
    trace_io.append(
        loop.paths.trace_file,
        run_id=state.run_id,
        ts=loop.clock(),
        session_id=state.session_id,
        tool=str(data.get('tool', '')),
        tool_input={k: v for k, v in data.items() if k != 'tool'},
        denied=True,
        reason=reason,
    )
    status = budget_rules.evaluate(state, loop._budget(), loop.project.doc.spec.metric)
    if status.should_stop and status.stop_reason is budget_rules.StopReason.DENIED_TOOL_CALLS:
        loop.halt(state, status.stop_reason, status.message, escalate=True)
    else:
        loop._save(state)
    loop.record_shown('hook:deny', reason, args=dict(data))


# ----- handlers ----------------------------------------------------------------------------------------


def _pre_tool_use(loop: RunLoop, state: RunState, payload: dict[str, object]) -> HookResponse:
    tool = str(payload.get('tool_name', ''))
    tool_input = payload.get('tool_input') or {}
    if not isinstance(tool_input, dict):
        return HookResponse()
    spec = loop.project.doc.spec
    if tool in READ_TOOLS:
        raw = str(tool_input.get('file_path') or '')
        rel = _rel(loop, raw)
        if rel is not None and _is_sealed(rel, spec.sealed):
            reason = f'{rel} is sealed by the contract (held-out data); the harness only tells you pass or fail'
            _record_denial(loop, state, reason, tool=tool, path=rel)
            return _deny(reason)
        return HookResponse()
    if tool in EDIT_TOOLS:
        raw = str(tool_input.get('file_path') or tool_input.get('notebook_path') or '')
        if _is_harness_path(raw):
            reason = f'{raw} is harness configuration; editing it during a run is not allowed'
            _record_denial(loop, state, reason, tool=tool, path=raw)
            return _deny(reason)
        rel = _rel(loop, raw)
        if rel is None:
            reason = f'{raw} is outside the project'
            _record_denial(loop, state, reason, tool=tool, path=raw)
            return _deny(reason)
        if rel == loop.paths.relative(loop.paths.notes_file(state.run_id)):
            return HookResponse()
        if rel.startswith(f'{DOTDIR}/'):
            reason = f'{rel} is harness state; use the automative CLI instead'
            _record_denial(loop, state, reason, tool=tool, path=rel)
            return _deny(reason)
        kind = classify(rel, spec.scope, spec.protected)
        if kind is Classification.PROTECTED:
            reason = f'{rel} is protected (verifier/tests/spec). Changing it would invalidate the metric.'
            _record_denial(loop, state, reason, tool=tool, path=rel)
            return _deny(reason)
        if kind is Classification.OTHER:
            reason = f'{rel} is outside scope ({", ".join(spec.scope)}). Note the idea in notes.md and tell the human.'
            _record_denial(loop, state, reason, tool=tool, path=rel)
            return _deny(reason)
        return HookResponse()
    if tool == 'Bash':
        cmd = str(tool_input.get('command', ''))
        sealed_hits = _sealed_tokens(cmd, spec.sealed, loop)
        if sealed_hits:
            reason = f'sealed by the contract (held-out data): {", ".join(sorted(set(sealed_hits)))}'
            _record_denial(loop, state, reason, tool=tool, command=cmd)
            return _deny(reason)
        if PRIVILEGE_RE.search(cmd):
            reason = 'sudo, doas, and su are not allowed during a run'
            _record_denial(loop, state, reason, tool=tool, command=cmd)
            return _deny(reason)
        if spec.metric.heldout and spec.metric.heldout in cmd:
            reason = 'the held-out command is run by `automative try` only; its result is pass or fail'
            _record_denial(loop, state, reason, tool=tool, command=cmd)
            return _deny(reason)
        if NO_VERIFY_RE.search(cmd):
            reason = '`--no-verify` is never allowed during a run'
            _record_denial(loop, state, reason, tool=tool, command=cmd)
            return _deny(reason)
        if HOOK_KILL_RE.search(cmd):
            reason = 'commands that change hooks, plugins, or the automative install are blocked during a run'
            _record_denial(loop, state, reason, tool=tool, command=cmd)
            return _deny(reason)
        if GIT_WRITE_RE.search(cmd):
            reason = 'git writes are CLI-only during a run; use `automative try` / `automative discard`'
            _record_denial(loop, state, reason, tool=tool, command=cmd)
            return _deny(reason)
        hit = [t for t in _write_targets(cmd) if _touches(t, tuple(state.protected), loop.paths.root)]
        if hit:
            reason = f'shell write touching protected/harness paths: {", ".join(sorted(set(hit)))}'
            _record_denial(loop, state, reason, tool=tool, command=cmd)
            return _deny(reason)
    return HookResponse()


def _post_tool_use(loop: RunLoop, state: RunState, payload: dict[str, object]) -> HookResponse:
    trace_io.append(
        loop.paths.trace_file,
        run_id=state.run_id,
        ts=loop.clock(),
        session_id=state.session_id,
        tool=str(payload.get('tool_name', '')),
        tool_input=payload.get('tool_input') or {},
        response=payload.get('tool_response'),
    )
    changed = lock_io.check(loop.paths.root, state.protected)
    if not changed:
        return HookResponse()
    tracked = tuple(p for p in changed if state.protected.get(p) != 'missing')
    with contextlib.suppress(AutomativeError):
        loop.git.restore(tracked)
    detail = 'protected files modified by a tool call: ' + ', '.join(changed)
    loop.halt(state, budget_rules.StopReason.INTEGRITY, detail, escalate=True)
    reason = f'{detail}. They were restored from git and the run was stopped for a human to review.'
    loop.record_shown('hook:post-tool-use', reason)
    return HookResponse(payload={'decision': 'block', 'reason': reason})


def _post_tool_batch(loop: RunLoop, state: RunState) -> HookResponse:
    budget = loop._budget()
    if budget.minutes and loop._elapsed_s(state) >= budget.minutes * 60:
        loop.halt(
            state,
            budget_rules.StopReason.WALL_CLOCK,
            f'wall-clock budget {budget.minutes} min exhausted',
            escalate=False,
        )
        return HookResponse(stderr='automative: wall-clock budget exhausted; run `automative run end`.')
    return HookResponse()


def _stop(loop: RunLoop, state: RunState, payload: dict[str, object]) -> HookResponse:
    if state.status is not RunStatus.ACTIVE or state.escalated:
        return HookResponse()
    status = budget_rules.evaluate(state, loop._budget(), loop.project.doc.spec.metric)
    if status.should_stop:
        assert status.stop_reason is not None
        loop.halt(state, status.stop_reason, status.message, escalate=status.escalate)
        return HookResponse()
    state.stop_hook.blocks_since_last_try += 1
    if state.stop_hook.blocks_since_last_try >= STALL_BLOCKS:
        state.status = RunStatus.PAUSED
        state.paused_at = loop.clock()
        state.stop_reason = budget_rules.StopReason.STALLED.value
        loop._save(state)
        loop._event(state.run_id, 'stalled', f'agent tried to stop {STALL_BLOCKS} times without a try; paused')
        return HookResponse(stderr='automative: run paused as stalled; a human can `automative run resume`.')
    loop._save(state)
    suggestions = strategy_io.suggest_lines(loop.paths.strategies_file, loop.project.doc.spec.tags, 3)
    brief = render_brief(loop.brief(suggestions))
    reason = (
        f'The automative run is still active ({state.stop_hook.blocks_since_last_try}/{STALL_BLOCKS} stop attempts). '
        'Do not stop. Make ONE atomic change inside scope, then run `automative try -m "..." --hypothesis "..."`.\n\n'
        + brief
    )
    loop.record_shown('hook:stop', reason, args={'blocks': state.stop_hook.blocks_since_last_try})
    return HookResponse(payload={'decision': 'block', 'reason': reason})


def _session_start(loop: RunLoop, state: RunState) -> HookResponse:
    if state.status is RunStatus.DONE:
        return HookResponse()
    suggestions = strategy_io.suggest_lines(loop.paths.strategies_file, loop.project.doc.spec.tags, 3)
    text = 'automative: a run is active in this project.\n' + render_brief(loop.brief(suggestions))
    loop.record_shown('hook:session-start', text)
    return HookResponse(stdout_text=text)


def _prompt_submit(loop: RunLoop, state: RunState) -> HookResponse:
    if state.status is not RunStatus.ACTIVE:
        return HookResponse()
    brief = loop.brief()
    text = (
        f'[automative {state.run_id}: iter {brief.iteration}/{brief.iterations_budget or "inf"}, best {brief.best}, '
        f'{brief.tries_since_best} since best] Goal: {brief.goal}'
    )
    loop.record_shown('hook:prompt-submit', text)
    return HookResponse(stdout_text=text)


def _config_change(state: RunState) -> HookResponse:
    if state.status is RunStatus.ACTIVE:
        return HookResponse(exit_code=2, stderr='automative: configuration changes are blocked while a run is active.')
    return HookResponse()


def handle(event: str, payload: dict[str, object], cwd: Path | None = None) -> HookResponse:
    """Dispatch one hook event. Unknown events and projects without runs are no-ops."""
    loaded = _load(cwd or Path.cwd())
    if loaded is None:
        return HookResponse()
    loop, state = loaded
    write_heartbeat(loop.paths.heartbeat_file, str(payload.get('session_id') or 'unknown'))
    if state.session_id is None and payload.get('session_id'):
        state.session_id = str(payload['session_id'])
        loop._save(state)
    match event:
        case 'pre-tool-use':
            return _pre_tool_use(loop, state, payload) if state.status is RunStatus.ACTIVE else HookResponse()
        case 'post-tool-use':
            return _post_tool_use(loop, state, payload) if state.status is RunStatus.ACTIVE else HookResponse()
        case 'post-tool-batch':
            return _post_tool_batch(loop, state) if state.status is RunStatus.ACTIVE else HookResponse()
        case 'stop':
            return _stop(loop, state, payload)
        case 'session-start':
            return _session_start(loop, state)
        case 'prompt-submit':
            return _prompt_submit(loop, state)
        case 'config-change':
            return _config_change(state)
        case _:
            return HookResponse()


def rows_for(loop: RunLoop, run_id: str) -> tuple[ledger_io.EventRow, ...]:
    """Denial/integrity events for a run (used by reports and tests)."""
    return tuple(
        r for r in ledger_io.read(loop.paths.ledger_file) if isinstance(r, ledger_io.EventRow) and r.run_id == run_id
    )

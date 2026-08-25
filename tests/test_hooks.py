"""Red-team suite: one test per enforcement-matrix row."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from automative import hooks
from automative.runloop import RunLoop
from automative.state import RunStatus


def _pre(loop: RunLoop, tool: str, **tool_input: object) -> hooks.HookResponse:
    payload = {'tool_name': tool, 'tool_input': tool_input, 'session_id': 's1', 'cwd': str(loop.paths.root)}
    return hooks.handle('pre-tool-use', payload, loop.paths.root)


def _denied(response: hooks.HookResponse) -> bool:
    return bool(response.payload) and response.payload['hookSpecificOutput']['permissionDecision'] == 'deny'  # type: ignore[index]


def test_noop_without_run(loop: RunLoop) -> None:
    response = hooks.handle(
        'pre-tool-use', {'tool_name': 'Edit', 'tool_input': {'file_path': 'score.sh'}}, loop.paths.root
    )
    assert response.payload is None and response.exit_code == 0


def test_noop_outside_project(tmp_path: Path) -> None:
    assert hooks.handle('stop', {}, tmp_path).payload is None


def test_edit_protected_denied(started: RunLoop) -> None:
    assert _denied(_pre(started, 'Edit', file_path=str(started.paths.root / 'score.sh')))
    assert started.require_state().denied_tool_calls == 1


def test_edit_out_of_scope_denied(started: RunLoop) -> None:
    assert _denied(_pre(started, 'Write', file_path='other.txt'))


def test_edit_in_scope_allowed(started: RunLoop) -> None:
    assert not _denied(_pre(started, 'Edit', file_path='src/data.txt'))


def test_notes_allowed(started: RunLoop) -> None:
    run_id = started.require_state().run_id
    assert not _denied(_pre(started, 'Write', file_path=f'.automative/runs/{run_id}/notes.md'))


def test_edit_harness_state_denied(started: RunLoop) -> None:
    assert _denied(_pre(started, 'Write', file_path='.automative/ledger.jsonl'))


def test_edit_settings_denied(started: RunLoop) -> None:
    assert _denied(_pre(started, 'Write', file_path=str(started.paths.root / '.claude' / 'settings.local.json')))
    assert _denied(_pre(started, 'Edit', file_path=str(Path.home() / '.claude' / 'settings.json')))


@pytest.mark.parametrize(
    'command',
    [
        'git commit -am hack',
        'git reset --hard HEAD~1',
        'git checkout -- score.sh',
        'git add -A && git commit -m x',
        'git stash',
        'git push --force',
        'git commit --no-verify -m x',
        'sed -i "s/wc/echo 1 #/" score.sh',
        'echo 1 > score.sh',
        'cat x | tee guard.sh',
        'rm -rf .automative/',
        'echo hi &> score.sh',
        'cp /tmp/x guard.sh',
        'python3 gen.py >> score.sh',
        'sed -i.bak "s/a/b/" ./guard.sh',
        'claude plugin uninstall automative',
        'uv tool install --force automative',
        'python -c "open(\'.claude/settings.json\',\'w\')" ; echo "disableAllHooks"',
    ],
)
def test_bash_denied(started: RunLoop, command: str) -> None:
    assert _denied(_pre(started, 'Bash', command=command)), command


@pytest.mark.parametrize(
    'command',
    [
        'automative try -m "x" --hypothesis "y"',
        'git log --oneline -10',
        'git diff HEAD~1',
        'uv run pytest -q',
        'echo hello > src/data.txt',
        'cat score.sh',
        'cat score.sh 2>/dev/null; cat guard.sh 2>&1 | head',
        'automative ledger --last 10 2>&1; ls .automative/runs/*/ 2>/dev/null',
        'python3 - <<EOF\nprint(open("score.sh").read())\nEOF',
        'git log --oneline -10 > /dev/null',
    ],
)
def test_bash_allowed(started: RunLoop, command: str) -> None:
    assert not _denied(_pre(started, 'Bash', command=command)), command


def test_denials_escalate_and_stop(started: RunLoop) -> None:
    for _ in range(5):
        _pre(started, 'Edit', file_path='score.sh')
    state = started.require_state()
    assert state.status is RunStatus.DONE and state.stop_reason == 'denied_tool_calls' and state.escalated


def test_post_tool_use_restores_tampered_file(started: RunLoop, repo: Path) -> None:
    original = (repo / 'score.sh').read_text()
    (repo / 'score.sh').write_text('#!/bin/sh\necho 0\n')
    response = hooks.handle('post-tool-use', {'tool_name': 'Bash', 'session_id': 's1'}, repo)
    assert response.payload and response.payload['decision'] == 'block'
    assert (repo / 'score.sh').read_text() == original
    state = started.require_state()
    assert state.status is RunStatus.DONE and state.stop_reason == 'integrity'


def test_stop_blocks_then_stalls(started: RunLoop) -> None:
    first = hooks.handle('stop', {'session_id': 's1'}, started.paths.root)
    assert first.payload and first.payload['decision'] == 'block'
    assert 'automative try' in str(first.payload['reason'])
    hooks.handle('stop', {'session_id': 's1'}, started.paths.root)
    third = hooks.handle('stop', {'session_id': 's1'}, started.paths.root)
    assert third.payload is None
    state = started.require_state()
    assert state.status is RunStatus.PAUSED and state.stop_reason == 'stalled'


def test_try_resets_stop_counter(started: RunLoop, write_data: Callable[[str], None]) -> None:
    hooks.handle('stop', {'session_id': 's1'}, started.paths.root)
    write_data('hello world\n')
    started.try_change('a', 'b')
    assert started.require_state().stop_hook.blocks_since_last_try == 0


def test_stop_allows_when_done(started: RunLoop, write_data: Callable[[str], None]) -> None:
    started.end('finished')
    assert hooks.handle('stop', {'session_id': 's1'}, started.paths.root).payload is None


def test_stop_halts_when_budget_exhausted(started: RunLoop) -> None:
    state = started.require_state()
    state.iter = 30
    from automative.state import save_state

    save_state(started.paths.state_file, state)
    response = hooks.handle('stop', {'session_id': 's1'}, started.paths.root)
    assert response.payload is None
    assert started.require_state().status is RunStatus.DONE


def test_heartbeat_written(started: RunLoop) -> None:
    hooks.handle('prompt-submit', {'session_id': 'abc'}, started.paths.root)
    beat = json.loads(started.paths.heartbeat_file.read_text())
    assert beat['session_id'] == 'abc' and beat['counter'] == 1
    assert started.require_state().session_id == 'abc'


def test_session_start_prints_brief(started: RunLoop) -> None:
    response = hooks.handle('session-start', {'session_id': 's1'}, started.paths.root)
    assert response.stdout_text and 'AUTOMATIVE run' in response.stdout_text


def test_config_change_blocked(started: RunLoop) -> None:
    assert hooks.handle('config-change', {'session_id': 's1'}, started.paths.root).exit_code == 2


def test_heartbeat_required_blocks_try(
    tmp_path: Path, home: Path, write_data: Callable[[str], None], repo: Path
) -> None:
    from automative.errors import IntegrityError
    from automative.runloop import Project

    spec = repo / 'AUTOMATIVE.md'
    spec.write_text(spec.read_text().replace('require_hooks: false', 'require_hooks: true'))
    from tests.conftest import git

    git(repo, 'commit', '-qam', 'require hooks')
    loop = RunLoop(Project.load(repo))
    loop.start('h')
    write_data('hello\n')
    with pytest.raises(IntegrityError):
        loop.try_change('x', 'y')
    assert loop.require_state().stop_reason == 'hooks_dead'

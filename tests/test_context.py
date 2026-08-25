"""Model-visible means logged: shown rows, the try-time view check, the audit, and the attempt tree."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from automative import audit as audit_io
from automative import hooks, report
from automative import ledger as ledger_io
from automative.decide import Outcome
from automative.errors import ContextError, ScopeError, StateError
from automative.runloop import RunLoop
from automative.state import save_state
from tests.conftest import git


def _shown(loop: RunLoop) -> tuple[ledger_io.ShownRow, ...]:
    return ledger_io.shown(loop.paths.ledger_file, loop.require_state().run_id)


def test_start_and_try_record_what_was_shown(started: RunLoop, write_data: Callable[[str], None]) -> None:
    rows = _shown(started)
    assert [r.surface for r in rows] == ['start']
    assert rows[0].text is not None and 'AUTOMATIVE run' in rows[0].text
    assert rows[0].sha256 == ledger_io.text_sha(rows[0].text)
    write_data('hello world\n')
    outcome = started.try_change('shorter', 'fewer bytes')
    rows = _shown(started)
    assert [r.surface for r in rows] == ['start', 'try']
    assert rows[1].text == outcome.text and 'KEEP' in outcome.text
    assert outcome.row.context_sha == rows[0].context_sha
    assert rows[1].context_sha == started.require_state().view_sha()


def test_try_refuses_a_stale_view(started: RunLoop, write_data: Callable[[str], None]) -> None:
    state = started.require_state()
    state.denied_tool_calls += 1  # the run changed and nobody showed the agent
    save_state(started.paths.state_file, state)
    write_data('hello\n')
    with pytest.raises(ContextError) as info:
        started.try_change('x', 'y')
    assert info.value.exit_code == 20
    events = [r for r in ledger_io.read(started.paths.ledger_file) if isinstance(r, ledger_io.EventRow)]
    assert events[-1].event == 'stale_context'
    started.brief_text()
    assert started.try_change('x', 'y').decision.outcome is Outcome.KEEP


def test_logged_context_can_be_switched_off(repo: Path, write_data: Callable[[str], None]) -> None:
    spec = repo / 'AUTOMATIVE.md'
    spec.write_text(spec.read_text().replace('require_hooks: false', 'require_hooks: false, logged_context: false'))
    git(repo, 'commit', '-qam', 'no context check')
    from automative.runloop import Project

    loop = RunLoop(Project.load(repo))
    loop.start('free')
    state = loop.require_state()
    state.denied_tool_calls += 1
    save_state(loop.paths.state_file, state)
    write_data('hello\n')
    assert loop.try_change('x', 'y').decision.outcome is Outcome.KEEP


def test_hook_denial_is_shown(started: RunLoop) -> None:
    payload = {'tool_name': 'Edit', 'tool_input': {'file_path': 'score.sh'}, 'session_id': 's1'}
    hooks.handle('pre-tool-use', payload, started.paths.root)
    rows = _shown(started)
    assert rows[-1].surface == 'hook:deny' and 'protected' in (rows[-1].text or '')
    assert rows[-1].context_sha == started.require_state().view_sha()


def test_stop_hook_reason_is_shown(started: RunLoop) -> None:
    response = hooks.handle('stop', {'session_id': 's1'}, started.paths.root)
    assert response.payload and response.payload['decision'] == 'block'
    rows = _shown(started)
    assert rows[-1].surface == 'hook:stop' and rows[-1].text == response.payload['reason']


def test_audit_passes_then_catches_tampering(started: RunLoop, write_data: Callable[[str], None]) -> None:
    write_data('hello world\n')
    started.try_change('a', 'b')
    started.brief_text()
    write_data('hello\n')
    started.try_change('c', 'd')
    run_id = started.require_state().run_id
    result = audit_io.audit(ledger_io.read(started.paths.ledger_file), run_id)
    assert result.ok and result.iterations == 2 and result.shown >= 4
    assert dict(result.by_surface)['try'] == 2
    assert 'OK' in report.render_audit(result)

    path = started.paths.ledger_file
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        row = json.loads(line)
        if row['type'] == 'shown' and row['surface'] == 'start':
            row['text'] = 'something the agent never saw'
            lines[index] = json.dumps(row)
    path.write_text('\n'.join(lines) + '\n')
    tampered = audit_io.audit(ledger_io.read(path), run_id)
    assert not tampered.ok and tampered.corrupt_shown
    assert 'FAILED' in report.render_audit(tampered)


def test_audit_flags_a_try_with_no_shown_view(started: RunLoop, write_data: Callable[[str], None]) -> None:
    write_data('hello world\n')
    started.try_change('a', 'b')
    run_id = started.require_state().run_id
    rows = [r for r in ledger_io.read(started.paths.ledger_file) if not isinstance(r, ledger_io.ShownRow)]
    result = audit_io.audit(rows, run_id)
    assert result.unlogged_iterations == (1,) and not result.ok


# ----- attempt tree ----------------------------------------------------------------------------------------


def test_checkout_builds_the_next_try_on_an_earlier_attempt(
    started: RunLoop, repo: Path, write_data: Callable[[str], None]
) -> None:
    data = repo / 'src' / 'data.txt'
    write_data('hello world hello world\n')
    assert started.try_change('shorter', 'fewer bytes').decision.outcome is Outcome.KEEP  # i1, 24 bytes
    write_data('hello world hello world hello world hello\n')
    assert started.try_change('longer', 'worse').decision.outcome is Outcome.DISCARD  # i2, reverted
    assert data.read_text() == 'hello world hello world\n'

    _rev, files = started.checkout(2)
    assert files == ('src/data.txt',) and data.read_text() == 'hello world hello world hello world hello\n'
    state = started.require_state()
    assert state.checked_out == 2
    assert state.view_sha() == _shown(started)[-1].context_sha
    brief = started.brief()
    assert brief.checked_out == 2 and 'Working from attempt i2' in report.render_brief(brief)

    write_data('hello\n')
    outcome = started.try_change('fix the long one', 'debug from i2', mode='debug')
    assert outcome.decision.outcome is Outcome.KEEP
    assert outcome.row.parent_iter == 2 and '(from i2)' in outcome.text
    assert started.require_state().checked_out is None
    rows = ledger_io.iterations(started.paths.ledger_file)
    assert [r.parent_iter for r in rows] == [0, 1, 2]
    tree = report.render_tree(rows, 36.0)
    assert tree.splitlines()[0] == 'i0 baseline 36'
    assert '    i2 discard' in tree and '      i3 keep' in tree


def test_checkout_baseline_and_refusals(started: RunLoop, repo: Path, write_data: Callable[[str], None]) -> None:
    write_data('hello world\n')
    started.try_change('shorter', 'fewer bytes')
    write_data('dirty\n')
    with pytest.raises(ScopeError):
        started.checkout(0)
    started.discard()
    assert started.require_state().checked_out is None
    with pytest.raises(StateError):
        started.checkout(9)
    started.checkout(0)
    assert (repo / 'src' / 'data.txt').read_text() == 'hello world hello world hello world\n'
    assert git(repo, 'diff', '--name-only') == 'src/data.txt'  # working tree only
    assert git(repo, 'diff', '--cached', '--name-only') == ''  # index untouched
    started.discard()
    assert (repo / 'src' / 'data.txt').read_text() == 'hello world\n'


def test_restore_from_deletes_files_absent_in_source(started: RunLoop, repo: Path) -> None:
    extra = repo / 'src' / 'extra.txt'
    extra.write_text('new file\n')
    started.try_change('add a file', 'more bytes are worse')  # discard; i1 ref still has extra.txt
    assert not extra.exists()
    started.checkout(1)
    assert extra.exists()
    started.discard()
    assert not extra.exists()

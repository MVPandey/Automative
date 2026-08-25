"""Git-backed tests of the harness state machine."""

from collections.abc import Callable
from pathlib import Path

import pytest

from automative import ledger as ledger_io
from automative.decide import Outcome
from automative.errors import GitError, IntegrityError, ScopeError, StateError
from automative.runloop import Project, RunLoop, parse_prediction
from automative.state import RunStatus
from tests.conftest import FakeClock, git, make_repo


def test_start_records_baseline_and_branch(started: RunLoop, repo: Path) -> None:
    state = started.require_state()
    assert state.baseline is not None and state.baseline.score == 36.0
    assert state.best is not None and state.best.iter == 0
    assert git(repo, 'symbolic-ref', '--short', 'HEAD') == state.branch
    rows = ledger_io.read(started.paths.ledger_file)
    assert rows[0].type == 'run_start'
    assert 'score.sh' in state.protected and 'AUTOMATIVE.md' in state.protected


def test_start_refuses_dirty_tree(loop: RunLoop, write_data: Callable[[str], None]) -> None:
    write_data('dirty\n')
    with pytest.raises(GitError):
        loop.start('x')


def test_start_refuses_when_active(started: RunLoop) -> None:
    with pytest.raises(StateError):
        started.start('again')


def test_keep_then_discard(started: RunLoop, write_data: Callable[[str], None], repo: Path) -> None:
    write_data('hello world hello world\n')
    kept = started.try_change('shorter', 'fewer bytes', predict='-30%')
    assert kept.decision.outcome is Outcome.KEEP
    assert kept.best == 24.0
    assert kept.row.prediction_error_pct is not None and kept.row.prediction_error_pct < 5
    write_data('hello world hello world hello world hello\n')
    lost = started.try_change('longer', 'worse')
    assert lost.decision.outcome is Outcome.DISCARD
    assert (repo / 'src' / 'data.txt').read_text() == 'hello world hello world\n'
    log = git(repo, 'log', '--oneline', '-3')
    assert 'Revert' in log and 'automative(i2)' in log
    state = started.require_state()
    assert state.iter == 2 and state.tries_since_best == 1 and state.best is not None and state.best.iter == 1
    assert git(repo, 'rev-parse', '--verify', f'refs/automative/{state.run_id}/2')


def test_guard_fail_reverts(started: RunLoop, write_data: Callable[[str], None], repo: Path) -> None:
    write_data('bye\n')
    result = started.try_change('remove hello', 'guard fails')
    assert result.decision.outcome is Outcome.GUARD_FAIL
    assert 'hello' in (repo / 'src' / 'data.txt').read_text()


def test_crash_counts_consecutive_errors(started: RunLoop, write_data: Callable[[str], None], repo: Path) -> None:
    started.project.git.run('checkout', '-q', '--', '.')
    (repo / 'score.sh').write_text('#!/bin/sh\nexit 2\n')
    started.project.git.run('commit', '-q', '-a', '-m', 'break verifier')
    # rehash so the protected-file check passes (simulates a run started with a broken verifier)
    state = started.require_state()
    from automative.lock import snapshot

    state.protected = snapshot(repo, tuple(state.protected))
    from automative.state import save_state

    save_state(started.paths.state_file, state)
    for text in ('a hello\n', 'b hello\n', 'c hello\n'):
        write_data(text)
        result = started.try_change('x', 'y')
        assert result.decision.outcome is Outcome.CRASH
    assert result.stopped and result.budget.stop_reason is not None
    assert started.require_state().status is RunStatus.DONE


def test_refuses_out_of_scope_changes(started: RunLoop, repo: Path, write_data: Callable[[str], None]) -> None:
    write_data('hello\n')
    (repo / 'other.txt').write_text('changed\n')
    with pytest.raises(ScopeError):
        started.try_change('x', 'y')
    assert started.require_state().iter == 0


def test_refuses_empty_diff(started: RunLoop) -> None:
    with pytest.raises(ScopeError):
        started.try_change('x', 'y')


def test_protected_edit_halts_run(started: RunLoop, repo: Path, write_data: Callable[[str], None]) -> None:
    (repo / 'score.sh').write_text('#!/bin/sh\necho 1\n')
    write_data('hello\n')
    with pytest.raises(IntegrityError):
        started.try_change('cheat', 'edit verifier')
    state = started.require_state()
    assert state.status is RunStatus.DONE and state.stop_reason == 'integrity' and state.escalated


def test_spec_edit_halts_run(started: RunLoop, repo: Path, write_data: Callable[[str], None]) -> None:
    spec = repo / 'AUTOMATIVE.md'
    spec.write_text(spec.read_text().replace('iterations: 30', 'iterations: 999'))
    write_data('hello\n')
    with pytest.raises(IntegrityError):
        started.try_change('cheat', 'extend budget')


def test_iteration_budget_stops(repo: Path, clock: FakeClock, write_data: Callable[[str], None]) -> None:
    loop = RunLoop(Project.load(repo), clock=clock)
    loop.start('b', iterations=2)
    write_data('hello world hello\n')
    assert not loop.try_change('a', 'b').stopped
    write_data('hello world\n')
    result = loop.try_change('c', 'd')
    assert result.stopped and result.budget.stop_reason is not None and result.budget.stop_reason.value == 'iterations'
    assert loop.brief().exit_code == 3


def test_plateau_stops_and_escalates(tmp_path: Path, home: Path, clock: FakeClock) -> None:
    repo = make_repo(tmp_path / 'p', patience=2)
    loop = RunLoop(Project.load(repo), clock=clock)
    loop.start('p')
    for text in ('hello world hello world hello world x\n', 'hello world hello world hello world xy\n'):
        (repo / 'src' / 'data.txt').write_text(text)
        result = loop.try_change('worse', 'plateau')
    assert result.stopped and result.budget.escalate
    assert loop.require_state().stop_reason == 'plateau'


def test_discard_restores_tree(started: RunLoop, repo: Path, write_data: Callable[[str], None]) -> None:
    write_data('junk hello\n')
    (repo / 'src' / 'new.txt').write_text('new\n')
    started.discard('changed my mind')
    assert (repo / 'src' / 'data.txt').read_text().startswith('hello world hello world hello world')
    assert not (repo / 'src' / 'new.txt').exists()


def test_resume_reverts_pending(started: RunLoop, repo: Path, write_data: Callable[[str], None]) -> None:
    from automative.state import Pending, save_state

    write_data('hello pending\n')
    started.project.git.stage(('src/data.txt',))
    sha = started.project.git.commit('automative(i1): simulated crash mid-verify')
    state = started.require_state()
    state.pending = Pending(iter=1, commit=sha, started_at=started.clock())
    state.iter = 1
    save_state(started.paths.state_file, state)
    with pytest.raises(StateError):
        started.try_change('x', 'y')
    started.resume()
    assert started.require_state().pending is None
    assert 'Revert' in git(repo, 'log', '--oneline', '-1')


def test_pause_and_resume_clock(started: RunLoop, clock: FakeClock) -> None:
    started.pause()
    assert started.brief().exit_code == 5
    clock.advance(600)
    started.resume()
    assert started.brief().minutes_used < 1


def test_end_commits_ledger(started: RunLoop, repo: Path, write_data: Callable[[str], None]) -> None:
    write_data('hello world\n')
    started.try_change('a', 'b')
    summary = started.end('done')
    assert summary.keeps == 1 and summary.best == 12.0
    assert 'automative(end)' in git(repo, 'log', '--oneline', '-1')
    assert started.require_state().status is RunStatus.DONE


def test_parse_prediction() -> None:
    assert parse_prediction('-30%', 10.0) == -3.0
    assert parse_prediction('0.5', 10.0) == 0.5
    assert parse_prediction(None, 10.0) is None
    with pytest.raises(ScopeError):
        parse_prediction('big', 10.0)

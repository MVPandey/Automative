"""Catalogue ops, curator, rejected buffer, retrieval."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from automative import strategies
from automative.decide import FailureCategory, Outcome
from automative.errors import StrategyError
from automative.ledger import GuardRecord, IterationRow, VerifyRecord
from automative.strategies import Kind, StrategyStatus
from automative.verify import GuardStatus, VerifyOutcome


def _row(decision: Outcome, category: FailureCategory, ids: tuple[str, ...], iteration: int = 1) -> IterationRow:
    return IterationRow(
        run_id='r-1',
        iter=iteration,
        ts=datetime.now(UTC),
        protocol_version='1.0.0',
        mode='improve',
        change='c',
        hypothesis='h',
        strategy_ids=ids,
        commit='a',
        parent_commit='b',
        ref='refs/x',
        verify=VerifyRecord(outcome=VerifyOutcome.OK, score=1.0, runtime_s=0.1, exit_code=0),
        guard=GuardRecord(status=GuardStatus.PASS),
        best_before=2.0,
        observed_delta=-1.0,
        decision=decision,
        decision_reason='r',
        failure_category=category,
        wall_clock_s=1.0,
    )


def test_add_and_load(tmp_path: Path) -> None:
    path = tmp_path / 's.jsonl'
    entry = strategies.add(
        path, kind=Kind.WORKS, action='use a set for membership tests', when='O(n^2) lookups', tags=('python',)
    )
    assert entry.id == 'S-0001' and entry.status is StrategyStatus.CANDIDATE
    second = strategies.add(path, kind=Kind.FAILS, action='change the comparator')
    assert second.id == 'S-0002'
    assert [s.id for s in strategies.load(path)] == ['S-0001', 'S-0002']
    assert path.read_text().count('\n') == 2  # append-only op log


def test_evidence_validates(tmp_path: Path) -> None:
    path = tmp_path / 's.jsonl'
    strategies.add(path, kind=Kind.WORKS, action='cache the parsed config')
    for i in range(3):
        strategies.record_evidence(path, _row(Outcome.KEEP, FailureCategory.NONE, ('S-0001',), i))
    entry = strategies.load(path)[0]
    assert entry.status is StrategyStatus.VALIDATED and len(entry.helped) == 3


def test_evidence_rejects(tmp_path: Path) -> None:
    path = tmp_path / 's.jsonl'
    strategies.add(path, kind=Kind.WORKS, action='inline the hot loop')
    for i in range(5):
        strategies.record_evidence(path, _row(Outcome.DISCARD, FailureCategory.WORSE, ('S-0001',), i))
    assert strategies.load(path)[0].status is StrategyStatus.REJECTED


def test_rejected_buffer_refuses_similar(tmp_path: Path) -> None:
    path = tmp_path / 's.jsonl'
    strategies.add(path, kind=Kind.WORKS, action='inline the hot loop body')
    strategies.set_status(path, 'S-0001', StrategyStatus.REJECTED, 'never helped')
    with pytest.raises(StrategyError, match='rejected S-0001'):
        strategies.add(path, kind=Kind.WORKS, action='inline the hot loop')


def test_duplicate_refused(tmp_path: Path) -> None:
    path = tmp_path / 's.jsonl'
    strategies.add(path, kind=Kind.INSIGHT, action='profile before optimizing')
    with pytest.raises(StrategyError, match='Duplicate'):
        strategies.add(path, kind=Kind.INSIGHT, action='profile before optimizing')


def test_merge_retires(tmp_path: Path) -> None:
    path = tmp_path / 's.jsonl'
    strategies.add(path, kind=Kind.WORKS, action='batch the writes')
    strategies.add(path, kind=Kind.WORKS, action='batch writes and reads together')
    strategies.merge(path, 'S-0002', 'S-0001')
    entries = {s.id: s for s in strategies.load(path)}
    assert entries['S-0001'].status is StrategyStatus.RETIRED and entries['S-0001'].superseded_by == 'S-0002'
    assert entries['S-0002'].supersedes == 'S-0001'


def test_suggest_ranks_validated_and_tags(tmp_path: Path) -> None:
    path = tmp_path / 's.jsonl'
    strategies.add(path, kind=Kind.WORKS, action='a', tags=('ml',))
    strategies.add(path, kind=Kind.WORKS, action='b', tags=('python',))
    strategies.add(path, kind=Kind.WORKS, action='c')
    strategies.set_status(path, 'S-0003', StrategyStatus.VALIDATED)
    strategies.set_status(path, 'S-0002', StrategyStatus.REJECTED)
    ranked = strategies.suggest(path, ('ml',), 5, global_path=tmp_path / 'none.jsonl')
    assert [s.id for s in ranked] == ['S-0003', 'S-0001']
    lines = strategies.suggest_lines(path, ('ml',), 1)
    assert len(lines) == 1 and lines[0].startswith('S-0003')


def test_promote_global(tmp_path: Path) -> None:
    path, global_path = tmp_path / 's.jsonl', tmp_path / 'g.jsonl'
    strategies.add(path, kind=Kind.WORKS, action='use median of three runs when noisy')
    with pytest.raises(StrategyError):
        strategies.promote_global(path, 'S-0001', global_path)
    strategies.set_status(path, 'S-0001', StrategyStatus.VALIDATED)
    promoted = strategies.promote_global(path, 'S-0001', global_path)
    assert promoted.scope == 'global' and promoted.created_by == 'promoted:S-0001'
    assert any(s.scope == 'global' for s in strategies.suggest(path, (), 5, global_path=global_path))


def test_evidence_through_runloop(started, write_data) -> None:  # type: ignore[no-untyped-def]
    path = started.paths.strategies_file
    strategies.add(path, kind=Kind.WORKS, action='shorten the data file')
    write_data('hello world\n')
    started.try_change('shorter', 'fewer bytes', strategy_ids=('S-0001',))
    assert len(strategies.load(path)[0].helped) == 1

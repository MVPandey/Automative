"""Strategy catalogue (L1): an append-only op log of reusable lessons, curated deterministically.

The JSONL file is a log of delta operations (``add``, ``evidence``, ``set_status``, ``merge``); the
catalogue is the fold of that log. Nothing is ever rewritten, so brevity bias and context collapse cannot
happen, and every status change is auditable.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from automative import ledger as ledger_io
from automative.decide import FailureCategory, Outcome
from automative.errors import StrategyError
from automative.paths import automative_home

__all__ = [
    'Kind',
    'Strategy',
    'StrategyStatus',
    'add',
    'load',
    'merge',
    'promote_global',
    'record_evidence',
    'set_status',
    'suggest',
    'suggest_lines',
]

TOKEN_RE = re.compile(r'[a-z0-9]+')
REJECT_SIMILARITY = 0.6
VALIDATE_MIN_HELPED = 3
REJECT_MIN_USED = 5


class StrategyStatus(StrEnum):
    """Curator-assigned lifecycle."""

    CANDIDATE = 'candidate'
    VALIDATED = 'validated'
    REJECTED = 'rejected'
    RETIRED = 'retired'


class Kind(StrEnum):
    """What kind of lesson this is."""

    WORKS = 'works'
    FAILS = 'fails'
    INSIGHT = 'insight'
    AVOID = 'avoid'


@dataclass(frozen=True, slots=True)
class Evidence:
    """One observation of a strategy in use."""

    run: str
    iter: int
    outcome: str
    delta: float | None


@dataclass(frozen=True, slots=True)
class Strategy:
    """Materialized catalogue entry."""

    id: str
    status: StrategyStatus
    scope: str
    kind: Kind
    tags: tuple[str, ...]
    when: str
    action: str
    expected_effect: str
    created_by: str
    created_at: str
    run: str | None
    protocol_version: str | None
    helped: tuple[Evidence, ...] = ()
    hurt: tuple[Evidence, ...] = ()
    neutral: tuple[Evidence, ...] = ()
    supersedes: str | None = None
    superseded_by: str | None = None
    status_reason: str = ''
    rev: int = 1
    history: tuple[str, ...] = field(default=())

    @property
    def used(self) -> int:
        return len(self.helped) + len(self.hurt) + len(self.neutral)

    @property
    def kept_rate(self) -> float:
        return len(self.helped) / self.used if self.used else 0.0

    def line(self) -> str:
        """One-line rendering for briefs and suggestions (<= ~60 words)."""
        stats = f'{len(self.helped)} helped/{len(self.hurt)} hurt' if self.used else 'untested'
        when = f' when {self.when}' if self.when else ''
        return f'{self.id} [{self.kind.value}, {self.status.value}, {stats}]{when}: {self.action}'


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


def _read_ops(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    ops: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            ops.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise StrategyError(f'{path} line {number} is not valid JSON: {exc}') from exc
    return ops


def _append(path: Path, op: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(op, separators=(',', ':')) + '\n')


def _fold(ops: Iterable[dict[str, Any]]) -> dict[str, Strategy]:
    catalogue: dict[str, Strategy] = {}
    for op in ops:
        kind = op.get('op')
        if kind == 'add':
            entry = Strategy(
                id=str(op['id']),
                status=StrategyStatus(op.get('status', 'candidate')),
                scope=str(op.get('scope', 'project')),
                kind=Kind(op['kind']),
                tags=tuple(op.get('tags', ())),
                when=str(op.get('when', '')),
                action=str(op['action']),
                expected_effect=str(op.get('expected_effect', '')),
                created_by=str(op.get('created_by', 'agent')),
                created_at=str(op.get('ts', '')),
                run=op.get('run'),
                protocol_version=op.get('protocol_version'),
                supersedes=op.get('supersedes'),
            )
            catalogue[entry.id] = entry
        elif kind == 'evidence' and op.get('id') in catalogue:
            entry = catalogue[str(op['id'])]
            evidence = Evidence(str(op['run']), int(op['iter']), str(op['outcome']), op.get('delta'))
            bucket = {'helped': entry.helped, 'hurt': entry.hurt, 'neutral': entry.neutral}
            key = evidence.outcome if evidence.outcome in bucket else 'neutral'
            bucket[key] = (*bucket[key], evidence)
            catalogue[entry.id] = replace(
                entry, helped=bucket['helped'], hurt=bucket['hurt'], neutral=bucket['neutral'], rev=entry.rev + 1
            )
        elif kind == 'set_status' and op.get('id') in catalogue:
            entry = catalogue[str(op['id'])]
            catalogue[entry.id] = replace(
                entry,
                status=StrategyStatus(op['status']),
                status_reason=str(op.get('reason', '')),
                rev=entry.rev + 1,
                history=(*entry.history, f'{op.get("ts", "")}: {op["status"]} ({op.get("reason", "")})'),
            )
        elif kind == 'merge' and op.get('keep') in catalogue and op.get('retire') in catalogue:
            keep_id, retire_id = str(op['keep']), str(op['retire'])
            catalogue[retire_id] = replace(
                catalogue[retire_id],
                status=StrategyStatus.RETIRED,
                superseded_by=keep_id,
                rev=catalogue[retire_id].rev + 1,
            )
            catalogue[keep_id] = replace(catalogue[keep_id], supersedes=retire_id, rev=catalogue[keep_id].rev + 1)
    return catalogue


def load(path: Path) -> tuple[Strategy, ...]:
    """Materialize the catalogue at ``path`` in id order."""
    return tuple(_fold(_read_ops(path)).values())


def _tokens(text: str) -> frozenset[str]:
    return frozenset(TOKEN_RE.findall(text.lower()))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _next_id(catalogue: dict[str, Strategy]) -> str:
    numbers = [int(s.id.split('-')[1]) for s in catalogue.values() if s.id.startswith('S-') and s.id[2:].isdigit()]
    return f'S-{(max(numbers) + 1 if numbers else 1):04d}'


def add(
    path: Path,
    *,
    kind: Kind,
    action: str,
    when: str = '',
    expected_effect: str = '',
    tags: tuple[str, ...] = (),
    created_by: str = 'agent',
    run: str | None = None,
    protocol_version: str | None = None,
    scope: str = 'project',
) -> Strategy:
    """Append an ``add`` op; refuses near-duplicates of rejected entries (the rejected buffer)."""
    action = ' '.join(action.split())
    if not action:
        raise StrategyError('A strategy needs an action')
    if len(action.split()) > 60:
        raise StrategyError('Keep the action under 60 words; put detail in notes.md')
    catalogue = _fold(_read_ops(path))
    new_tokens = _tokens(action)
    for entry in catalogue.values():
        similarity = _jaccard(new_tokens, _tokens(entry.action))
        if entry.status is StrategyStatus.REJECTED and similarity > REJECT_SIMILARITY:
            raise StrategyError(
                f'Too similar to rejected {entry.id} ({similarity:.0%}): "{entry.action}": {entry.status_reason}'
            )
        if entry.status is not StrategyStatus.RETIRED and similarity > 0.9:
            raise StrategyError(f'Duplicate of {entry.id}: "{entry.action}"')
    new_id = _next_id(catalogue)
    _append(
        path,
        {
            'op': 'add',
            'ts': _now(),
            'id': new_id,
            'kind': kind.value,
            'scope': scope,
            'tags': list(tags),
            'when': ' '.join(when.split()),
            'action': action,
            'expected_effect': ' '.join(expected_effect.split()),
            'created_by': created_by,
            'run': run,
            'protocol_version': protocol_version,
        },
    )
    return _fold(_read_ops(path))[new_id]


def set_status(path: Path, strategy_id: str, status: StrategyStatus, reason: str = '') -> Strategy:
    """Append a ``set_status`` op."""
    catalogue = _fold(_read_ops(path))
    if strategy_id not in catalogue:
        raise StrategyError(f'Unknown strategy {strategy_id}')
    _append(path, {'op': 'set_status', 'ts': _now(), 'id': strategy_id, 'status': status.value, 'reason': reason})
    return _fold(_read_ops(path))[strategy_id]


def merge(path: Path, keep_id: str, retire_id: str) -> Strategy:
    """Append a ``merge`` op: ``keep_id`` supersedes ``retire_id``."""
    catalogue = _fold(_read_ops(path))
    for sid in (keep_id, retire_id):
        if sid not in catalogue:
            raise StrategyError(f'Unknown strategy {sid}')
    _append(path, {'op': 'merge', 'ts': _now(), 'keep': keep_id, 'retire': retire_id})
    return _fold(_read_ops(path))[keep_id]


def _curate(path: Path, entry: Strategy) -> None:
    """Deterministic status transitions from evidence counts."""
    helped, hurt = len(entry.helped), len(entry.hurt)
    if entry.status is StrategyStatus.CANDIDATE and helped >= VALIDATE_MIN_HELPED and helped >= 2 * hurt:
        set_status(path, entry.id, StrategyStatus.VALIDATED, f'curator: {helped} helped vs {hurt} hurt')
    elif (
        entry.status in (StrategyStatus.CANDIDATE, StrategyStatus.VALIDATED)
        and entry.used >= REJECT_MIN_USED
        and (helped <= 1 or (hurt >= 3 and hurt > helped))
    ):
        reason = f'curator: {helped} helped vs {hurt} hurt over {entry.used} uses'
        set_status(path, entry.id, StrategyStatus.REJECTED, reason)


def record_evidence(path: Path, row: ledger_io.IterationRow) -> None:
    """Accrue helped/hurt/neutral evidence for every strategy cited on ``row``, then curate."""
    catalogue = _fold(_read_ops(path))
    if row.decision is Outcome.KEEP:
        outcome = 'helped'
    elif row.decision in (Outcome.DISCARD, Outcome.GUARD_FAIL) and row.failure_category in (
        FailureCategory.WORSE,
        FailureCategory.GUARD,
        FailureCategory.HELDOUT,
    ):
        outcome = 'hurt'
    else:
        outcome = 'neutral'
    for sid in row.strategy_ids:
        if sid not in catalogue:
            continue
        _append(
            path,
            {
                'op': 'evidence',
                'ts': _now(),
                'id': sid,
                'run': row.run_id,
                'iter': row.iter,
                'outcome': outcome,
                'delta': row.observed_delta,
            },
        )
        _curate(path, _fold(_read_ops(path))[sid])


def promote_global(project_path: Path, strategy_id: str, global_path: Path | None = None) -> Strategy:
    """Copy a validated project strategy into the global catalogue."""
    catalogue = _fold(_read_ops(project_path))
    entry = catalogue.get(strategy_id)
    if entry is None:
        raise StrategyError(f'Unknown strategy {strategy_id}')
    if entry.status is not StrategyStatus.VALIDATED:
        raise StrategyError(f'{strategy_id} is {entry.status.value}; only validated strategies go global')
    target = global_path or automative_home() / 'strategies.jsonl'
    return add(
        target,
        kind=entry.kind,
        action=entry.action,
        when=entry.when,
        expected_effect=entry.expected_effect,
        tags=entry.tags,
        created_by=f'promoted:{entry.id}',
        run=entry.run,
        protocol_version=entry.protocol_version,
        scope='global',
    )


def _score(entry: Strategy, tags: tuple[str, ...]) -> float:
    status_weight = {StrategyStatus.VALIDATED: 1.0, StrategyStatus.CANDIDATE: 0.5}.get(entry.status, 0.0)
    if status_weight == 0.0:
        return 0.0
    overlap = len(set(entry.tags) & set(tags)) * 0.2
    return status_weight + overlap + entry.kept_rate * 0.5 + (0.1 if entry.scope == 'global' else 0.0)


def suggest(path: Path, tags: tuple[str, ...], k: int, global_path: Path | None = None) -> tuple[Strategy, ...]:
    """Top-``k`` strategies for ideation, ranked by status, tag overlap, and evidence."""
    entries = list(load(path))
    global_file = global_path if global_path is not None else automative_home() / 'strategies.jsonl'
    if global_file.is_file() and global_file != path:
        entries.extend(load(global_file))
    ranked = sorted((e for e in entries if _score(e, tags) > 0), key=lambda e: (-_score(e, tags), e.id))
    return tuple(ranked[: max(0, k)])


def suggest_lines(path: Path, tags: tuple[str, ...], k: int) -> tuple[str, ...]:
    """Rendered suggestions for briefs and the Stop hook."""
    try:
        return tuple(entry.line() for entry in suggest(path, tags, k))
    except StrategyError:
        return ()

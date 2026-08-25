"""Append-only JSONL ledger of everything that happened in a run."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from automative.decide import FailureCategory, Outcome
from automative.errors import StateError
from automative.state import Best, Score
from automative.verify import GuardStatus, VerifyOutcome

__all__ = [
    'EventRow',
    'GuardRecord',
    'IterationRow',
    'Row',
    'RunEndRow',
    'RunStartRow',
    'RunSummary',
    'VerifyRecord',
    'append',
    'iterations',
    'read',
    'summarize',
    'tail',
]


class VerifyRecord(BaseModel):
    """What the verify subprocess produced."""

    model_config = ConfigDict(frozen=True)

    outcome: VerifyOutcome
    score: float | None
    samples: tuple[float, ...] = ()
    runtime_s: float
    exit_code: int | None
    log: str | None = None
    integrity: str = 'ok'


class GuardRecord(BaseModel):
    """What the guard commands produced."""

    model_config = ConfigDict(frozen=True)

    status: GuardStatus
    failed_cmd: str | None = None


class IterationRow(BaseModel):
    """One ``try``."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    type: Literal['iteration'] = 'iteration'
    run_id: str
    iter: int
    ts: datetime
    protocol_version: str
    mode: str
    change: str
    hypothesis: str
    predicted_delta: float | None = None
    strategy_ids: tuple[str, ...] = ()
    commit: str
    parent_commit: str
    ref: str
    revert_commit: str | None = None
    files_changed: tuple[str, ...] = ()
    verify: VerifyRecord
    guard: GuardRecord
    heldout_score: float | None = None
    best_before: float
    observed_delta: float | None = None
    delta_pct: float | None = None
    prediction_error_pct: float | None = None
    decision: Outcome
    decision_reason: str
    failure_category: FailureCategory
    denied_tool_calls: int = 0
    wall_clock_s: float


class EventRow(BaseModel):
    """Anything notable that is not a try: refusals, integrity events, pauses, denials, learnings."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    type: Literal['event'] = 'event'
    run_id: str
    ts: datetime
    event: str
    detail: str = ''
    data: dict[str, Any] = Field(default_factory=dict)


class RunStartRow(BaseModel):
    """Recorded once per run with every hash the run depends on."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    type: Literal['run_start'] = 'run_start'
    run_id: str
    ts: datetime
    protocol_version: str
    branch: str
    base_branch: str
    baseline_commit: str
    baseline: Score
    spec_sha: str
    protected: dict[str, str]
    metric: dict[str, Any]
    budget: dict[str, Any]
    model: str | None = None
    disclosure: dict[str, Any] = Field(default_factory=dict)


class RunEndRow(BaseModel):
    """Recorded when a run stops for any reason."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    type: Literal['run_end'] = 'run_end'
    run_id: str
    ts: datetime
    stop_reason: str
    escalated: str | None = None
    best: Best | None = None
    iterations: int
    keeps: int
    wall_clock_s: float


Row = Annotated[IterationRow | EventRow | RunStartRow | RunEndRow, Field(discriminator='type')]
_ROW_ADAPTER: TypeAdapter[IterationRow | EventRow | RunStartRow | RunEndRow] = TypeAdapter(Row)


def append(path: Path, row: IterationRow | EventRow | RunStartRow | RunEndRow) -> None:
    """Append one row; the ledger is never rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(row.model_dump(mode='json'), separators=(',', ':')) + '\n')


def read(path: Path) -> tuple[IterationRow | EventRow | RunStartRow | RunEndRow, ...]:
    """Parse every row; a malformed line is a hard error because the ledger is evidence."""
    if not path.is_file():
        return ()
    rows: list[IterationRow | EventRow | RunStartRow | RunEndRow] = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(_ROW_ADAPTER.validate_json(line))
        except (ValidationError, ValueError) as exc:
            raise StateError(f'Ledger {path} line {number} is malformed: {exc}') from exc
    return tuple(rows)


def iterations(path: Path, run_id: str | None = None) -> tuple[IterationRow, ...]:
    """Return iteration rows, optionally for one run."""
    return tuple(r for r in read(path) if isinstance(r, IterationRow) and (run_id is None or r.run_id == run_id))


def tail(path: Path, count: int, run_id: str | None = None) -> tuple[IterationRow, ...]:
    """Return the last ``count`` iteration rows."""
    rows = iterations(path, run_id)
    return rows[-count:] if count > 0 else ()


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Aggregate statistics over one run's iteration rows."""

    run_id: str
    iterations: int
    keeps: int
    discards: int
    errors: int
    baseline: float | None
    best: float | None
    best_iter: int | None
    keep_rate: float
    calibration_mae_pct: float | None
    failure_histogram: tuple[tuple[str, int], ...]

    @property
    def improvement_pct(self) -> float | None:
        if self.baseline in (None, 0) or self.best is None:
            return None
        assert self.baseline is not None
        return (self.best - self.baseline) / abs(self.baseline) * 100.0


def summarize(rows: Sequence[IterationRow | EventRow | RunStartRow | RunEndRow], run_id: str) -> RunSummary:
    """Fold ledger rows for ``run_id`` into a :class:`RunSummary`."""
    baseline: float | None = None
    for row in rows:
        if isinstance(row, RunStartRow) and row.run_id == run_id:
            baseline = row.baseline.score
    iters = [r for r in rows if isinstance(r, IterationRow) and r.run_id == run_id]
    keeps = [r for r in iters if r.decision is Outcome.KEEP]
    errors = [r for r in iters if r.decision in (Outcome.CRASH, Outcome.TIMEOUT, Outcome.METRIC_ERROR)]
    discards = [r for r in iters if r.decision in (Outcome.DISCARD, Outcome.GUARD_FAIL)]
    best_row = keeps[-1] if keeps else None
    hist: dict[str, int] = {}
    for row in iters:
        if row.failure_category is not FailureCategory.NONE:
            hist[row.failure_category.value] = hist.get(row.failure_category.value, 0) + 1
    calibrated = [abs(r.prediction_error_pct) for r in iters if r.prediction_error_pct is not None]
    measured = len(keeps) + len(discards)
    return RunSummary(
        run_id=run_id,
        iterations=len(iters),
        keeps=len(keeps),
        discards=len(discards),
        errors=len(errors),
        baseline=baseline,
        best=best_row.verify.score if best_row else baseline,
        best_iter=best_row.iter if best_row else None,
        keep_rate=(len(keeps) / measured) if measured else 0.0,
        calibration_mae_pct=(sum(calibrated) / len(calibrated)) if calibrated else None,
        failure_histogram=tuple(sorted(hist.items(), key=lambda kv: (-kv[1], kv[0]))),
    )

"""The sealed record of held-out scores.

Held-out numbers never appear where the agent can see them: not in the ledger, not in the try verdict,
not in the iteration logs. They live here, in ``.automative/heldout/``, which the hooks refuse to read
during a run, so the held-out window stays what it is meant to be: a check the agent cannot select on.
Humans read it with ``automative report --heldout`` after the run.
"""

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from automative.errors import StateError
from automative.verify import VerifyOutcome

__all__ = ['HeldoutRecord', 'append', 'best_kept', 'read']


class HeldoutRecord(BaseModel):
    """One held-out measurement, taken when a try improved the training metric."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    run_id: str
    iter: int
    ts: datetime
    outcome: VerifyOutcome
    score: float | None
    not_worse: bool
    log: str | None = None


def append(path: Path, record: HeldoutRecord) -> None:
    """Append one record; the file is never rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(record.model_dump(mode='json'), separators=(',', ':')) + '\n')


def read(path: Path, run_id: str | None = None) -> tuple[HeldoutRecord, ...]:
    """Every record, optionally for one run."""
    if not path.is_file():
        return ()
    rows: list[HeldoutRecord] = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = HeldoutRecord.model_validate_json(line)
        except (ValidationError, ValueError) as exc:
            raise StateError(f'Held-out record {path} line {number} is malformed: {exc}') from exc
        if run_id is None or row.run_id == run_id:
            rows.append(row)
    return tuple(rows)


def best_kept(path: Path, run_id: str, kept_iters: frozenset[int]) -> float | None:
    """The held-out score of the most recent kept try, or ``None`` before the first one."""
    scores = [r.score for r in read(path, run_id) if r.iter in kept_iters and r.score is not None]
    return scores[-1] if scores else None

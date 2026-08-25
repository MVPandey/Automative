"""Mutable run state persisted as ``.automative/state.json``.

:class:`RunState` is the one deliberately mutable object in the package: it is owned exclusively by the CLI,
written atomically, and read by hooks. Everything else derives immutable values from it.
"""

import contextlib
import fcntl
import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from automative.errors import StateError

__all__ = [
    'Best',
    'Mode',
    'Pending',
    'RunState',
    'RunStatus',
    'Score',
    'StopHookState',
    'load_state',
    'new_run_id',
    'now',
    'run_lock',
    'save_state',
]


class RunStatus(StrEnum):
    """Lifecycle of a run."""

    ACTIVE = 'active'
    PAUSED = 'paused'
    DONE = 'done'


class Mode(StrEnum):
    """What kind of loop the agent is currently driving."""

    RUN = 'run'
    EVOLVE = 'evolve'


class Score(BaseModel):
    """A measured score and the raw samples that produced it."""

    model_config = ConfigDict(frozen=True)

    score: float
    samples: tuple[float, ...] = ()


class Best(BaseModel):
    """The incumbent best result."""

    model_config = ConfigDict(frozen=True)

    score: float
    iter: int
    commit: str


class Pending(BaseModel):
    """A ``try`` that committed but has not finished verifying (crash-recovery marker)."""

    model_config = ConfigDict(frozen=True)

    iter: int
    commit: str
    started_at: datetime


class StopHookState(BaseModel):
    """Counters the Stop hook uses to detect an agent spinning without iterating."""

    blocks_since_last_try: int = 0


class RunState(BaseModel):
    """Everything the CLI needs to resume a run from disk.

    Invariants: ``iter`` counts completed tries; ``best`` is never worse than ``baseline``; ``pending`` is
    set only between the commit and the decision of a single ``try``.
    """

    model_config = ConfigDict(extra='forbid', validate_assignment=True)

    schema_version: int = 1
    run_id: str
    status: RunStatus = RunStatus.ACTIVE
    stop_reason: str | None = None
    mode: Mode = Mode.RUN
    protocol: str
    branch: str
    base_branch: str
    baseline_commit: str
    head: str
    baseline: Score | None = None
    best: Best | None = None
    iter: int = 0
    tries_since_best: int = 0
    consecutive_errors: int = 0
    denied_tool_calls: int = 0
    started_at: datetime
    updated_at: datetime
    wall_clock_s: float = 0.0
    paused_at: datetime | None = None
    paused_total_s: float = 0.0
    protected: dict[str, str] = Field(default_factory=dict)
    spec_sha: str
    pending: Pending | None = None
    stop_hook: StopHookState = Field(default_factory=StopHookState)
    candidate_version: str | None = None
    escalated: str | None = None
    bench_task: str | None = None
    seed: int | None = None
    session_id: str | None = None
    model: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status is RunStatus.ACTIVE


def now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def new_run_id(slug: str, at: datetime | None = None) -> str:
    """Build a run id like ``r-20260825-1432-sortbench``."""
    stamp = (at or now()).strftime('%Y%m%d-%H%M')
    clean = re.sub(r'[^a-z0-9]+', '-', slug.lower()).strip('-') or 'run'
    return f'r-{stamp}-{clean[:24]}'


def load_state(path: Path) -> RunState | None:
    """Load state from ``path``; return ``None`` when no run exists."""
    if not path.is_file():
        return None
    try:
        return RunState.model_validate_json(path.read_text(encoding='utf-8'))
    except (OSError, ValidationError, ValueError) as exc:
        raise StateError(f'Corrupt run state at {path}: {exc}') from exc


def save_state(path: Path, state: RunState) -> None:
    """Atomically write ``state`` to ``path``."""
    state.updated_at = now()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f'.tmp.{os.getpid()}')
    try:
        tmp.write_text(json.dumps(state.model_dump(mode='json'), indent=2) + '\n', encoding='utf-8')
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise StateError(f'Cannot write run state to {path}: {exc}') from exc


@contextlib.contextmanager
def run_lock(dotdir: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock so two CLI invocations cannot mutate a run concurrently."""
    dotdir.mkdir(parents=True, exist_ok=True)
    lock_path = dotdir / '.cli.lock'
    with lock_path.open('a+', encoding='utf-8') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StateError('Another automative command is running in this project') from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

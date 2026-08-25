"""Replay check for the ledger's ``shown`` rows.

The invariant: every score, brief, verdict, or refusal the agent could act on was written to the ledger
at the moment it was shown, and every try names the view it was made against. ``audit`` walks one run's
rows in order and reports where that chain is broken.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from automative.ledger import EventRow, IterationRow, Row, ShownRow, text_sha

__all__ = ['AuditResult', 'audit']


@dataclass(frozen=True, slots=True)
class AuditResult:
    """Outcome of replaying one run's shown rows against its iterations."""

    run_id: str
    shown: int
    by_surface: tuple[tuple[str, int], ...]
    iterations: int
    legacy_iterations: int
    unlogged_iterations: tuple[int, ...]
    corrupt_shown: tuple[int, ...]
    stale_refusals: int

    @property
    def ok(self) -> bool:
        return not self.unlogged_iterations and not self.corrupt_shown


def audit(rows: Sequence[Row], run_id: str) -> AuditResult:
    """Check that each try cites a view that was shown before it, and that stored text matches its hash."""
    seen: set[str] = set()
    surfaces: dict[str, int] = {}
    shown = 0
    iterations = 0
    legacy = 0
    unlogged: list[int] = []
    corrupt: list[int] = []
    stale = 0
    for index, row in enumerate(rows, start=1):
        if row.run_id != run_id:
            continue
        if isinstance(row, ShownRow):
            shown += 1
            surfaces[row.surface] = surfaces.get(row.surface, 0) + 1
            seen.add(row.context_sha)
            if row.text is not None and text_sha(row.text) != row.sha256:
                corrupt.append(index)
        elif isinstance(row, IterationRow):
            iterations += 1
            if not row.context_sha:
                legacy += 1
            elif row.context_sha not in seen:
                unlogged.append(row.iter)
        elif isinstance(row, EventRow) and row.event == 'stale_context':
            stale += 1
    return AuditResult(
        run_id=run_id,
        shown=shown,
        by_surface=tuple(sorted(surfaces.items())),
        iterations=iterations,
        legacy_iterations=legacy,
        unlogged_iterations=tuple(unlogged),
        corrupt_shown=tuple(corrupt),
        stale_refusals=stale,
    )

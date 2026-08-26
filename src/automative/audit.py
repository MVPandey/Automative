"""Replay check for the ledger's ``shown`` rows.

The invariant: every score, brief, verdict, or refusal the agent could act on was written to the ledger
at the moment it was shown, and every try names the view it was made against. ``audit`` walks one run's
rows in order and reports where that chain is broken.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from automative.ledger import EventRow, IterationRow, Row, ShownRow, text_sha
from automative.trace import TraceRow

__all__ = ['AuditResult', 'audit', 'audit_trace']


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
    tool_calls: int = 0
    denials: int = 0
    trace_findings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.unlogged_iterations and not self.corrupt_shown and not self.trace_findings


def audit_trace(
    rows: Sequence[TraceRow],
    *,
    sealed: Sequence[str],
    heldout_cmd: str | None,
    is_sealed: Callable[[str, tuple[str, ...]], bool],
) -> tuple[int, int, tuple[str, ...]]:
    """Deterministic checks over the tool trace: sealed paths touched, held-out run out of band, privilege."""
    findings: list[str] = []
    denials = 0
    for index, row in enumerate(rows, start=1):
        if row.denied:
            denials += 1
            continue
        text = ' '.join(str(v) for v in row.input.values())
        if heldout_cmd and heldout_cmd in text:
            findings.append(f'trace #{index}: {row.tool} ran the held-out command out of band')
        if re.search(r'(^|[\s;&|(`])(sudo|doas|su)\b', text):
            findings.append(f'trace #{index}: {row.tool} used sudo/doas/su')
        if sealed:
            for token in re.split(r'[\s"\'`;|&()<>=]+', text):
                if token and not token.startswith('-') and is_sealed(token, tuple(sealed)):
                    findings.append(f'trace #{index}: {row.tool} touched sealed path {token}')
                    break
    return len(rows), denials, tuple(findings)


def audit(
    rows: Sequence[Row],
    run_id: str,
    *,
    trace: Sequence[TraceRow] = (),
    sealed: Sequence[str] = (),
    heldout_cmd: str | None = None,
    is_sealed: Callable[[str, tuple[str, ...]], bool] | None = None,
) -> AuditResult:
    """Check that each try cites a view that was shown before it, that stored text matches its hash, and that
    the tool trace holds no out-of-band held-out runs, sealed-path reads, or privilege escalation."""
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
    calls, denials = 0, 0
    findings: tuple[str, ...] = ()
    if trace:
        calls, denials, findings = audit_trace(
            trace, sealed=sealed, heldout_cmd=heldout_cmd, is_sealed=is_sealed or (lambda _p, _s: False)
        )
    return AuditResult(
        run_id=run_id,
        shown=shown,
        by_surface=tuple(sorted(surfaces.items())),
        iterations=iterations,
        legacy_iterations=legacy,
        unlogged_iterations=tuple(unlogged),
        corrupt_shown=tuple(corrupt),
        stale_refusals=stale,
        tool_calls=calls,
        denials=denials,
        trace_findings=findings,
    )

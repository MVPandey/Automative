"""Human-readable renderings of harness state. Pure functions returning text; only the CLI prints."""

from collections.abc import Sequence

from automative.audit import AuditResult
from automative.disclosure import disclosure_card
from automative.heldout import HeldoutRecord
from automative.ledger import IterationRow, RunSummary
from automative.views import Brief, TryOutcome, fmt, pct, render_brief, render_try

__all__ = [
    'Brief',
    'TryOutcome',
    'disclosure_card',
    'render_audit',
    'render_brief',
    'render_compact',
    'render_heldout',
    'render_ledger',
    'render_summary',
    'render_tree',
    'render_try',
]

_fmt = fmt
_pct = pct


def render_summary(summary: RunSummary) -> str:
    """End-of-run summary."""
    lines = [
        f'Run {summary.run_id}: {summary.iterations} tries, {summary.keeps} kept, {summary.discards} discarded, '
        f'{summary.errors} errors, keep rate {summary.keep_rate:.0%}',
        f'Baseline {_fmt(summary.baseline)} to best {_fmt(summary.best)}{_pct(summary.improvement_pct)}'
        + (f' at i{summary.best_iter}' if summary.best_iter else ''),
    ]
    if summary.calibration_mae_pct is not None:
        lines.append(f'Prediction calibration: mean |error| {summary.calibration_mae_pct:.1f}% of incumbent')
    if summary.failure_histogram:
        lines.append('Failures: ' + ', '.join(f'{k} x{v}' for k, v in summary.failure_histogram))
    return '\n'.join(lines)


def render_ledger(rows: Sequence[IterationRow]) -> str:
    """Tabular ledger view."""
    if not rows:
        return '(no iterations yet)'
    out = ['iter  decision      score        delta      change']
    for row in rows:
        out.append(
            f'{row.iter:<5} {row.decision.value:<13} {_fmt(row.verify.score):<12} '
            f'{_fmt(row.observed_delta):<10} {row.change[:60]}'
        )
    return '\n'.join(out)


def render_compact(rows: Sequence[IterationRow]) -> str:
    """Compact per-iteration lines for the reflector (~40 tokens each)."""
    out: list[str] = []
    for row in rows:
        pred = f' pred {row.predicted_delta:+g}' if row.predicted_delta is not None else ''
        ids = f' [{",".join(row.strategy_ids)}]' if row.strategy_ids else ''
        fail = f' fail={row.failure_category.value}' if row.failure_category.value != 'none' else ''
        delta = f'{row.observed_delta:+g}' if row.observed_delta is not None else '-'
        out.append(
            f'i{row.iter} {row.decision.value} delta {delta}{pred}{ids}{fail} | {row.change} | why: {row.hypothesis}'
        )
    return '\n'.join(out)


def render_tree(rows: Sequence[IterationRow], baseline: float | None) -> str:
    """The attempt tree: every try under the attempt it was built from (``parent_iter``)."""
    children: dict[int, list[IterationRow]] = {}
    for row in rows:
        parent = row.parent_iter if row.parent_iter is not None else 0
        children.setdefault(parent, []).append(row)
    lines = [f'i0 baseline {_fmt(baseline)}']

    def walk(node: int, depth: int) -> None:
        for row in children.get(node, ()):
            score = f'{_fmt(row.verify.score)}{_pct(row.delta_pct)}'
            lines.append(f'{"  " * depth}i{row.iter} {row.decision.value:<12} {score}  {row.change[:60]}')
            walk(row.iter, depth + 1)

    walk(0, 1)
    return '\n'.join(lines)


def render_audit(result: AuditResult) -> str:
    """Plain-text audit verdict."""
    lines = [
        f'Run {result.run_id}: {result.shown} shown rows, {result.iterations} tries'
        + (f' ({result.legacy_iterations} from before context logging)' if result.legacy_iterations else ''),
    ]
    if result.by_surface:
        lines.append('Surfaces: ' + ', '.join(f'{k} x{v}' for k, v in result.by_surface))
    if result.stale_refusals:
        lines.append(f'Stale-context refusals: {result.stale_refusals}')
    if result.unlogged_iterations:
        lines.append(
            'Tries made against a view that was never shown: ' + ', '.join(f'i{i}' for i in result.unlogged_iterations)
        )
    if result.corrupt_shown:
        lines.append(
            'Shown rows whose text does not match its hash (ledger lines): ' + ', '.join(map(str, result.corrupt_shown))
        )
    lines.append('OK: every try cites a view the agent was shown.' if result.ok else 'FAILED')
    return '\n'.join(lines)


def render_heldout(records: Sequence[HeldoutRecord], kept: set[int]) -> str:
    """The sealed held-out history, for a human after the run."""
    if not records:
        return '(no held-out measurements: none configured, or no try improved the training metric)'
    lines = ['iter  kept  held-out   verdict']
    for r in records:
        score = '-' if r.score is None else f'{r.score:g}'
        verdict = 'pass' if r.not_worse else 'fail'
        if r.outcome.value != 'ok':
            verdict = r.outcome.value
        lines.append(f'{r.iter:<5} {"yes" if r.iter in kept else "no":<5} {score:<10} {verdict}')
    return '\n'.join(lines)

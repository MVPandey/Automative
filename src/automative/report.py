"""Human-readable renderings of harness state. Pure functions returning text; only the CLI prints."""

from collections.abc import Sequence

from automative.decide import Outcome
from automative.disclosure import disclosure_card
from automative.ledger import IterationRow, RunSummary
from automative.runloop import Brief, TryOutcome

__all__ = ['disclosure_card', 'render_brief', 'render_compact', 'render_ledger', 'render_summary', 'render_try']


def _fmt(value: float | None) -> str:
    return '-' if value is None else f'{value:g}'


def _pct(value: float | None) -> str:
    return '' if value is None else f' ({value:+.1f}%)'


def render_brief(brief: Brief) -> str:
    """The <=15-line recitation block."""
    if not brief.exists:
        return '\n'.join(
            [
                'AUTOMATIVE: no run in this project',
                f'Goal: {brief.goal}',
                f'Metric: {brief.metric_name} ({brief.direction} is better) via `{brief.verify_cmd}`',
                f'Scope: {", ".join(brief.scope)}',
                f'Protocol: {brief.protocol_version} at {brief.protocol_path or "(not installed)"}',
                'Next: `automative run start` (after committing AUTOMATIVE.md), then follow the protocol.',
            ]
        )
    lines = [
        f'AUTOMATIVE run {brief.run_id}: {brief.status.upper()}'
        + (f' ({brief.stop_reason})' if brief.stop_reason else ''),
        f'Goal: {brief.goal}',
        f'Metric: {brief.metric_name} ({brief.direction} is better), baseline {_fmt(brief.baseline)}, best '
        f'{_fmt(brief.best)} (i{brief.best_iter})',
        f'Budget: iter {brief.iteration}/{brief.iterations_budget or "inf"}, {brief.minutes_used:.0f}/'
        f'{brief.minutes_budget or "inf"} min, {brief.tries_since_best}/{brief.plateau_patience or "inf"} since best',
        f'Scope: {", ".join(brief.scope)}. Protocol {brief.protocol_version}: {brief.protocol_path or "(missing)"}',
    ]
    if brief.recent:
        lines.append('Recent:')
        for row in brief.recent:
            score = f'{_fmt(row.verify.score)}{_pct(row.delta_pct)}'
            lines.append(f'  i{row.iter} {row.decision.value:<12} {score}  {row.change[:70]}')
    if brief.strategies:
        lines.append('Strategies: ' + ' | '.join(brief.strategies[:3]))
    if brief.pending:
        lines.append('! A try is pending (crashed mid-verify): run `automative run resume`.')
    if brief.escalated:
        lines.append(f'! Escalated to human: {brief.escalated}')
    if brief.status == 'active':
        lines.append('Next: one change inside scope, then `automative try -m "..." --hypothesis "..."`.')
    return '\n'.join(lines)


def render_try(outcome: TryOutcome) -> str:
    """The decision block the agent reads after ``try``."""
    row = outcome.row
    verdict = row.decision.value.upper()
    head = f'i{row.iter} {verdict}: {row.decision_reason}'
    detail = (
        f'score {_fmt(row.verify.score)} vs best-before {_fmt(row.best_before)}'
        f'{_pct(row.delta_pct)}, guard {row.guard.status.value}, verify {row.verify.runtime_s:.1f}s'
    )
    lines = [head, detail]
    if row.decision is Outcome.KEEP:
        lines.append(f'Kept commit {row.commit}; new best {_fmt(outcome.best)}.')
    elif row.revert_commit:
        lines.append(f'Reverted ({row.revert_commit}); tree is back at best {_fmt(outcome.best)}.')
    if row.decision in (Outcome.CRASH, Outcome.TIMEOUT, Outcome.METRIC_ERROR) and row.verify.log:
        lines.append(f'Log: {row.verify.log} (tail it; fix only if trivial, max 2 repairs per idea)')
    if row.guard.failed_cmd:
        lines.append(f'Guard failed: `{row.guard.failed_cmd}`. See {row.verify.log}')
    if outcome.stopped:
        reason = outcome.budget.stop_reason.value if outcome.budget.stop_reason else 'stopped'
        lines.append(f'RUN STOPPED ({reason}): {outcome.budget.message}. Run `automative run end` for the summary.')
    else:
        lines.append(f'Budget: {outcome.budget.message}. Next: one change, then `automative try`.')
    return '\n'.join(lines)


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

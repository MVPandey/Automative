"""What the agent sees: the brief and the try verdict, as data and as text.

These renderings are model-visible, so the run loop records each one in the ledger as a ``shown`` row at
the moment it is produced. Keeping them here (below :mod:`automative.runloop`) lets the loop render and
record in one step instead of trusting the CLI to do it afterwards.
"""

from dataclasses import dataclass

from automative import budget as budget_rules
from automative.decide import Decision, Outcome
from automative.ledger import IterationRow

__all__ = ['Brief', 'TryOutcome', 'fmt', 'pct', 'render_brief', 'render_try']


def fmt(value: float | None) -> str:
    """Render a score, or ``-`` when there is none."""
    return '-' if value is None else f'{value:g}'


def pct(value: float | None) -> str:
    """Render a signed percentage in parentheses, or nothing."""
    return '' if value is None else f' ({value:+.1f}%)'


@dataclass(frozen=True, slots=True)
class TryOutcome:
    """What ``try`` returned to the agent, including the exact text it printed."""

    row: IterationRow
    decision: Decision
    budget: budget_rules.BudgetStatus
    best: float
    stopped: bool
    text: str = ''


@dataclass(frozen=True)
class Brief:
    """The recitation the agent reads at the top of every iteration."""

    exists: bool
    status: str
    run_id: str | None
    goal: str
    metric_name: str
    direction: str
    verify_cmd: str
    baseline: float | None
    best: float | None
    best_iter: int | None
    iteration: int
    iterations_budget: int
    minutes_used: float
    minutes_budget: int
    tries_since_best: int
    plateau_patience: int
    recent: tuple[IterationRow, ...]
    strategies: tuple[str, ...]
    protocol_version: str
    protocol_path: str | None
    stop_reason: str | None
    escalated: str | None
    pending: bool
    scope: tuple[str, ...]
    checked_out: int | None = None

    @property
    def exit_code(self) -> int:
        if not self.exists:
            return 4
        return {'active': 0, 'done': 3, 'paused': 5}.get(self.status, 0)


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
        f'Metric: {brief.metric_name} ({brief.direction} is better), baseline {fmt(brief.baseline)}, best '
        f'{fmt(brief.best)} (i{brief.best_iter})',
        f'Budget: iter {brief.iteration}/{brief.iterations_budget or "inf"}, {brief.minutes_used:.0f}/'
        f'{brief.minutes_budget or "inf"} min, {brief.tries_since_best}/{brief.plateau_patience or "inf"} since best',
        f'Scope: {", ".join(brief.scope)}. Protocol {brief.protocol_version}: {brief.protocol_path or "(missing)"}',
    ]
    if brief.recent:
        lines.append('Recent:')
        for row in brief.recent:
            score = f'{fmt(row.verify.score)}{pct(row.delta_pct)}'
            lines.append(f'  i{row.iter} {row.decision.value:<12} {score}  {row.change[:70]}')
    if brief.strategies:
        lines.append('Strategies: ' + ' | '.join(brief.strategies[:3]))
    if brief.checked_out is not None:
        lines.append(
            f'Working from attempt i{brief.checked_out}: the next try builds on it, not on best i{brief.best_iter}.'
        )
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
    if row.parent_iter is not None:
        head += f' (from i{row.parent_iter})'
    detail = (
        f'score {fmt(row.verify.score)} vs best-before {fmt(row.best_before)}'
        f'{pct(row.delta_pct)}, guard {row.guard.status.value}, verify {row.verify.runtime_s:.1f}s'
    )
    lines = [head, detail]
    if row.decision is Outcome.KEEP:
        lines.append(f'Kept commit {row.commit}; new best {fmt(outcome.best)}.')
    elif row.revert_commit:
        lines.append(f'Reverted ({row.revert_commit}); tree is back at best {fmt(outcome.best)}.')
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

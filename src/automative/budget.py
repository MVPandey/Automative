"""Stop conditions: budgets, plateau, error streaks, and integrity-driven halts."""

from dataclasses import dataclass
from enum import StrEnum

from automative.spec import BudgetSpec, Direction, MetricSpec
from automative.state import RunState

__all__ = ['BudgetStatus', 'StopReason', 'evaluate', 'target_reached']


class StopReason(StrEnum):
    """Why a run stopped."""

    ITERATIONS = 'iterations'
    WALL_CLOCK = 'wall_clock'
    PLATEAU = 'plateau'
    TARGET = 'target'
    CONSECUTIVE_ERRORS = 'consecutive_errors'
    DENIED_TOOL_CALLS = 'denied_tool_calls'
    INTEGRITY = 'integrity'
    CONFIG_DRIFT = 'config_drift'
    HOOKS_DEAD = 'hooks_dead'
    STALLED = 'stalled'
    ENDED = 'ended'


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    """Whether the run must stop, and whether a human should be pulled in."""

    stop_reason: StopReason | None
    escalate: bool
    message: str

    @property
    def should_stop(self) -> bool:
        return self.stop_reason is not None


def target_reached(best: float | None, metric: MetricSpec) -> bool:
    """Return whether the incumbent meets the optional target."""
    if best is None or metric.target is None:
        return False
    return best <= metric.target if metric.direction is Direction.LOWER else best >= metric.target


def evaluate(state: RunState, budget: BudgetSpec, metric: MetricSpec) -> BudgetStatus:
    """Compute the stop status for the current state; the first matching rule wins."""
    if state.escalated:
        reason = StopReason(state.stop_reason) if state.stop_reason else StopReason.INTEGRITY
        return BudgetStatus(reason, True, state.escalated)
    best = state.best.score if state.best else None
    if target_reached(best, metric):
        return BudgetStatus(StopReason.TARGET, False, f'target {metric.target:g} reached with {best:g}')
    if state.consecutive_errors >= budget.max_consecutive_errors:
        return BudgetStatus(
            StopReason.CONSECUTIVE_ERRORS, True, f'{state.consecutive_errors} consecutive crashes/metric errors'
        )
    if state.denied_tool_calls >= budget.max_denied_tool_calls:
        return BudgetStatus(StopReason.DENIED_TOOL_CALLS, True, f'{state.denied_tool_calls} denied tool calls')
    if budget.iterations and state.iter >= budget.iterations:
        return BudgetStatus(StopReason.ITERATIONS, False, f'iteration budget {budget.iterations} exhausted')
    if budget.minutes and state.wall_clock_s >= budget.minutes * 60:
        return BudgetStatus(StopReason.WALL_CLOCK, False, f'wall-clock budget {budget.minutes} min exhausted')
    if budget.plateau_patience and state.tries_since_best >= budget.plateau_patience:
        return BudgetStatus(
            StopReason.PLATEAU, True, f'no new best in {state.tries_since_best} measured tries (plateau)'
        )
    return BudgetStatus(None, False, 'within budget')

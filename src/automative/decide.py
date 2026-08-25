"""The keep/discard decision, kept pure so it can be tested exhaustively and swapped for other policies."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from automative.spec import Direction, Threshold
from automative.verify import GuardStatus, VerifyOutcome

__all__ = ['Decision', 'FailureCategory', 'GreedyPolicy', 'Outcome', 'Policy', 'decide', 'is_improvement']


class Outcome(StrEnum):
    """Final status of one try, as recorded in the ledger."""

    KEEP = 'keep'
    DISCARD = 'discard'
    GUARD_FAIL = 'guard_fail'
    CRASH = 'crash'
    TIMEOUT = 'timeout'
    METRIC_ERROR = 'metric_error'
    NO_OP = 'no_op'
    ABANDONED = 'abandoned'
    TAMPER = 'tamper'


class FailureCategory(StrEnum):
    """Why a try did not become a keep (Harness-Bench-style taxonomy plus harness-specific causes)."""

    NONE = 'none'
    WORSE = 'worse'
    BELOW_MIN = 'below_min'
    GUARD = 'guard'
    HELDOUT = 'heldout'
    CRASH = 'crash'
    TIMEOUT = 'timeout'
    METRIC_PARSE = 'metric_parse'
    SCOPE = 'scope'
    INTEGRITY = 'integrity'
    CONTRACT_FORMAT = 'contract_format'
    TOOL_RECOVERY = 'tool_recovery'
    STATE_CONTINUATION = 'state_continuation'


@dataclass(frozen=True, slots=True)
class Decision:
    """Result of judging a try against the incumbent."""

    outcome: Outcome
    reason: str
    delta: float | None
    delta_pct: float | None
    failure_category: FailureCategory

    @property
    def kept(self) -> bool:
        return self.outcome is Outcome.KEEP


def is_improvement(score: float, best: float, direction: Direction, threshold: Threshold) -> bool:
    """Return whether ``score`` beats ``best`` by at least the threshold in the required direction."""
    required = threshold.amount(best)
    if direction is Direction.LOWER:
        return score < best - required
    return score > best + required


def _deltas(score: float, best: float, direction: Direction) -> tuple[float, float | None]:
    delta = score - best
    pct = None if best == 0 else delta / abs(best) * 100.0
    if direction is Direction.LOWER:
        return delta, pct
    return delta, pct


def decide(
    *,
    verify_outcome: VerifyOutcome,
    score: float | None,
    best: float,
    direction: Direction,
    threshold: Threshold,
    guard: GuardStatus,
    heldout_ok: bool = True,
) -> Decision:
    """Judge one try. Errors and guard failures always lose; ties and sub-threshold gains are discards."""
    if verify_outcome is VerifyOutcome.TIMEOUT:
        return Decision(Outcome.TIMEOUT, 'verify timed out', None, None, FailureCategory.TIMEOUT)
    if verify_outcome is VerifyOutcome.CRASH:
        return Decision(Outcome.CRASH, 'verify exited non-zero', None, None, FailureCategory.CRASH)
    if verify_outcome is VerifyOutcome.METRIC_ERROR or score is None:
        return Decision(
            Outcome.METRIC_ERROR, 'last stdout line was not a bare number', None, None, FailureCategory.METRIC_PARSE
        )
    delta, pct = _deltas(score, best, direction)
    improved = is_improvement(score, best, direction, threshold)
    if guard is GuardStatus.FAIL:
        return Decision(Outcome.GUARD_FAIL, 'guard command failed', delta, pct, FailureCategory.GUARD)
    if not improved:
        moved_right_way = (score < best) if direction is Direction.LOWER else (score > best)
        if score == best:
            return Decision(Outcome.DISCARD, 'no change in metric', delta, pct, FailureCategory.WORSE)
        if moved_right_way:
            return Decision(
                Outcome.DISCARD,
                f'improvement below min_improvement ({threshold.value:g} {threshold.kind})',
                delta,
                pct,
                FailureCategory.BELOW_MIN,
            )
        return Decision(Outcome.DISCARD, 'metric got worse', delta, pct, FailureCategory.WORSE)
    if not heldout_ok:
        return Decision(Outcome.DISCARD, 'held-out metric regressed', delta, pct, FailureCategory.HELDOUT)
    return Decision(Outcome.KEEP, f'improved by {abs(delta):g}', delta, pct, FailureCategory.NONE)


class Policy(Protocol):
    """Selection policy seam; the default is greedy hill-climbing against a single incumbent."""

    def judge(
        self,
        *,
        verify_outcome: VerifyOutcome,
        score: float | None,
        best: float,
        direction: Direction,
        threshold: Threshold,
        guard: GuardStatus,
        heldout_ok: bool,
    ) -> Decision: ...


class GreedyPolicy:
    """Keep iff the score beats the single incumbent by the threshold."""

    def judge(
        self,
        *,
        verify_outcome: VerifyOutcome,
        score: float | None,
        best: float,
        direction: Direction,
        threshold: Threshold,
        guard: GuardStatus,
        heldout_ok: bool,
    ) -> Decision:
        return decide(
            verify_outcome=verify_outcome,
            score=score,
            best=best,
            direction=direction,
            threshold=threshold,
            guard=guard,
            heldout_ok=heldout_ok,
        )

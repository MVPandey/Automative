"""Exhaustive table for the keep/discard decision."""

import pytest

from automative.decide import FailureCategory, Outcome, decide, is_improvement
from automative.spec import Direction, Threshold
from automative.verify import GuardStatus, VerifyOutcome


@pytest.mark.parametrize(
    ('score', 'best', 'direction', 'threshold', 'expected'),
    [
        (9.0, 10.0, Direction.LOWER, Threshold('abs', 0.0), True),
        (10.0, 10.0, Direction.LOWER, Threshold('abs', 0.0), False),
        (11.0, 10.0, Direction.LOWER, Threshold('abs', 0.0), False),
        (9.9, 10.0, Direction.LOWER, Threshold('pct', 0.02), False),
        (9.7, 10.0, Direction.LOWER, Threshold('pct', 0.02), True),
        (11.0, 10.0, Direction.HIGHER, Threshold('abs', 0.5), True),
        (10.4, 10.0, Direction.HIGHER, Threshold('abs', 0.5), False),
        (-1.0, -2.0, Direction.HIGHER, Threshold('pct', 0.1), True),
    ],
)
def test_is_improvement(score: float, best: float, direction: Direction, threshold: Threshold, expected: bool) -> None:
    assert is_improvement(score, best, direction, threshold) is expected


def _decide(**overrides: object) -> object:
    kwargs: dict[str, object] = {
        'verify_outcome': VerifyOutcome.OK,
        'score': 9.0,
        'best': 10.0,
        'direction': Direction.LOWER,
        'threshold': Threshold('abs', 0.0),
        'guard': GuardStatus.PASS,
        'heldout_ok': True,
    }
    kwargs.update(overrides)
    return decide(**kwargs)  # type: ignore[arg-type]


def test_keep() -> None:
    result = _decide()
    assert result.outcome is Outcome.KEEP  # type: ignore[attr-defined]
    assert result.failure_category is FailureCategory.NONE  # type: ignore[attr-defined]
    assert result.delta == -1.0  # type: ignore[attr-defined]
    assert result.delta_pct == -10.0  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ('overrides', 'outcome', 'category'),
    [
        ({'verify_outcome': VerifyOutcome.TIMEOUT, 'score': None}, Outcome.TIMEOUT, FailureCategory.TIMEOUT),
        ({'verify_outcome': VerifyOutcome.CRASH, 'score': None}, Outcome.CRASH, FailureCategory.CRASH),
        (
            {'verify_outcome': VerifyOutcome.METRIC_ERROR, 'score': None},
            Outcome.METRIC_ERROR,
            FailureCategory.METRIC_PARSE,
        ),
        ({'guard': GuardStatus.FAIL}, Outcome.GUARD_FAIL, FailureCategory.GUARD),
        ({'score': 10.0}, Outcome.DISCARD, FailureCategory.WORSE),
        ({'score': 12.0}, Outcome.DISCARD, FailureCategory.WORSE),
        ({'score': 9.9, 'threshold': Threshold('pct', 0.05)}, Outcome.DISCARD, FailureCategory.BELOW_MIN),
        ({'heldout_ok': False}, Outcome.DISCARD, FailureCategory.HELDOUT),
    ],
)
def test_non_keep_paths(overrides: dict[str, object], outcome: Outcome, category: FailureCategory) -> None:
    result = _decide(**overrides)
    assert result.outcome is outcome  # type: ignore[attr-defined]
    assert result.failure_category is category  # type: ignore[attr-defined]


def test_guard_none_counts_as_pass() -> None:
    assert _decide(guard=GuardStatus.NONE).outcome is Outcome.KEEP  # type: ignore[attr-defined]

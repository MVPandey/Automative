"""The held-out check, done without ever writing a number down.

The harness needs one comparison per improving try: is the candidate's held-out score at least as good
as the incumbent's? Rather than remember the incumbent's score (a number the agent could then go and
read), ``paired_check`` measures both trees in the same invocation and keeps only the verdict. The
scores exist in this process for a few seconds and nowhere else. ``history`` re-measures a finished
run's baseline and kept commits for a human report.

Both functions swap the in-scope files of the working tree between commits with a worktree-only
restore and always put the candidate's files back, even when a measurement crashes, so untracked data
next to the code (the usual case) is measured in place.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from automative.gitops import Git
from automative.scope import Classification, classify
from automative.spec import Direction
from automative.verify import Runner, VerifyResult, measure

__all__ = ['HeldoutPoint', 'HeldoutVerdict', 'history', 'paired_check']


@dataclass(frozen=True, slots=True)
class HeldoutVerdict:
    """What ``try`` learns: pass or fail, and why when it could not compare."""

    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class HeldoutPoint:
    """One re-measured commit in the human report."""

    iteration: int
    commit: str
    result: VerifyResult


def _scoped(git: Git, base: str, head: str, scope: Sequence[str], protected: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        p
        for p in git.changed_files(base, head)
        if classify(p, tuple(scope), tuple(protected)) is Classification.IN_SCOPE
    )


@contextmanager
def _tree_at(git: Git, current: str, target: str, scope: Sequence[str], protected: Sequence[str]) -> Iterator[None]:
    """Temporarily make the working tree's in-scope files match ``target``; restore ``current`` on exit."""
    paths = _scoped(git, current, target, scope, protected)
    git.restore_from(target, paths)
    try:
        yield
    finally:
        git.restore_from(current, paths)


def paired_check(
    *,
    runner: Runner,
    git: Git,
    root: Path,
    cmd: str,
    timeout_s: int,
    direction: Direction,
    scope: Sequence[str],
    protected: Sequence[str],
    incumbent: str,
    candidate: str,
) -> HeldoutVerdict:
    """Measure the candidate (the current tree) and the incumbent commit; return only the verdict."""
    cand = measure(runner, cmd, root, timeout_s, 1, None)
    if not cand.ok:
        return HeldoutVerdict(False, f'held-out check {cand.outcome.value} on the candidate')
    with _tree_at(git, candidate, incumbent, scope, protected):
        inc = measure(runner, cmd, root, timeout_s, 1, None)
    if not inc.ok:
        return HeldoutVerdict(False, f'held-out check {inc.outcome.value} on the incumbent')
    assert cand.score is not None and inc.score is not None
    not_worse = cand.score <= inc.score if direction is Direction.LOWER else cand.score >= inc.score
    return HeldoutVerdict(
        not_worse, 'held-out not worse than the incumbent' if not_worse else 'held-out metric regressed'
    )


def history(
    *,
    runner: Runner,
    git: Git,
    root: Path,
    cmd: str,
    timeout_s: int,
    scope: Sequence[str],
    protected: Sequence[str],
    head: str,
    points: Sequence[tuple[int, str]],
    announce: Callable[[str], None] | None = None,
) -> tuple[HeldoutPoint, ...]:
    """Re-measure each ``(iteration, commit)`` on the held-out command, restoring ``head`` afterwards."""
    out: list[HeldoutPoint] = []
    for iteration, commit in points:
        if announce:
            announce(f'measuring i{iteration} ({commit})')
        with _tree_at(git, head, commit, scope, protected):
            result = measure(runner, cmd, root, timeout_s, 1, None)
        out.append(HeldoutPoint(iteration=iteration, commit=commit, result=result))
    return tuple(out)

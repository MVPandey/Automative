"""Exception hierarchy for the automative package.

Every error raised by the library derives from :class:`AutomativeError`; the CLI maps each subclass to a
process exit code so hooks and scripts can branch on outcomes without parsing text.
"""

__all__ = [
    'AutomativeError',
    'BenchError',
    'BudgetError',
    'ContextError',
    'GitError',
    'IntegrityError',
    'ProtocolError',
    'ScopeError',
    'SpecError',
    'StateError',
    'StrategyError',
    'VerifyError',
]


class AutomativeError(Exception):
    """Base exception for automative."""

    exit_code: int = 1


class SpecError(AutomativeError):
    """AUTOMATIVE.md is missing, malformed, or violates the schema."""

    exit_code = 10


class StateError(AutomativeError):
    """Run state is missing, corrupt, or the requested transition is illegal."""

    exit_code = 11


class GitError(AutomativeError):
    """A git operation failed or the working tree is not in the required condition."""

    exit_code = 12


class VerifyError(AutomativeError):
    """The verify command could not be executed."""

    exit_code = 13


class IntegrityError(AutomativeError):
    """A protected file, the spec, or the pinned protocol was modified during a run."""

    exit_code = 14


class ScopeError(AutomativeError):
    """A change touched files outside the declared scope."""

    exit_code = 15


class BudgetError(AutomativeError):
    """The run has exhausted its budget or hit a stop condition."""

    exit_code = 16


class ProtocolError(AutomativeError):
    """Protocol version store, manifest, or candidate validation failure."""

    exit_code = 17


class StrategyError(AutomativeError):
    """Strategy catalogue operation was refused or malformed."""

    exit_code = 18


class BenchError(AutomativeError):
    """Benchmark suite operation failed."""

    exit_code = 19


class ContextError(AutomativeError):
    """The agent is acting on a view of the run that the harness never showed it (or showed before it changed)."""

    exit_code = 20

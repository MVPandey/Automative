"""Running the verify, guard, and held-out commands and parsing their scores.

The agent never runs these; only :mod:`automative.runloop` does, through this module, so a score is always
produced from a subprocess the CLI controlled.
"""

import re
import shlex
import statistics
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

__all__ = [
    'SCORE_RE',
    'CommandResult',
    'GuardResult',
    'GuardStatus',
    'LocalRunner',
    'Runner',
    'SudoRunner',
    'VerifyOutcome',
    'VerifyResult',
    'measure',
    'parse_score',
    'run_guards',
]

SCORE_RE = re.compile(r'^-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$')
TAIL_CHARS = 4000


class VerifyOutcome(StrEnum):
    """How a verify invocation ended."""

    OK = 'ok'
    CRASH = 'crash'
    TIMEOUT = 'timeout'
    METRIC_ERROR = 'metric_error'


class GuardStatus(StrEnum):
    """Aggregate result of the guard commands."""

    PASS = 'pass'
    FAIL = 'fail'
    NONE = 'none'


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Raw outcome of one subprocess."""

    exit_code: int | None
    stdout: str
    stderr: str
    runtime_s: float
    timed_out: bool


class Runner(Protocol):
    """Executes a shell command; swap for a sandboxed implementation later."""

    def run(self, cmd: str, cwd: Path, timeout_s: int) -> CommandResult: ...


class LocalRunner:
    """Runs commands in the local shell."""

    def run(self, cmd: str, cwd: Path, timeout_s: int) -> CommandResult:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, check=False
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or '')
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or '')
            return CommandResult(None, out, err, time.monotonic() - started, True)
        except OSError as exc:
            return CommandResult(127, '', str(exc), time.monotonic() - started, False)
        return CommandResult(proc.returncode, proc.stdout, proc.stderr, time.monotonic() - started, False)


@dataclass(frozen=True, slots=True)
class SudoRunner:
    """Runs a command as another OS user through ``sudo -n -u USER -- ARGV``.

    Meant for the held-out command: the held-out data is owned by ``user`` with mode 700, and a sudoers
    rule lets the harness's user run exactly this command as ``user`` and nothing else. The command is
    split into argv (no shell), so the sudoers rule can name it precisely.
    """

    user: str

    def argv(self, cmd: str) -> list[str]:
        return ['sudo', '-n', '-u', self.user, '--', *shlex.split(cmd)]

    def run(self, cmd: str, cwd: Path, timeout_s: int) -> CommandResult:
        started = time.monotonic()
        try:
            proc = subprocess.run(
                self.argv(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout_s, check=False
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or '')
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or '')
            return CommandResult(None, out, err, time.monotonic() - started, True)
        except (OSError, ValueError) as exc:
            return CommandResult(127, '', str(exc), time.monotonic() - started, False)
        return CommandResult(proc.returncode, proc.stdout, proc.stderr, time.monotonic() - started, False)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Outcome of measuring the metric, possibly over several repeats."""

    outcome: VerifyOutcome
    score: float | None
    samples: tuple[float, ...]
    exit_code: int | None
    runtime_s: float
    tail: str

    @property
    def ok(self) -> bool:
        return self.outcome is VerifyOutcome.OK and self.score is not None


@dataclass(frozen=True, slots=True)
class GuardResult:
    """Outcome of the guard commands."""

    status: GuardStatus
    failed_cmd: str | None
    tail: str


def parse_score(stdout: str) -> float | None:
    """Return the number on the last non-empty stdout line, or ``None`` if it is not a bare number."""
    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if text:
            return float(text) if SCORE_RE.match(text) else None
    return None


def _tail(text: str) -> str:
    return text[-TAIL_CHARS:]


def _append_log(log_path: Path | None, header: str, result: CommandResult) -> None:
    if log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('a', encoding='utf-8') as handle:
        handle.write(f'===== {header} (exit={result.exit_code}, {result.runtime_s:.1f}s) =====\n')
        handle.write('--- stdout ---\n' + result.stdout + '\n--- stderr ---\n' + result.stderr + '\n')


def measure(
    runner: Runner, cmd: str, cwd: Path, timeout_s: int, repeats: int = 1, log_path: Path | None = None
) -> VerifyResult:
    """Run ``cmd`` ``repeats`` times and return the median score; any failed sample fails the whole measure."""
    samples: list[float] = []
    total_runtime = 0.0
    last_exit: int | None = 0
    for index in range(max(1, repeats)):
        result = runner.run(cmd, cwd, timeout_s)
        total_runtime += result.runtime_s
        last_exit = result.exit_code
        _append_log(log_path, f'verify sample {index + 1}/{repeats}', result)
        if result.timed_out:
            return VerifyResult(VerifyOutcome.TIMEOUT, None, tuple(samples), None, total_runtime, _tail(result.stderr))
        if result.exit_code != 0:
            tail = _tail(result.stderr or result.stdout)
            return VerifyResult(VerifyOutcome.CRASH, None, tuple(samples), result.exit_code, total_runtime, tail)
        score = parse_score(result.stdout)
        if score is None:
            tail = _tail(result.stdout or result.stderr)
            return VerifyResult(VerifyOutcome.METRIC_ERROR, None, tuple(samples), 0, total_runtime, tail)
        samples.append(score)
    return VerifyResult(VerifyOutcome.OK, statistics.median(samples), tuple(samples), last_exit, total_runtime, '')


def run_guards(
    runner: Runner, cmds: tuple[str, ...], cwd: Path, timeout_s: int, log_path: Path | None = None
) -> GuardResult:
    """Run every guard; the first non-zero exit fails the guard."""
    if not cmds:
        return GuardResult(GuardStatus.NONE, None, '')
    for cmd in cmds:
        result = runner.run(cmd, cwd, timeout_s)
        _append_log(log_path, f'guard: {cmd}', result)
        if result.timed_out or result.exit_code != 0:
            return GuardResult(GuardStatus.FAIL, cmd, _tail(result.stderr or result.stdout))
    return GuardResult(GuardStatus.PASS, None, '')

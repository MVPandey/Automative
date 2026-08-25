"""Shared fixtures: a scratch git repo with a byte-count verifier, and a fake clock."""

import os
import subprocess
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automative.runloop import Project, RunLoop

SPEC = """---
automative: 1
protocol: 1.0.0
tags: [test]
metric:
  name: bytes
  direction: lower
  verify: ./score.sh
  guard: [./guard.sh]
  timeout_s: 5
  repeats: 1
  min_improvement: "0"
scope: [src/**]
protected: [score.sh, guard.sh]
budget: {{iterations: {iterations}, minutes: 0, plateau_patience: {patience}, max_consecutive_errors: 3, max_denied_tool_calls: 5}}
enforcement: {{require_hooks: false}}
---
# Goal

Make src/data.txt as small as possible while keeping the word hello.
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / 'home'
    monkeypatch.setenv('AUTOMATIVE_HOME', str(home))
    return home


def make_repo(root: Path, *, iterations: int = 30, patience: int = 8) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'src').mkdir(exist_ok=True)
    (root / 'src' / 'data.txt').write_text('hello world hello world hello world\n')
    (root / 'score.sh').write_text('#!/bin/sh\nwc -c < src/data.txt | tr -d " "\n')
    (root / 'guard.sh').write_text('#!/bin/sh\ngrep -q hello src/data.txt\n')
    (root / 'other.txt').write_text('untouched\n')
    for name in ('score.sh', 'guard.sh'):
        os.chmod(root / name, 0o755)
    (root / 'AUTOMATIVE.md').write_text(SPEC.format(iterations=iterations, patience=patience))
    (root / '.gitignore').write_text(
        '.automative/state.json\n.automative/lock.json\n.automative/heartbeat\n.automative/budget.json\n'
        '.automative/.cli.lock\n.automative/runs/\n'
    )
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'config', 'user.email', 'test@example.com')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'commit.gpgsign', 'false')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'init')
    return root


@pytest.fixture
def repo(tmp_path: Path, home: Path) -> Path:
    return make_repo(tmp_path / 'repo')


class FakeClock:
    """Deterministic, manually advanced clock."""

    def __init__(self) -> None:
        self.now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def loop(repo: Path, clock: FakeClock) -> RunLoop:
    return RunLoop(Project.load(repo), clock=clock)


@pytest.fixture
def started(loop: RunLoop) -> RunLoop:
    loop.start('test')
    return loop


@pytest.fixture
def write_data(repo: Path) -> Callable[[str], None]:
    def _write(text: str) -> None:
        (repo / 'src' / 'data.txt').write_text(text)

    return _write


@pytest.fixture
def in_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(repo)
    yield repo

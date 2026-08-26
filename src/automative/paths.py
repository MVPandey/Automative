"""Filesystem locations used by automative: the per-project ``.automative/`` directory and the global home."""

import os
from dataclasses import dataclass
from pathlib import Path

from automative.errors import StateError

__all__ = ['SPEC_FILENAME', 'ProjectPaths', 'automative_home', 'find_root']

SPEC_FILENAME = 'AUTOMATIVE.md'
DOTDIR = '.automative'
HOME_ENV = 'AUTOMATIVE_HOME'


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Resolved paths for one target project."""

    root: Path

    @property
    def spec_file(self) -> Path:
        return self.root / SPEC_FILENAME

    @property
    def dotdir(self) -> Path:
        return self.root / DOTDIR

    @property
    def state_file(self) -> Path:
        return self.dotdir / 'state.json'

    @property
    def lock_file(self) -> Path:
        return self.dotdir / 'lock.json'

    @property
    def heartbeat_file(self) -> Path:
        return self.dotdir / 'heartbeat'

    @property
    def ledger_file(self) -> Path:
        return self.dotdir / 'ledger.jsonl'

    @property
    def strategies_file(self) -> Path:
        return self.dotdir / 'strategies.jsonl'

    @property
    def runs_dir(self) -> Path:
        return self.dotdir / 'runs'

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def iter_dir(self, run_id: str, iteration: int) -> Path:
        return self.run_dir(run_id) / f'iter-{iteration:03d}'

    def notes_file(self, run_id: str) -> Path:
        return self.run_dir(run_id) / 'notes.md'

    @property
    def heldout_dir(self) -> Path:
        """Sealed: held-out scores and logs. Hooks refuse to read it during a run."""
        return self.dotdir / 'heldout'

    @property
    def heldout_file(self) -> Path:
        return self.heldout_dir / 'scores.jsonl'

    def heldout_log(self, run_id: str, iteration: int) -> Path:
        return self.heldout_dir / f'{run_id}-iter-{iteration:03d}.log'

    def relative(self, path: Path) -> str:
        """Return ``path`` as a posix string relative to the project root."""
        return path.resolve().relative_to(self.root.resolve()).as_posix()


def find_root(start: Path | None = None) -> ProjectPaths:
    """Locate the nearest ancestor directory containing ``AUTOMATIVE.md``.

    Raises:
        StateError: If no spec file is found walking up from ``start``.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / SPEC_FILENAME).is_file():
            return ProjectPaths(root=candidate)
    raise StateError(f'No {SPEC_FILENAME} found in {current} or its parents; run `automative init` first')


def automative_home() -> Path:
    """Return the global automative home (``$AUTOMATIVE_HOME`` or ``~/.automative``)."""
    override = os.environ.get(HOME_ENV)
    return Path(override).expanduser() if override else Path.home() / DOTDIR

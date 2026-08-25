"""Thin subprocess wrapper around the git operations the harness relies on."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from automative.errors import GitError

__all__ = ['NOTES_REF', 'Git', 'ref_for_try']

NOTES_REF = 'automative'


def ref_for_try(run_id: str, iteration: int) -> str:
    """Return the ref that pins the commit of one try, e.g. ``refs/automative/r-.../7``."""
    return f'refs/automative/{run_id}/{iteration}'


@dataclass(frozen=True, slots=True)
class Git:
    """Git operations rooted at ``root``. Every failure raises :class:`GitError`."""

    root: Path

    def run(self, *args: str, check: bool = True, strip: bool = True) -> str:
        """Run ``git <args>`` and return stdout (stripped unless ``strip`` is false)."""
        try:
            proc = subprocess.run(
                ['git', *args], cwd=self.root, capture_output=True, text=True, check=False, timeout=120
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError(f'git {" ".join(args)} failed: {exc}') from exc
        if check and proc.returncode != 0:
            raise GitError(f'git {" ".join(args)} failed ({proc.returncode}): {proc.stderr.strip()}')
        return proc.stdout.strip() if strip else proc.stdout

    def is_repo(self) -> bool:
        try:
            return self.run('rev-parse', '--is-inside-work-tree') == 'true'
        except GitError:
            return False

    def head(self) -> str:
        return self.run('rev-parse', '--short=10', 'HEAD')

    def has_commits(self) -> bool:
        try:
            self.run('rev-parse', '--verify', 'HEAD')
        except GitError:
            return False
        return True

    def current_branch(self) -> str | None:
        name = self.run('symbolic-ref', '--short', '-q', 'HEAD', check=False)
        return name or None

    def dirty_files(self) -> tuple[str, ...]:
        """Return modified, staged, and untracked paths (porcelain v1, renames reported as the new path)."""
        out = self.run('status', '--porcelain', '--untracked-files=all', strip=False)
        paths: list[str] = []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]
            if ' -> ' in path:
                path = path.split(' -> ', 1)[1]
            paths.append(path.strip('"'))
        return tuple(sorted(paths))

    def is_clean(self) -> bool:
        return not self.dirty_files()

    def branch_exists(self, name: str) -> bool:
        return self.run('rev-parse', '--verify', '-q', f'refs/heads/{name}', check=False) != ''

    def create_branch(self, name: str) -> None:
        self.run('checkout', '-q', '-b', name)

    def checkout(self, name: str) -> None:
        self.run('checkout', '-q', name)

    def stage(self, paths: tuple[str, ...]) -> None:
        if paths:
            self.run('add', '--', *paths)

    def staged_files(self) -> tuple[str, ...]:
        out = self.run('diff', '--cached', '--name-only')
        return tuple(sorted(p for p in out.splitlines() if p))

    def commit(self, message: str) -> str:
        self.run('commit', '-q', '--no-verify', '-m', message)
        return self.head()

    def revert_head(self) -> str:
        """Revert HEAD with a new commit; on conflict abort and hard-reset to the parent instead."""
        try:
            self.run('revert', '--no-edit', 'HEAD')
        except GitError:
            self.run('revert', '--abort', check=False)
            self.run('reset', '-q', '--hard', 'HEAD~1')
        return self.head()

    def reset_hard(self, rev: str) -> None:
        self.run('reset', '-q', '--hard', rev)

    def restore(self, paths: tuple[str, ...]) -> None:
        """Discard working-tree changes to tracked ``paths`` and delete untracked ones."""
        if not paths:
            return
        tracked = tuple(p for p in paths if self.run('ls-files', '--error-unmatch', '--', p, check=False))
        untracked = tuple(p for p in paths if p not in tracked)
        if tracked:
            self.run('checkout', '-q', '--', *tracked)
        for rel in untracked:
            target = self.root / rel
            if target.is_file():
                target.unlink()

    def exists_in(self, rev: str, path: str) -> bool:
        """Whether ``path`` is a blob in ``rev``."""
        return self.run('cat-file', '-e', f'{rev}:{path}', check=False) == '' and (
            self.run('ls-tree', '--name-only', rev, '--', path, check=False) != ''
        )

    def restore_from(self, rev: str, paths: tuple[str, ...]) -> None:
        """Make the working tree copy of ``paths`` match ``rev`` without touching the index."""
        present = tuple(p for p in paths if self.exists_in(rev, p))
        if present:
            self.run('restore', f'--source={rev}', '--worktree', '--', *present)
        for rel in paths:
            if rel in present:
                continue
            target = self.root / rel
            if target.is_file():
                target.unlink()

    def add_note(self, sha: str, text: str) -> None:
        self.run('notes', f'--ref={NOTES_REF}', 'add', '-f', '-m', text, sha)

    def update_ref(self, ref: str, sha: str) -> None:
        self.run('update-ref', ref, sha)

    def changed_files(self, base: str, head: str = 'HEAD') -> tuple[str, ...]:
        out = self.run('diff', '--name-only', f'{base}..{head}')
        return tuple(sorted(p for p in out.splitlines() if p))

    def log_oneline(self, count: int = 10) -> str:
        return self.run('log', '--oneline', f'-{count}', check=False)

    def bundle(self, target: Path, rev: str) -> None:
        self.run('bundle', 'create', str(target), rev)

    def config_value(self, key: str) -> str:
        return self.run('config', '--get', key, check=False)

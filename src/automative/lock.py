"""Hash-locking of protected files so the verifier cannot be edited during a run."""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from automative.errors import IntegrityError, StateError
from automative.paths import SPEC_FILENAME
from automative.scope import matches

__all__ = [
    'SKIP_DIRS',
    'LockFile',
    'assert_intact',
    'check',
    'read_lock',
    'resolve_protected',
    'sha256_file',
    'snapshot',
    'write_lock',
]

SKIP_DIRS = frozenset(
    {'.git', '.automative', '.venv', 'venv', 'node_modules', '__pycache__', '.mypy_cache', '.ruff_cache'}
)


class LockFile(BaseModel):
    """Hashes captured at ``run start``."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    protected: dict[str, str]
    spec_sha: str
    protocol_manifest_sha: str | None = None


def sha256_file(path: Path) -> str:
    """Return the hex sha256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _walk(root: Path) -> list[str]:
    out: list[str] = []
    stack = [root]
    while stack:
        current = stack.pop()
        for entry in sorted(current.iterdir()):
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                out.append(entry.relative_to(root).as_posix())
    return out


def resolve_protected(root: Path, patterns: tuple[str, ...]) -> tuple[str, ...]:
    """Return sorted relative paths of existing files matching ``patterns``; the spec file is always included."""
    files = {p for p in _walk(root) if matches(p, patterns)}
    files.add(SPEC_FILENAME)
    return tuple(sorted(files))


def snapshot(root: Path, rel_paths: tuple[str, ...]) -> dict[str, str]:
    """Hash every listed file; missing files hash to ``"missing"``."""
    result: dict[str, str] = {}
    for rel in rel_paths:
        path = root / rel
        result[rel] = sha256_file(path) if path.is_file() else 'missing'
    return result


def check(root: Path, expected: dict[str, str]) -> tuple[str, ...]:
    """Return the relative paths whose current hash differs from ``expected``."""
    current = snapshot(root, tuple(expected))
    return tuple(sorted(rel for rel, digest in expected.items() if current[rel] != digest))


def assert_intact(root: Path, expected: dict[str, str]) -> None:
    """Raise :class:`IntegrityError` if any protected file changed."""
    changed = check(root, expected)
    if changed:
        raise IntegrityError('Protected files were modified: ' + ', '.join(changed))


def write_lock(path: Path, lock: LockFile) -> None:
    """Persist the lock file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(lock.model_dump(mode='json'), indent=2) + '\n', encoding='utf-8')


def read_lock(path: Path) -> LockFile:
    """Load the lock file."""
    try:
        return LockFile.model_validate_json(path.read_text(encoding='utf-8'))
    except (OSError, ValidationError, ValueError) as exc:
        raise StateError(f'Cannot read lock file {path}: {exc}') from exc

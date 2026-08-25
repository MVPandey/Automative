"""Protocol version store: immutable versioned protocol directories, manifests, and pins.

Phase 4 extends this with candidate ops, promotion, and fallback; this module already owns resolution and
integrity because ``session brief`` and ``try`` verify the pinned protocol every time.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from automative.errors import ProtocolError
from automative.paths import automative_home

__all__ = [
    'MANIFEST_NAME',
    'NULL_VERSION',
    'Manifest',
    'ProtocolVersion',
    'bundled_versions_dir',
    'compute_files_sha',
    'list_versions',
    'load_manifest',
    'manifest_sha',
    'resolve_version',
    'user_versions_dir',
    'verify_integrity',
    'write_manifest',
]

MANIFEST_NAME = 'MANIFEST.json'
NULL_VERSION = '0.0.0-null'
SEMVER_RE = re.compile(r'^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$')


class Manifest(BaseModel):
    """Provenance and hashes of one protocol version."""

    model_config = ConfigDict(frozen=True, extra='allow')

    version: str
    parent: str | None = None
    created_at: str
    created_by: str
    files: dict[str, str]
    ops: tuple[dict[str, object], ...] = ()
    rationale: str = ''
    strategy_ids: tuple[str, ...] = ()
    evidence: dict[str, object] = Field(default_factory=dict)
    status: str = 'promoted'


@dataclass(frozen=True, slots=True)
class ProtocolVersion:
    """A resolved protocol version on disk."""

    version: str
    path: Path
    manifest: Manifest
    bundled: bool

    @property
    def skill_file(self) -> Path:
        return self.path / 'SKILL.md'

    @property
    def rules_file(self) -> Path:
        return self.path / 'rules.toml'


def bundled_versions_dir() -> Path:
    """Directory of protocol versions shipped inside the package."""
    return Path(str(resources.files('automative') / 'protocol' / 'versions'))


def user_versions_dir() -> Path:
    """Directory of installed/candidate protocol versions under the automative home."""
    return automative_home() / 'protocol' / 'versions'


def _sort_key(version: str) -> tuple[int, int, int, int, str]:
    match = SEMVER_RE.match(version)
    if not match:
        return (0, 0, 0, 0, version)
    major, minor, patch, pre = match.groups()
    return (int(major), int(minor), int(patch), 0 if pre else 1, pre or '')


def list_versions() -> tuple[ProtocolVersion, ...]:
    """Return every resolvable version, user-store entries shadowing bundled ones, sorted ascending."""
    found: dict[str, ProtocolVersion] = {}
    for base, bundled in ((bundled_versions_dir(), True), (user_versions_dir(), False)):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.is_dir() and (entry / MANIFEST_NAME).is_file():
                found[entry.name] = ProtocolVersion(entry.name, entry, load_manifest(entry), bundled)
    return tuple(sorted(found.values(), key=lambda v: _sort_key(v.version)))


def load_manifest(version_dir: Path) -> Manifest:
    """Read and validate a version's manifest."""
    try:
        return Manifest.model_validate_json((version_dir / MANIFEST_NAME).read_text(encoding='utf-8'))
    except (OSError, ValidationError, ValueError) as exc:
        raise ProtocolError(f'Cannot read protocol manifest in {version_dir}: {exc}') from exc


def resolve_version(version: str) -> ProtocolVersion:
    """Resolve ``version`` from the user store first, then the bundled versions."""
    for base, bundled in ((user_versions_dir(), False), (bundled_versions_dir(), True)):
        candidate = base / version
        if (candidate / MANIFEST_NAME).is_file():
            return ProtocolVersion(version, candidate, load_manifest(candidate), bundled)
    available = ', '.join(v.version for v in list_versions()) or 'none'
    raise ProtocolError(f'Protocol version {version!r} is not installed (available: {available})')


def compute_files_sha(version_dir: Path) -> dict[str, str]:
    """Hash every file in the version directory except the manifest itself."""
    result: dict[str, str] = {}
    for path in sorted(version_dir.rglob('*')):
        if path.is_file() and path.name != MANIFEST_NAME:
            result[path.relative_to(version_dir).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def manifest_sha(manifest: Manifest) -> str:
    """Stable hash of the manifest's file table, stored in the lock file."""
    payload = json.dumps(manifest.files, sort_keys=True).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def verify_integrity(protocol: ProtocolVersion) -> tuple[str, ...]:
    """Return the files whose current hash differs from the manifest (empty means intact)."""
    current = compute_files_sha(protocol.path)
    expected = protocol.manifest.files
    changed = [p for p, digest in expected.items() if current.get(p) != digest]
    changed.extend(p for p in current if p not in expected)
    return tuple(sorted(changed))


def write_manifest(
    version_dir: Path,
    *,
    version: str,
    parent: str | None,
    created_by: str,
    rationale: str = '',
    status: str = 'promoted',
    ops: tuple[dict[str, object], ...] = (),
    strategy_ids: tuple[str, ...] = (),
    evidence: dict[str, object] | None = None,
    created_at: str | None = None,
) -> Manifest:
    """Hash the version directory and write its manifest."""
    if not SEMVER_RE.match(version):
        raise ProtocolError(f'{version!r} is not a semver string')
    stamp = created_at or datetime.now(UTC).isoformat(timespec='seconds')
    manifest = Manifest(
        version=version,
        parent=parent,
        created_at=stamp,
        created_by=created_by,
        files=compute_files_sha(version_dir),
        ops=ops,
        rationale=rationale,
        strategy_ids=strategy_ids,
        evidence=evidence or {},
        status=status,
    )
    (version_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest.model_dump(mode='json'), indent=2) + '\n', encoding='utf-8'
    )
    return manifest

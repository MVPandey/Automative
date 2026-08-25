"""Bundled protocol immutability and resolution."""

import hashlib
from pathlib import Path

import pytest

from automative import protocol
from automative.errors import ProtocolError


def test_bundled_checksums_match() -> None:
    base = protocol.bundled_versions_dir()
    expected = {}
    for line in (base.parent / 'CHECKSUMS.txt').read_text().splitlines():
        digest, rel = line.split('  ', 1)
        expected[rel] = digest
    actual = {
        f.relative_to(base).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
        for d in base.iterdir()
        if d.is_dir()
        for f in d.rglob('*')
        if f.is_file()
    }
    assert actual == expected, 'bundled protocol changed: re-seal with write_manifest and update CHECKSUMS.txt'


def test_manifests_are_intact() -> None:
    for version in protocol.list_versions():
        assert protocol.verify_integrity(version) == (), version.version


def test_resolve_and_order(home: Path) -> None:
    versions = [v.version for v in protocol.list_versions()]
    assert versions[:2] == ['0.0.0-null', '1.0.0']
    assert protocol.resolve_version('1.0.0').bundled
    with pytest.raises(ProtocolError):
        protocol.resolve_version('9.9.9')


def test_user_store_shadows_bundled(home: Path) -> None:
    user_dir = protocol.user_versions_dir() / '1.0.0'
    user_dir.mkdir(parents=True)
    (user_dir / 'SKILL.md').write_text('# shadow\n')
    protocol.write_manifest(user_dir, version='1.0.0', parent=None, created_by='test')
    resolved = protocol.resolve_version('1.0.0')
    assert not resolved.bundled and resolved.skill_file.read_text() == '# shadow\n'

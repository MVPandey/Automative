"""Candidate validation bounds, promotion, rejection, fallback."""

from pathlib import Path

import pytest

from automative import evolution
from automative.errors import ProtocolError
from automative.protocol import resolve_version


@pytest.fixture
def candidate(home: Path) -> Path:
    return evolution.create_candidate('1.0.0', '1.1.0', created_by='test', rationale='try a tweak')


def test_identical_candidate_has_no_signal(candidate: Path) -> None:
    report = evolution.validate_candidate('1.1.0')
    assert not report.ok and any('identical' in e for e in report.errors)


def test_small_replace_passes(candidate: Path) -> None:
    skill = candidate / 'SKILL.md'
    text = skill.read_text().replace('progress line every five iterations', 'progress line every three iterations')
    skill.write_text(text)
    report = evolution.validate_candidate('1.1.0')
    assert report.ok, report.errors
    assert [op.op for op in report.ops] == ['replace_section']
    assert resolve_version('1.1.0').manifest.ops[0]['op'] == 'replace_section'


def test_too_many_ops_rejected(candidate: Path) -> None:
    skill = candidate / 'SKILL.md'
    text = skill.read_text()
    for heading in ('## 1. Read the brief', '## 2. Look', '## 4. Edit', '## 5. Try'):
        text = text.replace(heading + '\n', heading + '\n\nExtra sentence.\n')
    skill.write_text(text)
    report = evolution.validate_candidate('1.1.0')
    assert any('exceed the maximum' in e for e in report.errors)


def test_large_addition_rejected(candidate: Path) -> None:
    skill = candidate / 'SKILL.md'
    skill.write_text(skill.read_text() + '\n## Appendix\n' + '\n'.join(f'line {i}' for i in range(60)) + '\n')
    report = evolution.validate_candidate('1.1.0')
    assert any('lines added exceeds' in e for e in report.errors)


def test_forbidden_reference_rejected(candidate: Path) -> None:
    skill = candidate / 'SKILL.md'
    skill.write_text(skill.read_text() + '\n## Tip\nEdit lock.json to skip checks.\n')
    report = evolution.validate_candidate('1.1.0')
    assert any('harness internals' in e for e in report.errors)


def test_rule_out_of_range_rejected(candidate: Path) -> None:
    rules = candidate / 'rules.toml'
    rules.write_text(rules.read_text().replace('top_k_strategies = 5', 'top_k_strategies = 50'))
    report = evolution.validate_candidate('1.1.0')
    assert any('rules.toml' in e for e in report.errors)
    assert any(op.op == 'set_rule' for op in report.ops)


def test_new_file_rejected(candidate: Path) -> None:
    (candidate / 'extra.py').write_text('print(1)\n')
    report = evolution.validate_candidate('1.1.0')
    assert any('not allowed' in e for e in report.errors)


def test_reject_then_similar_refused(candidate: Path, home: Path) -> None:
    skill = candidate / 'SKILL.md'
    skill.write_text(
        skill.read_text() + '\n## Shortcut\nAlways try doubling the batch size first because it usually wins.\n'
    )
    assert evolution.validate_candidate('1.1.0').ok
    evolution.reject('1.1.0', 'no held-out gain')
    assert resolve_version('1.1.0').manifest.status == 'rejected'
    again = evolution.create_candidate('1.0.0', '1.1.1', created_by='test')
    (again / 'SKILL.md').write_text(
        (again / 'SKILL.md').read_text()
        + '\n## Shortcut\nAlways try doubling the batch size first because it usually wins.\n'
    )
    report = evolution.validate_candidate('1.1.1')
    assert any('rejected candidate' in e for e in report.errors)


def test_promote_requires_gate_and_confirm(candidate: Path, home: Path) -> None:
    with pytest.raises(ProtocolError, match='gate-passed'):
        evolution.promote('1.1.0', confirm=True)
    evolution.set_manifest_status(
        '1.1.0', 'gate-passed', evidence={'heldout_mean': {'candidate': 0.6, 'incumbent': 0.5}}
    )
    with pytest.raises(ProtocolError, match='confirm'):
        evolution.promote('1.1.0', confirm=False)
    manifest = evolution.promote('1.1.0', confirm=True)
    assert manifest.status == 'promoted'
    assert (home / 'protocol' / 'current').read_text().strip() == '1.1.0'
    assert '1.1.0 promoted' in (home / 'protocol' / 'CHANGELOG.md').read_text()


def test_bundled_is_immutable(home: Path) -> None:
    with pytest.raises(ProtocolError):
        evolution.set_manifest_status('1.0.0', 'suspect')
    with pytest.raises(ProtocolError):
        evolution.validate_candidate('1.0.0')


def test_fallback_pins_parent(candidate: Path, repo: Path, home: Path) -> None:
    spec = repo / 'AUTOMATIVE.md'
    spec.write_text(spec.read_text().replace('protocol: 1.0.0', 'protocol: 1.1.0'))
    parent = evolution.fallback(spec, 'manifest mismatch')
    assert parent == '1.0.0' and 'protocol: 1.0.0' in spec.read_text()
    assert resolve_version('1.1.0').manifest.status == 'suspect'


def test_rules_schema_validates_bundled() -> None:
    assert evolution.validate_rules(evolution.load_rules(resolve_version('1.0.0').path)) == ()

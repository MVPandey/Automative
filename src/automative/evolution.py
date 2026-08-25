"""Protocol evolution substrate (L2): candidates as bounded section-level diffs, gates, promotion, fallback.

A candidate is a copy of its parent that the agent edits in place. ``validate`` derives the ops from the
diff (add/replace/delete section, set_rule), enforces the bounds, and refuses anything resembling a
previously rejected candidate. Promotion requires gate evidence plus a human ``--confirm``.
"""

import difflib
import json
import re
import shutil
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

from automative.errors import ProtocolError
from automative.paths import automative_home
from automative.protocol import (
    MANIFEST_NAME,
    Manifest,
    ProtocolVersion,
    load_manifest,
    resolve_version,
    user_versions_dir,
    write_manifest,
)
from automative.spec import render_pin

__all__ = [
    'MAX_LINES_PER_OP',
    'MAX_OPS',
    'MAX_SKILL_LINES',
    'MAX_TOKENS',
    'CandidateReport',
    'Op',
    'append_changelog',
    'create_candidate',
    'derive_ops',
    'fallback',
    'load_rules',
    'promote',
    'record_telemetry',
    'reject',
    'set_manifest_status',
    'validate_candidate',
    'validate_rules',
]

MAX_OPS = 3
MAX_LINES_PER_OP = 40
MAX_SKILL_LINES = 400
MAX_TOKENS = 20_000
REJECT_SIMILARITY = 0.7
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')
FORBIDDEN_RE = re.compile(r'lock\.json|hooks\.json|settings\.json|--no-verify|\.git/hooks|disableAllHooks')
ALLOWED_FILES_RE = re.compile(r'^(SKILL\.md|rules\.toml|references/[^/]+\.md)$')
TOKEN_RE = re.compile(r'[a-z0-9]+')


@dataclass(frozen=True, slots=True)
class Op:
    """One bounded edit derived from the candidate/parent diff."""

    op: str
    file: str
    section: str
    lines_added: int
    lines_removed: int
    detail: str = ''

    def as_dict(self) -> dict[str, object]:
        return {
            'op': self.op,
            'file': self.file,
            'section': self.section,
            'lines_added': self.lines_added,
            'lines_removed': self.lines_removed,
            'detail': self.detail,
        }


@dataclass(frozen=True, slots=True)
class CandidateReport:
    """Validation outcome."""

    version: str
    ops: tuple[Op, ...]
    errors: tuple[str, ...]
    tokens: int
    skill_lines: int

    @property
    def ok(self) -> bool:
        return not self.errors


# ----- rules ---------------------------------------------------------------------------------------------


def _rules_schema() -> dict[str, Any]:
    text = (resources.files('automative') / 'schemas' / 'rules.schema.json').read_text('utf-8')
    return dict(json.loads(text))


def load_rules(version_dir: Path) -> dict[str, Any]:
    """Parse ``rules.toml``."""
    path = version_dir / 'rules.toml'
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding='utf-8'))
    except tomllib.TOMLDecodeError as exc:
        raise ProtocolError(f'{path} is not valid TOML: {exc}') from exc


def validate_rules(rules: dict[str, Any]) -> tuple[str, ...]:
    """Return schema violations (empty when valid)."""
    validator = jsonschema.Draft202012Validator(_rules_schema())
    return tuple(
        f'{"/".join(str(p) for p in err.path) or "rules"}: {err.message}' for err in validator.iter_errors(rules)
    )


def _flatten(rules: dict[str, Any], prefix: str = '') -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in rules.items():
        name = f'{prefix}{key}'
        if isinstance(value, dict):
            out.update(_flatten(value, f'{name}.'))
        else:
            out[name] = value
    return out


# ----- sections ------------------------------------------------------------------------------------------


def _sections(text: str) -> dict[str, list[str]]:
    """Split markdown into ``{heading: body lines}``; the preamble is keyed ``(preamble)``."""
    sections: dict[str, list[str]] = {}
    current = '(preamble)'
    sections[current] = []
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            current = line.strip()
            sections.setdefault(current, [])
            continue
        sections[current].append(line)
    return sections


def _line_delta(old: list[str], new: list[str]) -> tuple[int, int]:
    added = removed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag in ('replace', 'insert'):
            added += j2 - j1
        if tag in ('replace', 'delete'):
            removed += i2 - i1
    return added, removed


def _diff_markdown(rel: str, parent: str | None, candidate: str | None) -> list[Op]:
    ops: list[Op] = []
    if parent is None and candidate is not None:
        lines = candidate.splitlines()
        return [Op('add_file', rel, '(file)', len(lines), 0)]
    if candidate is None and parent is not None:
        return [Op('delete_file', rel, '(file)', 0, len(parent.splitlines()))]
    assert parent is not None and candidate is not None
    old, new = _sections(parent), _sections(candidate)
    for heading in list(old) + [h for h in new if h not in old]:
        if heading in old and heading in new:
            if old[heading] != new[heading]:
                added, removed = _line_delta(old[heading], new[heading])
                ops.append(Op('replace_section', rel, heading, added, removed))
        elif heading in new:
            ops.append(Op('add_section', rel, heading, len(new[heading]) + 1, 0))
        else:
            ops.append(Op('delete_section', rel, heading, 0, len(old[heading]) + 1))
    return ops


def derive_ops(parent: ProtocolVersion, candidate_dir: Path) -> tuple[Op, ...]:
    """Derive bounded ops from the file-level diff between parent and candidate."""
    ops: list[Op] = []
    files = {p.relative_to(parent.path).as_posix() for p in parent.path.rglob('*') if p.is_file()}
    files |= {p.relative_to(candidate_dir).as_posix() for p in candidate_dir.rglob('*') if p.is_file()}
    for rel in sorted(f for f in files if f != MANIFEST_NAME):
        old_path, new_path = parent.path / rel, candidate_dir / rel
        old = old_path.read_text(encoding='utf-8') if old_path.is_file() else None
        new = new_path.read_text(encoding='utf-8') if new_path.is_file() else None
        if rel == 'rules.toml':
            old_rules = _flatten(load_rules(parent.path))
            new_rules = _flatten(load_rules(candidate_dir))
            for key in sorted(set(old_rules) | set(new_rules)):
                if old_rules.get(key) != new_rules.get(key):
                    ops.append(Op('set_rule', rel, key, 1, 1, f'{old_rules.get(key)!r} to {new_rules.get(key)!r}'))
            continue
        ops.extend(_diff_markdown(rel, old, new))
    return tuple(ops)


def _tokens_of(candidate_dir: Path) -> int:
    total = 0
    for path in candidate_dir.rglob('*'):
        if path.is_file() and path.suffix in ('.md', '.toml'):
            total += int(len(path.read_text(encoding='utf-8').split()) * 1.3)
    return total


def _added_text(parent: ProtocolVersion, candidate_dir: Path) -> str:
    chunks: list[str] = []
    for path in candidate_dir.rglob('*'):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        rel = path.relative_to(candidate_dir).as_posix()
        old_path = parent.path / rel
        new_lines = path.read_text(encoding='utf-8').splitlines()
        old_lines = old_path.read_text(encoding='utf-8').splitlines() if old_path.is_file() else []
        chunks.extend(line for line in new_lines if line not in set(old_lines))
    return '\n'.join(chunks)


def _rejected_dir() -> Path:
    return automative_home() / 'protocol' / 'rejected'


def _similar_rejected(ops: tuple[Op, ...], added_text: str) -> str | None:
    tokens = frozenset(TOKEN_RE.findall(added_text.lower()))
    if not tokens:
        return None
    for path in sorted(_rejected_dir().glob('*.json')) if _rejected_dir().is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            continue
        other = frozenset(TOKEN_RE.findall(str(data.get('added_text', '')).lower()))
        if other and len(tokens & other) / len(tokens | other) > REJECT_SIMILARITY:
            return f'{path.stem}: {data.get("reason", "rejected")}'
    return None


def validate_candidate(version: str) -> CandidateReport:
    """Derive ops, enforce every bound, and record the ops in the manifest."""
    candidate = resolve_version(version)
    if candidate.bundled:
        raise ProtocolError(f'{version} is a bundled version, not a candidate')
    manifest = candidate.manifest
    if manifest.parent is None:
        raise ProtocolError(f'{version} has no parent; candidates must derive from an existing version')
    parent = resolve_version(manifest.parent)
    errors: list[str] = []
    for path in candidate.path.rglob('*'):
        if path.is_file() and path.name != MANIFEST_NAME:
            rel = path.relative_to(candidate.path).as_posix()
            if not ALLOWED_FILES_RE.match(rel):
                errors.append(f'file not allowed in a protocol: {rel}')
    ops = derive_ops(parent, candidate.path)
    if not ops:
        errors.append('candidate is identical to its parent (no signal)')
    if len(ops) > MAX_OPS:
        errors.append(f'{len(ops)} ops exceed the maximum of {MAX_OPS}')
    for op in ops:
        if op.lines_added > MAX_LINES_PER_OP:
            errors.append(f'{op.op} {op.file}#{op.section}: {op.lines_added} lines added exceeds {MAX_LINES_PER_OP}')
        if op.op in ('add_file', 'delete_file'):
            errors.append(f'{op.op} {op.file}: candidates may not add or delete files')
    skill_lines = len((candidate.path / 'SKILL.md').read_text(encoding='utf-8').splitlines())
    if skill_lines > MAX_SKILL_LINES:
        errors.append(f'SKILL.md has {skill_lines} lines; maximum is {MAX_SKILL_LINES}')
    tokens = _tokens_of(candidate.path)
    if tokens > MAX_TOKENS:
        errors.append(f'protocol is ~{tokens} tokens; maximum is {MAX_TOKENS}')
    errors.extend(f'rules.toml: {e}' for e in validate_rules(load_rules(candidate.path)))
    added = _added_text(parent, candidate.path)
    forbidden = FORBIDDEN_RE.findall(added)
    if forbidden:
        errors.append('added text references harness internals: ' + ', '.join(sorted(set(forbidden))))
    similar = _similar_rejected(ops, added)
    if similar:
        errors.append(f'too similar to a rejected candidate ({similar})')
    write_manifest(
        candidate.path,
        version=version,
        parent=manifest.parent,
        created_by=manifest.created_by,
        rationale=manifest.rationale,
        status='candidate' if not errors else 'invalid',
        ops=tuple(op.as_dict() for op in ops),
        strategy_ids=manifest.strategy_ids,
        evidence=dict(manifest.evidence),
        created_at=manifest.created_at,
    )
    return CandidateReport(version=version, ops=ops, errors=tuple(errors), tokens=tokens, skill_lines=skill_lines)


# ----- lifecycle -----------------------------------------------------------------------------------------


def create_candidate(parent_version: str, new_version: str, *, created_by: str, rationale: str = '') -> Path:
    """Copy the parent into the user store under ``new_version`` with a candidate manifest."""
    parent = resolve_version(parent_version)
    target = user_versions_dir() / new_version
    if target.exists():
        raise ProtocolError(f'{target} already exists')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(parent.path, target)
    write_manifest(
        target,
        version=new_version,
        parent=parent_version,
        created_by=created_by,
        rationale=rationale,
        status='candidate',
    )
    return target


def set_manifest_status(version: str, status: str, *, evidence: dict[str, object] | None = None) -> Manifest:
    """Update status (and evidence) on a version's manifest without touching its files."""
    proto = resolve_version(version)
    if proto.bundled:
        raise ProtocolError(f'{version} is bundled and immutable')
    manifest = proto.manifest
    merged = dict(manifest.evidence)
    if evidence:
        merged.update(evidence)
    return write_manifest(
        proto.path,
        version=version,
        parent=manifest.parent,
        created_by=manifest.created_by,
        rationale=manifest.rationale,
        status=status,
        ops=tuple(dict(op) for op in manifest.ops),
        strategy_ids=manifest.strategy_ids,
        evidence=merged,
        created_at=manifest.created_at,
    )


def append_changelog(entry: str) -> Path:
    """Append a dated entry to the global protocol CHANGELOG."""
    path = automative_home() / 'protocol' / 'CHANGELOG.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime('%Y-%m-%d')
    with path.open('a', encoding='utf-8') as handle:
        handle.write(f'\n## {stamp}: {entry}\n')
    return path


def promote(version: str, *, confirm: bool) -> Manifest:
    """Promote a gate-passed candidate; refuses without gate evidence or confirmation."""
    proto = resolve_version(version)
    if proto.manifest.status != 'gate-passed':
        raise ProtocolError(f'{version} is {proto.manifest.status}; only gate-passed candidates can be promoted')
    if not confirm:
        raise ProtocolError('Promotion requires a human: re-run with --confirm')
    manifest = set_manifest_status(version, 'promoted')
    (automative_home() / 'protocol').mkdir(parents=True, exist_ok=True)
    (automative_home() / 'protocol' / 'current').write_text(version + '\n', encoding='utf-8')
    evidence = json.dumps(manifest.evidence, sort_keys=True)
    ops = '; '.join(f'{o.get("op")} {o.get("file")}#{o.get("section")}' for o in manifest.ops)
    append_changelog(
        f'{version} promoted (parent {manifest.parent})\nOps: {ops}\nEvidence: {evidence}\n'
        f'Rationale: {manifest.rationale}'
    )
    return manifest


def reject(version: str, reason: str) -> Manifest:
    """Reject a candidate and remember its added text so near-duplicates are refused later."""
    proto = resolve_version(version)
    parent = resolve_version(proto.manifest.parent) if proto.manifest.parent else None
    added = _added_text(parent, proto.path) if parent else ''
    _rejected_dir().mkdir(parents=True, exist_ok=True)
    (_rejected_dir() / f'{version}.json').write_text(
        json.dumps(
            {'version': version, 'reason': reason, 'ops': [dict(o) for o in proto.manifest.ops], 'added_text': added},
            indent=2,
        ),
        encoding='utf-8',
    )
    manifest = set_manifest_status(version, 'rejected')
    append_changelog(f'{version} rejected: {reason}')
    return manifest


def fallback(spec_path: Path, reason: str) -> str:
    """Pin the project to the parent of its current protocol; mark the current one suspect."""
    text = spec_path.read_text(encoding='utf-8')
    match = re.search(r'^protocol:\s*(\S+)', text, re.MULTILINE)
    if not match:
        raise ProtocolError('No protocol pin found in AUTOMATIVE.md')
    current = match.group(1)
    proto = resolve_version(current)
    parent = proto.manifest.parent
    if not parent:
        raise ProtocolError(f'{current} has no parent to fall back to')
    resolve_version(parent)
    spec_path.write_text(render_pin(text, parent), encoding='utf-8')
    if not proto.bundled:
        set_manifest_status(current, 'suspect')
    append_changelog(f'auto-fallback {current} to {parent}: {reason}')
    return parent


def record_telemetry(entry: dict[str, object]) -> Path:
    """Append a per-run summary keyed by protocol version (advisory fallback signal)."""
    path = automative_home() / 'protocol' / 'telemetry.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, separators=(',', ':'), default=str) + '\n')
    return path


def load_manifest_of(version: str) -> Manifest:
    """Convenience for callers that only have a version string."""
    return load_manifest(resolve_version(version).path)

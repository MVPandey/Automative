"""The ``AUTOMATIVE.md`` contract: YAML front matter (machine-owned schema) plus a prose body for the agent."""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from automative.errors import SpecError

__all__ = [
    'BudgetSpec',
    'Direction',
    'EnforcementSpec',
    'MetricSpec',
    'Spec',
    'SpecDocument',
    'Threshold',
    'compute_spec_sha',
    'load_spec',
    'parse_spec',
    'parse_threshold',
    'render_pin',
]

FRONT_MATTER_RE = re.compile(r'\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z', re.DOTALL)
PIN_LINE_RE = re.compile(r'^(protocol:\s*)(\S+)(.*)$', re.MULTILINE)
SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')


class Direction(StrEnum):
    """Which way the metric should move."""

    LOWER = 'lower'
    HIGHER = 'higher'


@dataclass(frozen=True, slots=True)
class Threshold:
    """Minimum improvement required for a keep, either absolute or a fraction of the incumbent."""

    kind: Literal['abs', 'pct']
    value: float

    def amount(self, incumbent: float) -> float:
        """Return the absolute improvement required against ``incumbent``."""
        return self.value if self.kind == 'abs' else abs(incumbent) * self.value


def parse_threshold(raw: str | float | int) -> Threshold:
    """Parse ``"0"``, ``"0.001"``, or ``"2%"`` into a :class:`Threshold`."""
    text = str(raw).strip()
    try:
        if text.endswith('%'):
            return Threshold(kind='pct', value=float(text[:-1]) / 100.0)
        return Threshold(kind='abs', value=float(text))
    except ValueError as exc:
        msg = f'Invalid min_improvement {raw!r}; use "0", an absolute number, or a percent like "2%"'
        raise SpecError(msg) from exc


class MetricSpec(BaseModel):
    """How the target is measured."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    name: str = 'score'
    direction: Direction = Direction.LOWER
    verify: str
    guard: tuple[str, ...] = ()
    heldout: str | None = None
    timeout_s: int = Field(default=600, ge=1)
    repeats: int = Field(default=1, ge=1, le=9)
    min_improvement: str = '0'
    target: float | None = None

    @field_validator('guard', mode='before')
    @classmethod
    def _coerce_guard(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return tuple(value) if isinstance(value, list | tuple) else value

    @field_validator('min_improvement', mode='before')
    @classmethod
    def _coerce_min_improvement(cls, value: object) -> str:
        text = str(value).strip()
        parse_threshold(text)
        return text

    @property
    def threshold(self) -> Threshold:
        return parse_threshold(self.min_improvement)


class BudgetSpec(BaseModel):
    """Stop conditions. ``iterations=0`` or ``minutes=0`` means unbounded on that axis."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    iterations: int = Field(default=30, ge=0)
    minutes: int = Field(default=120, ge=0)
    plateau_patience: int = Field(default=8, ge=0)
    max_consecutive_errors: int = Field(default=3, ge=1)
    max_denied_tool_calls: int = Field(default=5, ge=1)


class EnforcementSpec(BaseModel):
    """Which deterministic controls are required for this project."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    require_hooks: bool = True
    heartbeat_max_age_s: int = Field(default=120, ge=10)


class Spec(BaseModel):
    """Validated front matter of ``AUTOMATIVE.md``."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    automative: int = Field(default=1, ge=1, le=1)
    protocol: str = '1.0.0'
    tags: tuple[str, ...] = ()
    metric: MetricSpec
    scope: tuple[str, ...]
    protected: tuple[str, ...] = ()
    budget: BudgetSpec = BudgetSpec()
    enforcement: EnforcementSpec = EnforcementSpec()

    @field_validator('protocol')
    @classmethod
    def _check_semver(cls, value: str) -> str:
        if not SEMVER_RE.match(value):
            raise ValueError(f'protocol must be a semver string, got {value!r}')
        return value

    @field_validator('scope', 'protected', 'tags', mode='before')
    @classmethod
    def _coerce_tuple(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        return tuple(value) if isinstance(value, list | tuple) else value

    @field_validator('scope')
    @classmethod
    def _non_empty_scope(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError('scope must list at least one glob the agent may edit')
        return value


@dataclass(frozen=True, slots=True)
class SpecDocument:
    """A parsed ``AUTOMATIVE.md``: the validated front matter, the prose body, and the raw text."""

    spec: Spec
    body: str
    raw: str

    @property
    def goal(self) -> str:
        """Return the first paragraph under the ``# Goal`` heading, or the first body paragraph."""
        match = re.search(r'^#\s+Goal\s*$\n+(.+?)(?:\n\s*\n|\Z)', self.body, re.MULTILINE | re.DOTALL)
        text = match.group(1) if match else self.body.strip().split('\n\n', 1)[0]
        return ' '.join(text.split())


def parse_spec(text: str) -> SpecDocument:
    """Parse and validate the text of an ``AUTOMATIVE.md``."""
    match = FRONT_MATTER_RE.match(text)
    if not match:
        raise SpecError('AUTOMATIVE.md must start with a YAML front matter block delimited by ---')
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SpecError(f'AUTOMATIVE.md front matter is not valid YAML: {exc}') from exc
    if not isinstance(data, dict):
        raise SpecError('AUTOMATIVE.md front matter must be a mapping')
    try:
        spec = Spec.model_validate(data)
    except ValidationError as exc:
        raise SpecError(f'AUTOMATIVE.md front matter is invalid:\n{exc}') from exc
    return SpecDocument(spec=spec, body=match.group(2), raw=text)


def load_spec(path: Path) -> SpecDocument:
    """Read and parse the spec file at ``path``."""
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        raise SpecError(f'Cannot read {path}: {exc}') from exc
    return parse_spec(text)


def render_pin(text: str, version: str) -> str:
    """Return ``text`` with only the ``protocol:`` front-matter line rewritten to ``version``."""
    if not SEMVER_RE.match(version):
        raise SpecError(f'{version!r} is not a semver string')
    match = FRONT_MATTER_RE.match(text)
    if not match:
        raise SpecError('Cannot pin: no front matter found')
    front, body = match.group(1), match.group(2)
    if not PIN_LINE_RE.search(front):
        front = f'protocol: {version}\n{front}'
    else:
        front = PIN_LINE_RE.sub(lambda m: f'{m.group(1)}{version}{m.group(3)}', front, count=1)
    return f'---\n{front}\n---\n{body}'


def compute_spec_sha(text: str) -> str:
    """Return the sha256 of the spec text, used to detect mid-run edits."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

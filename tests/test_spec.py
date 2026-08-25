"""AUTOMATIVE.md parsing and pinning."""

import pytest

from automative.errors import SpecError
from automative.spec import Direction, parse_spec, parse_threshold, render_pin

MINIMAL = """---
automative: 1
protocol: 1.0.0
metric:
  verify: ./score.sh
scope: [src/**]
---
# Goal

Shrink it.

## Context

Body text.
"""


def test_parse_minimal() -> None:
    doc = parse_spec(MINIMAL)
    assert doc.spec.metric.direction is Direction.LOWER
    assert doc.spec.scope == ('src/**',)
    assert doc.spec.budget.iterations == 30
    assert doc.goal == 'Shrink it.'
    assert 'Body text.' in doc.body


def test_missing_front_matter() -> None:
    with pytest.raises(SpecError):
        parse_spec('# no front matter\n')


def test_invalid_field() -> None:
    with pytest.raises(SpecError):
        parse_spec(MINIMAL.replace('scope: [src/**]', 'scope: []'))


def test_unknown_key_rejected() -> None:
    with pytest.raises(SpecError):
        parse_spec(MINIMAL.replace('protocol: 1.0.0', 'protocol: 1.0.0\nbogus: 1'))


@pytest.mark.parametrize(
    ('raw', 'kind', 'value'),
    [('0', 'abs', 0.0), ('0.001', 'abs', 0.001), ('2%', 'pct', 0.02), (3, 'abs', 3.0)],
)
def test_threshold(raw: str | int, kind: str, value: float) -> None:
    threshold = parse_threshold(raw)
    assert (threshold.kind, threshold.value) == (kind, value)


def test_threshold_invalid() -> None:
    with pytest.raises(SpecError):
        parse_threshold('lots')


def test_render_pin_preserves_body_bytes() -> None:
    pinned = render_pin(MINIMAL, '1.2.3')
    assert 'protocol: 1.2.3' in pinned
    assert pinned.split('---\n', 2)[2] == MINIMAL.split('---\n', 2)[2]
    assert parse_spec(pinned).spec.protocol == '1.2.3'


def test_render_pin_adds_line_when_missing() -> None:
    text = MINIMAL.replace('protocol: 1.0.0\n', '')
    assert parse_spec(render_pin(text, '2.0.0')).spec.protocol == '2.0.0'

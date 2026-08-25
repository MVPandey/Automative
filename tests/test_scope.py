"""Glob classification."""

import pytest

from automative.scope import Classification, classify, matches


@pytest.mark.parametrize(
    ('path', 'pattern', 'expected'),
    [
        ('src/a.py', 'src/**', True),
        ('src/deep/b.py', 'src/**/*.py', True),
        ('src/a.py', 'src/*.py', True),
        ('src/deep/b.py', 'src/*.py', False),
        ('tests/test_x.py', 'tests/**', True),
        ('tests/test_x.py', 'tests/', True),
        ('tests/test_x.py', 'tests', True),
        ('tests2/x.py', 'tests', False),
        ('bench.py', 'bench.py', True),
        ('./bench.py', 'bench.py', True),
        ('a/bench.py', 'bench.py', False),
        ('a/bench.py', '**/bench.py', True),
        ('train.py', '*.py', True),
        ('a/train.py', '*.py', False),
    ],
)
def test_matches(path: str, pattern: str, expected: bool) -> None:
    assert matches(path, (pattern,)) is expected


def test_protected_wins_over_scope() -> None:
    assert classify('src/tests/t.py', ('src/**',), ('src/tests/**',)) is Classification.PROTECTED
    assert classify('src/a.py', ('src/**',), ('src/tests/**',)) is Classification.IN_SCOPE
    assert classify('README.md', ('src/**',), ()) is Classification.OTHER

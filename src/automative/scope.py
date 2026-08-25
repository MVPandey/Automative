"""Pure classification of project paths against ``scope`` and ``protected`` globs."""

import re
from enum import StrEnum
from functools import cache

__all__ = ['Classification', 'classify', 'matches']


class Classification(StrEnum):
    """Where a path falls relative to the agent's write permissions."""

    IN_SCOPE = 'in_scope'
    PROTECTED = 'protected'
    OTHER = 'other'


@cache
def _compile(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-style glob to a regex over posix relative paths.

    ``**`` matches across directory separators, ``*`` and ``?`` do not. A pattern with a trailing slash or
    no wildcards also matches everything beneath it, so ``tests`` and ``tests/`` both cover ``tests/x.py``.
    """
    text = pattern.strip().lstrip('./')
    if text.endswith('/'):
        text = text.rstrip('/') + '/**'
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if text.startswith('**/', i):
            out.append('(?:.*/)?')
            i += 3
        elif text.startswith('**', i):
            out.append('.*')
            i += 2
        elif char == '*':
            out.append('[^/]*')
            i += 1
        elif char == '?':
            out.append('[^/]')
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    body = ''.join(out)
    has_wildcard = any(c in text for c in '*?[')
    if not has_wildcard:
        body = f'{body}(?:/.*)?'
    return re.compile(f'^{body}$')


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    """Return whether the posix relative ``path`` matches any of ``patterns``."""
    normalized = path.lstrip('./')
    return any(_compile(p).match(normalized) for p in patterns)


def classify(path: str, scope: tuple[str, ...], protected: tuple[str, ...]) -> Classification:
    """Classify ``path``; ``protected`` wins over ``scope`` when both match."""
    if matches(path, protected):
        return Classification.PROTECTED
    if matches(path, scope):
        return Classification.IN_SCOPE
    return Classification.OTHER

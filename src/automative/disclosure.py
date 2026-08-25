"""The harness disclosure card recorded at run start (execution, tool, context, scheduling, observability,
verification, governance: the axes of "Stop Comparing LLM Agents Without Disclosing the Harness")."""

import platform
from typing import Any

from automative import __version__

__all__ = ['HOOK_EVENTS', 'disclosure_card']

HOOK_EVENTS = (
    'PreToolUse',
    'PostToolUse',
    'PostToolBatch',
    'Stop',
    'SessionStart',
    'UserPromptSubmit',
    'ConfigChange',
)


def disclosure_card(
    *, model: str | None, protocol_version: str, metric: dict[str, Any], budget: dict[str, Any], require_hooks: bool
) -> dict[str, Any]:
    """Build the card as plain JSON-serializable data."""
    return {
        'execution': {'automative': __version__, 'python': platform.python_version(), 'os': platform.system()},
        'tool': {
            'model': model,
            'hooks': [
                'PreToolUse',
                'PostToolUse',
                'PostToolBatch',
                'Stop',
                'SessionStart',
                'UserPromptSubmit',
                'ConfigChange',
            ]
            if require_hooks
            else [],
        },
        'context': {'protocol_version': protocol_version, 'recitation': 'session brief <=15 lines every iteration'},
        'scheduling': {'loop': 'greedy hill-climb, one atomic change per try, commit-before-verify', 'budget': budget},
        'observability': {
            'ledger': '.automative/ledger.jsonl',
            'git_notes': 'refs/notes/automative',
            'refs': 'refs/automative/<run>/<iter>',
        },
        'verification': metric,
        'governance': {
            'agent_may_edit': 'scope only',
            'verifier': 'hash-locked, CLI-executed',
            'promotion': 'human --confirm',
        },
    }

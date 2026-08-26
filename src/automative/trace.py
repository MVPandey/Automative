"""The tool trace: every tool call the agent made during a run, as the hooks saw it.

The ledger records decisions. The trace records actions, including the ones that were refused, so a
run can be audited afterwards for anything the hooks did not think to stop. Inputs and responses are
truncated to keep the file bounded; the ledger's shown rows already hold the harness's own output.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from automative.errors import StateError

__all__ = ['INPUT_CHARS', 'RESPONSE_CHARS', 'TraceRow', 'append', 'read']

INPUT_CHARS = 4000
RESPONSE_CHARS = 1500


class TraceRow(BaseModel):
    """One tool call."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    run_id: str
    ts: datetime
    session_id: str | None = None
    tool: str
    input: dict[str, Any] = Field(default_factory=dict)
    response: str | None = None
    denied: bool = False
    reason: str | None = None


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f'... [{len(value) - limit} more chars]'
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, limit) for v in value]
    return value


def append(
    path: Path,
    *,
    run_id: str,
    ts: datetime,
    session_id: str | None,
    tool: str,
    tool_input: object,
    response: object = None,
    denied: bool = False,
    reason: str | None = None,
) -> TraceRow:
    """Append one call; the trace is never rewritten."""
    payload = (
        _clip(tool_input, INPUT_CHARS) if isinstance(tool_input, dict) else {'value': _clip(tool_input, INPUT_CHARS)}
    )
    text = None
    if response is not None:
        text = response if isinstance(response, str) else json.dumps(response, default=str)
        text = _clip(text, RESPONSE_CHARS)
    row = TraceRow(
        run_id=run_id,
        ts=ts,
        session_id=session_id,
        tool=tool,
        input=payload,
        response=text,
        denied=denied,
        reason=reason,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(row.model_dump(mode='json'), separators=(',', ':')) + '\n')
    return row


def read(path: Path, run_id: str | None = None) -> tuple[TraceRow, ...]:
    """Every row, optionally for one run."""
    if not path.is_file():
        return ()
    rows: list[TraceRow] = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = TraceRow.model_validate_json(line)
        except (ValidationError, ValueError) as exc:
            raise StateError(f'Trace {path} line {number} is malformed: {exc}') from exc
        if run_id is None or row.run_id == run_id:
            rows.append(row)
    return tuple(rows)

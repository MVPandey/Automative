"""The hook heartbeat: proof that Claude Code hooks are alive for the session driving a run."""

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = ['Heartbeat', 'age_seconds', 'read_heartbeat', 'write_heartbeat']


class Heartbeat(BaseModel):
    """Last hook activity."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    ts: datetime
    counter: int


def read_heartbeat(path: Path) -> Heartbeat | None:
    """Return the heartbeat or ``None`` if missing/corrupt (corrupt is treated as absent)."""
    if not path.is_file():
        return None
    try:
        return Heartbeat.model_validate_json(path.read_text(encoding='utf-8'))
    except (OSError, ValidationError, ValueError):
        return None


def write_heartbeat(path: Path, session_id: str) -> Heartbeat:
    """Bump the heartbeat for ``session_id``."""
    previous = read_heartbeat(path)
    counter = previous.counter + 1 if previous and previous.session_id == session_id else 1
    beat = Heartbeat(session_id=session_id, ts=datetime.now(UTC), counter=counter)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(beat.model_dump(mode='json')), encoding='utf-8')
    return beat


def age_seconds(beat: Heartbeat | None, at: datetime | None = None) -> float | None:
    """Seconds since the heartbeat, or ``None`` if there is none."""
    if beat is None:
        return None
    return ((at or datetime.now(UTC)) - beat.ts).total_seconds()

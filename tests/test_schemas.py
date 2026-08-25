"""Exported JSON schemas stay in sync with the pydantic models."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from export_schemas import TARGET, schemas  # noqa: E402


def test_schemas_in_sync() -> None:
    for name, schema in schemas().items():
        on_disk = json.loads((TARGET / name).read_text(encoding='utf-8'))
        assert on_disk == schema, f'{name} is stale: run `uv run python scripts/export_schemas.py`'

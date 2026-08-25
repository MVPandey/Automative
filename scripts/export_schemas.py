"""Export JSON Schemas for the on-disk artifacts (ledger rows, state, lock, tasks, spec) from the pydantic models.

Run: `uv run python scripts/export_schemas.py`. The test suite asserts the exported files are in sync.
"""

import json
from pathlib import Path

from pydantic import TypeAdapter

from automative.bench import TaskSpec
from automative.ledger import EventRow, IterationRow, RunEndRow, RunStartRow, ShownRow
from automative.lock import LockFile
from automative.spec import Spec
from automative.state import RunState

TARGET = Path(__file__).resolve().parents[1] / 'src' / 'automative' / 'schemas'


def schemas() -> dict[str, dict[str, object]]:
    """Return ``{filename: schema}``."""
    row_adapter: TypeAdapter[IterationRow | EventRow | RunStartRow | RunEndRow | ShownRow] = TypeAdapter(
        IterationRow | EventRow | RunStartRow | RunEndRow | ShownRow
    )
    return {
        'ledger-row.schema.json': row_adapter.json_schema(),
        'state.schema.json': RunState.model_json_schema(),
        'lock.schema.json': LockFile.model_json_schema(),
        'task.schema.json': TaskSpec.model_json_schema(),
        'spec.schema.json': Spec.model_json_schema(),
    }


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, schema in schemas().items():
        (TARGET / name).write_text(json.dumps(schema, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print(f'wrote {TARGET / name}')


if __name__ == '__main__':
    main()

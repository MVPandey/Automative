#!/bin/sh
# Locate the automative CLI and forward the hook event. Never fails the session: a missing CLI is a no-op.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export AUTOMATIVE_PLUGIN_ROOT="$ROOT"
if [ -x "$ROOT/.venv/bin/automative" ]; then
  exec "$ROOT/.venv/bin/automative" hook "$1"
elif command -v automative >/dev/null 2>&1; then
  exec automative hook "$1"
elif command -v uv >/dev/null 2>&1; then
  exec uv run --project "$ROOT" -q automative hook "$1"
fi
exit 0

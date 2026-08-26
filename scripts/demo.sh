#!/bin/sh
# Copy an example to a scratch directory, initialise git, start an Automative run, and print the next
# step. Usage: scripts/demo.sh [example-dir] [target-dir]   (default example: examples/sortbench)
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
EXAMPLE="${1:-$HERE/examples/sortbench}"
TARGET="${2:-$(mktemp -d -t automative-demo)}"
NAME="$(basename "$EXAMPLE")"
mkdir -p "$TARGET"
cp -R "$EXAMPLE/." "$TARGET/"
cd "$TARGET"
git init -q -b main
git add -A
git -c user.email=demo@automative.local -c user.name=automative-demo commit -qm "$NAME demo baseline"
automative doctor
automative run start --name "$NAME"
echo
echo "Demo ready in $TARGET"
echo "Next: cd \"$TARGET\" && claude   # then type /automative"
echo "Codex/other agents: cd \"$TARGET\" && automative session brief  (set enforcement.require_hooks: false first)"

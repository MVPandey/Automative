#!/bin/sh
# Copy examples/sortbench to a scratch directory, initialise git, start an Automative run, and print the
# next step. Usage: scripts/demo.sh [target-dir]
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-$(mktemp -d -t automative-demo)}"
mkdir -p "$TARGET"
cp -R "$HERE/examples/sortbench/." "$TARGET/"
cd "$TARGET"
git init -q -b main
git add -A
git -c user.email=demo@automative.local -c user.name=automative-demo commit -qm "sortbench demo baseline"
automative doctor
automative run start --name sortbench
echo
echo "Demo ready in $TARGET"
echo "Next: cd \"$TARGET\" && claude   # then type /automative"
echo "Codex/other agents: cd \"$TARGET\" && automative session brief  (set enforcement.require_hooks: false first)"

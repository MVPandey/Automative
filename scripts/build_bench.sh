#!/bin/sh
# Build the benchmark suite: run every example through one headless Claude Code session and freeze the
# finished run as a task. Needs `claude` and `automative` on PATH. Each task costs roughly one run of
# its budget (about $2 and 4 minutes with claude -p). Runs happen in parallel.
#
# Usage: scripts/build_bench.sh [work-dir]
# Held-out assignment is fixed here so the suite always has the three held-out tasks the gate needs.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${1:-$(mktemp -d -t automative-bench)}"
export AUTOMATIVE_PLUGIN_ROOT="$HERE"
PROMPT='Run `automative session brief`, read the pinned protocol file it names, and follow it until the harness says the run is done. Then run `automative run end`. Do not stop early and do not ask questions.'

heldout="articulation_points pagerank stable_matching"
split_for() { case " $heldout " in *" $1 "*) echo heldout ;; *) echo train ;; esac; }

one() {
  name="$1"; example="$2"; dir="$WORK/$name"
  sh "$HERE/scripts/demo.sh" "$example" "$dir" > "$dir.start.log" 2>&1
  ( cd "$dir" && claude -p "$PROMPT" --plugin-dir "$HERE" --permission-mode bypassPermissions \
      --output-format json --max-turns 80 > "$dir.claude.json" 2> "$dir.claude.err" ) || true
  ( cd "$dir" && automative run end > "$dir.end.log" 2>&1 || true
    automative bench freeze --split "$(split_for "$name")" > "$dir.freeze.log" 2>&1 )
  echo "$name: $(tail -1 "$dir.freeze.log")"
}

one sortbench "$HERE/examples/sortbench" &
for t in count_connected_components articulation_points shortest_path_dijkstra minimum_spanning_tree pagerank stable_matching; do
  one "$t" "$HERE/examples/algotune/$t" &
done
wait
echo
automative bench list

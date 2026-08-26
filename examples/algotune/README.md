# AlgoTune-shaped tasks

Six tasks from [AlgoTune](https://github.com/oripress/AlgoTune) (NeurIPS 2025, MIT), rehosted so each
one is a self-contained Automative target with no dependencies. The input and output formats and the
correctness checks follow AlgoTune's task definitions. The reference solvers do not: AlgoTune's use
networkx and scipy, these are plain Python written the obvious way, so the speedup an agent can reach
is larger and comes from algorithms rather than from swapping in a library. `check.py` refuses
imports outside the standard library to keep it that way.

| Task | Reference | Where the speedup is |
|---|---|---|
| `count_connected_components` | BFS that rescans the edge list for every node | adjacency lists, union-find |
| `articulation_points` | remove each node and recount components | Tarjan's low-link DFS |
| `shortest_path_dijkstra` | Dijkstra with a linear scan for the minimum, from every source | a heap; reuse of adjacency |
| `minimum_spanning_tree` | Kruskal with a BFS per edge to test for a cycle | union-find with path compression |
| `pagerank` | power iteration over a dense transition matrix | iterate over edges, not the matrix |
| `stable_matching` | Gale-Shapley with `list.index` rank lookups and `pop(0)` | rank tables, a deque |

Each directory has the same five files: `task.py` (generator, reference, checker; protected),
`solver.py` (the only file in scope; starts by calling the reference, speedup 1.0), `bench.py`
(prints the speedup; protected), `check.py` (the guard; protected), and `AUTOMATIVE.md`.

Run one:

```sh
scripts/demo.sh examples/algotune/pagerank   # or any of the six
```

Build the benchmark suite (runs each task through the loop once and freezes it; needs `claude`):

```sh
scripts/build_bench.sh          # runs all seven in parallel; about $34 and 15 minutes in total
automative bench list
```

What one build produced (Claude Code 2.1, `claude -p`, one run per task, budgets of 8 tries and 12
minutes). The speedups are what the agent reached within budget and become each task's
`known_achievable`, the ceiling the normalized score is measured against:

| Task | Split | Tries | Kept | Speedup reached | Cost |
|---|---|---|---|---|---|
| `count_connected_components` | train | 8 | 4 | 369x | $7.11 |
| `minimum_spanning_tree` | train | 8 | 5 | 608x | $3.87 |
| `shortest_path_dijkstra` | train | 4 | 3 | 7.1x | $5.71 |
| `sortbench` (from `examples/sortbench`) | train | 8 | 4 | 9.885 ms to 0.034 ms | $2.24 |
| `articulation_points` | heldout | 2 | 2 | 111x | $4.93 |
| `pagerank` | heldout | 8 | 5 | 62x | $4.44 |
| `stable_matching` | heldout | 3 | 2 | 3.7x | $5.17 |

Three runs stopped on the 12 minute wall clock rather than on tries, because seven benchmarks were
timing themselves on one laptop at once. Run the script on a quieter machine, or raise `minutes` in
the task's `AUTOMATIVE.md`, if you want every task to use its full try budget.

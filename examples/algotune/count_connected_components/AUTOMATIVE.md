---
automative: 1
protocol: 1.0.0
tags: [python, perf, algotune, graph]
metric:
  name: speedup
  direction: higher
  verify: python bench.py
  guard: [python check.py]
  heldout: null
  timeout_s: 180
  repeats: 3
  min_improvement: "5%"
  target: null
scope: [solver.py]
protected: [task.py, bench.py, check.py]
budget: {iterations: 8, minutes: 12, plateau_patience: 4, max_consecutive_errors: 3, max_denied_tool_calls: 5}
enforcement: {require_hooks: true}
---
# Goal

Make `Solver.solve` count the connected components of an undirected graph as fast as possible. The graph
comes as `{"edges": [(u, v), ...], "num_nodes": n}`; return `{"number_connected_components": k}`.

## Context

`solver.py` is the only file you change. It starts by calling the reference in `task.py`, so the
speedup is 1.0 until you write your own `Solver.solve`. `python bench.py` prints the speedup: the
reference's time divided by yours on 3 problems of size 450, after checking every answer with
`task.is_solution`. `python check.py` is the guard: it runs the solver on other sizes and seeds and
refuses any import outside the standard library. Read `task.py` for the exact input and output format.

The reference does a breadth first search from every unvisited node and rebuilds the neighbour
list by scanning all the edges each time it visits a node. Graphs have 2 to 5 components, each dense
enough to be connected.

## Constraints

- Standard library only. `check.py` rejects other imports.
- Keep `class Solver` and `solve(self, problem)`.
- Do not special-case the benchmark sizes or seeds.

## Out of scope

- Changing the problem definition, the checker, or the timing.

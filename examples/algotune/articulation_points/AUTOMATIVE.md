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

Make `Solver.solve` find every articulation point of an undirected graph as fast as possible. The graph
comes as `{"num_nodes": n, "edges": [[u, v], ...]}`; return `{"articulation_points": sorted_list}`.

## Context

`solver.py` is the only file you change. It starts by calling the reference in `task.py`, so the
speedup is 1.0 until you write your own `Solver.solve`. `python bench.py` prints the speedup: the
reference's time divided by yours on 3 problems of size 320, after checking every answer with
`task.is_solution`. `python check.py` is the guard: it runs the solver on other sizes and seeds and
refuses any import outside the standard library. Read `task.py` for the exact input and output format.

The reference removes each node in turn and recounts the components with a breadth first search,
which is quadratic in the number of nodes times the number of edges. Edge probability is 0.3.

## Constraints

- Standard library only. `check.py` rejects other imports.
- Keep `class Solver` and `solve(self, problem)`.
- Do not special-case the benchmark sizes or seeds.

## Out of scope

- Changing the problem definition, the checker, or the timing.

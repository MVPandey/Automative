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

Make `Solver.solve` return a minimum spanning tree of a weighted undirected graph as fast as possible.
The graph comes as `{"num_nodes": n, "edges": [[u, v, w], ...]}`; return `{"mst_edges": [[u, v, w], ...]}`
with `u < v`, sorted by `(u, v)`.

## Context

`solver.py` is the only file you change. It starts by calling the reference in `task.py`, so the
speedup is 1.0 until you write your own `Solver.solve`. `python bench.py` prints the speedup: the
reference's time divided by yours on 3 problems of size 200, after checking every answer with
`task.is_solution`. `python check.py` is the guard: it runs the solver on other sizes and seeds and
refuses any import outside the standard library. Read `task.py` for the exact input and output format.

The reference is Kruskal without union-find: for every candidate edge it runs a breadth first search
over the edges chosen so far to see whether the endpoints are already connected. Any minimum spanning
tree is accepted; ties in weight are common, so the checker compares total weight, not edge sets.

## Constraints

- Standard library only. `check.py` rejects other imports.
- Keep `class Solver` and `solve(self, problem)`.
- Do not special-case the benchmark sizes or seeds.

## Out of scope

- Changing the problem definition, the checker, or the timing.

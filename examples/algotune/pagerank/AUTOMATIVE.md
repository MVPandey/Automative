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

Make `Solver.solve` compute PageRank scores (damping 0.85, dangling mass spread uniformly) for a
directed graph as fast as possible. Input is `{"adjacency_list": [[j, ...], ...]}`; return
`{"pagerank_scores": [r_0, ..., r_{n-1}]}` summing to 1 and within 1e-6 of the exact fixed point.

## Context

`solver.py` is the only file you change. It starts by calling the reference in `task.py`, so the
speedup is 1.0 until you write your own `Solver.solve`. `python bench.py` prints the speedup: the
reference's time divided by yours on 3 problems of size 400, after checking every answer with
`task.is_solution`. `python check.py` is the guard: it runs the solver on other sizes and seeds and
refuses any import outside the standard library. Read `task.py` for the exact input and output format.

The reference builds the dense n by n transition matrix as a list of lists and multiplies it on every
iteration, so each iteration costs n squared even though the graph has about 8 edges per node. It
iterates until the L1 change is below 1e-10.

## Constraints

- Standard library only. `check.py` rejects other imports.
- Keep `class Solver` and `solve(self, problem)`.
- Do not special-case the benchmark sizes or seeds.

## Out of scope

- Changing the problem definition, the checker, or the timing.

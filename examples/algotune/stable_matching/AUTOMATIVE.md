---
automative: 1
protocol: 1.0.0
tags: [python, perf, algotune, matching]
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

Make `Solver.solve` find a stable matching between n proposers and n receivers as fast as possible.
Input is `{"proposer_prefs": [[...], ...], "receiver_prefs": [[...], ...]}` (full rankings);
return `{"matching": [receiver_for_proposer_0, ...]}`. Any stable matching is accepted.

## Context

`solver.py` is the only file you change. It starts by calling the reference in `task.py`, so the
speedup is 1.0 until you write your own `Solver.solve`. `python bench.py` prints the speedup: the
reference's time divided by yours on 3 problems of size 700, after checking every answer with
`task.is_solution`. `python check.py` is the guard: it runs the solver on other sizes and seeds and
refuses any import outside the standard library. Read `task.py` for the exact input and output format.

The reference is Gale-Shapley written carelessly: it looks up ranks with `list.index` on every
proposal and takes the next free proposer with `pop(0)`. Preferences are uniformly random permutations.

## Constraints

- Standard library only. `check.py` rejects other imports.
- Keep `class Solver` and `solve(self, problem)`.
- Do not special-case the benchmark sizes or seeds.

## Out of scope

- Changing the problem definition, the checker, or the timing.

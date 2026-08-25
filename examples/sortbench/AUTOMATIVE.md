---
automative: 1
protocol: 1.0.0
tags: [python, perf]
metric:
  name: bench_ms
  direction: lower
  verify: python bench.py
  guard: [python -m unittest discover -q -s tests]
  heldout: null
  timeout_s: 120
  repeats: 3
  min_improvement: "2%"
  target: null
scope: [src/slowsort/**/*.py]
protected: [bench.py, tests/**]
budget: {iterations: 8, minutes: 10, plateau_patience: 4, max_consecutive_errors: 3, max_denied_tool_calls: 5}
enforcement: {require_hooks: true}
---
# Goal

Make `dedupe_and_sort()` in `src/slowsort/core.py` as fast as possible on the benchmark input without
changing what it returns. The tests define the behaviour: first occurrence wins, ascending order, any
hashable and orderable values.

## Context

`bench.py` builds a seeded list of 4,000 integers with many duplicates and times `dedupe_and_sort` over
five repetitions. It prints the median in milliseconds. The hot path is all inside `core.py`. Run
`python bench.py` to see the number and `python -m unittest discover -s tests` for the guard.

## Constraints

- Standard library only; no new dependencies.
- Keep the function signature and name.
- Do not special-case the benchmark input.

## Strategy hints

- Profile before guessing: where does the time actually go?
- In code like this, the membership test and the hand written sort are usually the first two levers.

## Out of scope

- Changing what "duplicate" means.

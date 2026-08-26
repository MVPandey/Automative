"""Speedup of solver.py over the reference in task.py. Prints one number: reference time / solver time.

Timing runs the reference and the solver on the same problems in the same process, so the ratio holds
up when the machine is busy. Every solver answer is checked with task.is_solution first; a wrong answer
prints "invalid" and exits 1, which the harness records as a crash, not a score.
"""

import sys
import time

import task
from solver import Solver

N = 700
SEEDS = (1, 2, 3)
BEST_OF = 3


def _best_of(fn, problem):
    best = None
    for _ in range(BEST_OF):
        start = time.perf_counter()
        fn(problem)
        elapsed = time.perf_counter() - start
        best = elapsed if best is None or elapsed < best else best
    return best


def main() -> int:
    problems = [task.generate_problem(N, seed) for seed in SEEDS]
    solver = Solver()
    for problem in problems:
        if not task.is_solution(problem, solver.solve(problem)):
            print("invalid")
            return 1
    ref = sum(_best_of(task.reference, p) for p in problems)
    mine = sum(_best_of(solver.solve, p) for p in problems)
    print(f"{ref / mine:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

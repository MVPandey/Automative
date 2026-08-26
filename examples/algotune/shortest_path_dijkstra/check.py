"""Guard: the solver must be correct on sizes and seeds the benchmark never times, and must not import
anything outside the standard library. Exit 0 on pass."""

import ast
import sys
from pathlib import Path

import task
from solver import Solver

SIZES = (1, 2, 15, 40)
SEEDS = (11, 12, 13)


def stdlib_only() -> bool:
    tree = ast.parse(Path("solver.py").read_text())
    allowed = set(sys.stdlib_module_names) | {"task"}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        for name in names:
            if name not in allowed:
                print(f"solver.py imports {name}, which is not in the standard library")
                return False
    return True


def main() -> int:
    if not stdlib_only():
        return 1
    solver = Solver()
    for n in SIZES:
        for seed in SEEDS:
            problem = task.generate_problem(n, seed)
            if not task.is_solution(problem, solver.solve(problem)):
                print(f"wrong answer for n={n} seed={seed}")
                return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Guard for strategy.py. Exit 0 only if all of these hold:

1. strategy.py imports nothing outside the standard library and never opens a file (so it cannot read
   the held-out data or anything else on disk).
2. Weights are numbers in [0, 1] and sum to at most 1 on every bar.
3. The strategy is causal: the weights it produces for bars before a cut point are identical whether or
   not it is shown the bars after the cut. Checked at several cut points.
4. It runs within a time limit.
"""

import ast
import sys
import time
from pathlib import Path

import backtest
from strategy import Strategy

CUTS = (0.35, 0.6, 0.85)
TIME_LIMIT_S = 60
FORBIDDEN_NAMES = {"open", "exec", "eval", "__import__", "compile"}
FORBIDDEN_MODULES = {"os", "pathlib", "csv", "io", "subprocess", "shutil", "importlib", "pickle", "json", "urllib", "socket", "glob"}


def source_is_clean() -> bool:
    tree = ast.parse(Path("strategy.py").read_text())
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        for name in names:
            if name not in sys.stdlib_module_names or name in FORBIDDEN_MODULES:
                print(f"strategy.py imports {name}, which is not allowed")
                return False
        if isinstance(node, ast.Call):
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if called in FORBIDDEN_NAMES:
                print(f"strategy.py calls {called}(), which is not allowed")
                return False
    return True


def valid_weights(weights, n) -> bool:
    if len(weights) != n:
        print(f"expected {n} weight rows, got {len(weights)}")
        return False
    for i, row in enumerate(weights):
        if not isinstance(row, dict):
            print(f"row {i} is not a dict")
            return False
        total = 0.0
        for coin, w in row.items():
            if not isinstance(w, (int, float)) or w != w or not (0.0 <= w <= 1.0):
                print(f"row {i} weight for {coin} is {w!r}")
                return False
            total += w
        if total > 1.0 + 1e-9:
            print(f"row {i} weights sum to {total:.4f}")
            return False
    return True


def main() -> int:
    if not source_is_clean():
        return 1
    times, closes, volumes = backtest.align(backtest.load("train"))
    n = len(times)
    started = time.perf_counter()
    full = Strategy().weights(closes, volumes)
    if time.perf_counter() - started > TIME_LIMIT_S:
        print("strategy is too slow")
        return 1
    if not valid_weights(full, n):
        return 1
    for frac in CUTS:
        cut = int(n * frac)
        partial = Strategy().weights({c: v[:cut] for c, v in closes.items()}, {c: v[:cut] for c, v in volumes.items()})
        if not valid_weights(partial, cut):
            return 1
        for i in range(cut):
            a, b = full[i], partial[i]
            keys = set(a) | set(b)
            if any(abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) > 1e-12 for k in keys):
                print(f"not causal: weights at bar {i} change when bars after {cut} are hidden")
                return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

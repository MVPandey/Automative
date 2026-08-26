"""Backtest strategy.py on one window and print the net return in percent.

    python backtest.py train      # the number the agent optimizes
    python backtest.py heldout    # the number the harness checks on every keep

Rules, all fixed here and not up for negotiation by the strategy:
- Long only, cash otherwise. Target weights per coin in [0, 1], sum at most 1.
- A weight decided from bar t is held from the close of bar t+1 (one-bar delay): no trading on the
  bar that produced the signal.
- Every unit of turnover costs COST (fee plus slippage). Memecoin books are thin; 0.5% is generous.
- A coin with no data for a bar cannot be held; its weight is treated as cash.
- The held-out run sees the train bars as history so indicators can warm up, but P&L is counted on
  held-out bars only.
"""

import csv
import math
import sys
from pathlib import Path

from strategy import Strategy

COST = 0.005
COINS = ("DOGE", "SHIB", "PEPE", "BONK", "WIF", "DEGEN", "TOSHI")


def load(window: str) -> dict[str, dict[int, tuple[float, float]]]:
    """coin -> {time: (close, volume)}."""
    out = {}
    for coin in COINS:
        path = Path("data") / window / f"{coin}.csv"
        if not path.is_file():
            continue
        with path.open() as handle:
            out[coin] = {int(float(r["time"])): (float(r["close"]), float(r["volume"])) for r in csv.DictReader(handle)}
    return out


def align(series: dict[str, dict[int, tuple[float, float]]]) -> tuple[list[int], dict[str, list], dict[str, list]]:
    times = sorted({t for s in series.values() for t in s})
    closes = {c: [series[c].get(t, (None, None))[0] for t in times] for c in series}
    volumes = {c: [series[c].get(t, (None, None))[1] for t in times] for c in series}
    return times, closes, volumes


def simulate(closes: dict[str, list], weights: list[dict[str, float]], start_index: int) -> dict[str, float]:
    """Equity curve from bar start_index on, applying weights with a one-bar delay and turnover costs."""
    coins = list(closes)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    held: dict[str, float] = {c: 0.0 for c in coins}
    returns: list[float] = []
    turnover = 0.0
    for t in range(start_index, len(weights)):
        # 1. P&L for this bar from the positions held at the previous close
        bar_return = 0.0
        for c in coins:
            prev, cur = closes[c][t - 1], closes[c][t]
            if held[c] > 0 and prev and cur:
                bar_return += held[c] * (cur / prev - 1.0)
        equity *= 1.0 + bar_return
        returns.append(bar_return)
        # 2. Rebalance at this close to the weights decided from the previous bar
        target = weights[t - 1] if t - 1 >= 0 else {}
        new_held = {}
        for c in coins:
            w = float(target.get(c, 0.0))
            if not (0.0 <= w <= 1.0) or closes[c][t] is None:
                w = 0.0
            new_held[c] = w
        if sum(new_held.values()) > 1.0 + 1e-9:
            raise ValueError("weights sum above 1")
        traded = sum(abs(new_held[c] - held[c]) for c in coins)
        turnover += traded
        equity *= 1.0 - COST * traded
        held = new_held
        peak = max(peak, equity)
        max_dd = max(max_dd, 1.0 - equity / peak)
    n = len(returns)
    mean = sum(returns) / n if n else 0.0
    var = sum((r - mean) ** 2 for r in returns) / n if n else 0.0
    sharpe = (mean / math.sqrt(var)) * math.sqrt(24 * 365) if var > 0 else 0.0
    return {"return_pct": (equity - 1.0) * 100.0, "sharpe": sharpe, "max_drawdown_pct": max_dd * 100.0, "turnover": turnover, "bars": n}


def run(window: str) -> dict[str, float]:
    train = load("train")
    if window == "train":
        times, closes, volumes = align(train)
        start = 1
    else:
        held = load("heldout")
        merged = {c: {**train.get(c, {}), **held.get(c, {})} for c in set(train) | set(held)}
        times, closes, volumes = align(merged)
        first_held = min(t for s in held.values() for t in s)
        start = times.index(first_held)
    weights = Strategy().weights(closes, volumes)
    if len(weights) != len(times):
        raise ValueError(f"strategy returned {len(weights)} weight rows for {len(times)} bars")
    return simulate(closes, weights, start)


def main() -> int:
    window = sys.argv[1] if len(sys.argv) > 1 else "train"
    if window not in ("train", "heldout"):
        print("usage: backtest.py train|heldout", file=sys.stderr)
        return 2
    stats = run(window)
    print(
        f"{window}: sharpe {stats['sharpe']:.2f}, max drawdown {stats['max_drawdown_pct']:.1f}%, "
        f"turnover {stats['turnover']:.1f}x over {stats['bars']} bars",
        file=sys.stderr,
    )
    print(f"{stats['return_pct']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

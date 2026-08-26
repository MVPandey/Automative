---
automative: 1
protocol: 1.0.0
tags: [trading, memecoin, backtest, python]
metric:
  name: train_return_pct
  direction: higher
  verify: python backtest.py train
  guard: [python check.py]
  heldout: python backtest.py heldout
  timeout_s: 180
  repeats: 1
  min_improvement: "1"
  target: null
scope: [strategy.py]
protected: [backtest.py, check.py, fetch_data.py, data/**]
budget: {iterations: 12, minutes: 30, plateau_patience: 5, max_consecutive_errors: 3, max_denied_tool_calls: 5}
enforcement: {require_hooks: true}
---
# Goal

Make `Strategy.weights` in `strategy.py` earn as much as possible, net of a 0.5% cost on every unit of
turnover, trading a basket of memecoins (DOGE, SHIB, PEPE, BONK, WIF, DEGEN, TOSHI) on hourly bars,
long only. The metric is the net return in percent over the training window. Every keep is also
checked on a held-out window that follows the training window in time; a change that helps in
training but hurts out of sample is discarded, so build rules that would have worked without knowing
the answer.

## Context

`python backtest.py train` prints the training return; `python backtest.py heldout` prints the held-out
return (the harness runs it for you on keeps). `backtest.py` documents the execution rules: one-bar
delay, weights in [0, 1] summing to at most 1, cash otherwise, no shorting, no leverage. `data/train/`
holds the bars you may look at; the numbers in `data/heldout/` are off limits and the guard rejects a
strategy that opens any file. The baseline holds each coin while its close is above its 72 hour moving
average. Levers that are fair game: trend and breakout rules, volatility filters and position sizing,
holding periods and rebalance frequency (turnover is expensive), cross-coin relative strength, volume
confirmation, drawdown control. Read `data/manifest.json` for how many bars there are.

## Constraints

- Standard library only, no file access, no network. `check.py` enforces this.
- Causal: weights for bar t may use bars 0..t only. `check.py` re-runs the strategy on truncated
  history and rejects any difference.
- No rules keyed on specific bar indices, dates, or a single coin's known history. Rules must be
  expressed in terms of the price and volume series themselves.
- Keep the class name and the method signature.

## Out of scope

- Changing costs, the coin list, the windows, or the execution rules.

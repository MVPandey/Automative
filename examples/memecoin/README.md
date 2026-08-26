# Memecoin strategy against a backtest

A trading task in the Automative shape. The agent edits `strategy.py`, the only file in scope, and the
metric is the net return in percent on a training window of hourly Coinbase bars for seven memecoins
(DOGE, SHIB, PEPE, BONK, WIF, DEGEN, TOSHI), long only, with a one-bar execution delay and a 0.5% cost
on every unit of turnover. The harness runs the held-out window (the 36 days after the training
window) on every keep and discards a change that regresses it. `check.py` is the guard: standard
library only, no file access, weights in range, and a causality test that re-runs the strategy on
truncated history and rejects any difference. No money moves; nothing here places orders.

```sh
python3 fetch_data.py --days 120 --heldout-days 36   # writes data/train and data/heldout
python3 backtest.py train; python3 backtest.py heldout; python3 check.py
scripts/demo.sh examples/memecoin                      # then /automative, or claude -p
```

The data is not committed; each fetch is the latest 120 days, so the numbers below will not repeat
exactly.

## One run, 2026-08-26

Data fetched 2026-08-26 06:00 UTC; training window 2026-04-28 to 2026-07-21, held out to 2026-08-26.
Budget 12 tries and 30 minutes; the run stopped on the wall clock after 8 tries. Claude Code 2.1,
36 turns, $8.73.

| Try | Decision | Train | Held out | Change |
|---|---|---|---|---|
| baseline | | -89.7% | -59.5% | hold while close is above the 72h SMA |
| 1 | keep | -1.2% | +14.4% | 240h SMA trend with 2% hysteresis; retrade only on a 0.2 weight move |
| 2 | discard (held out) | +0.7% | +4.7% | scale new positions in over 12 bars |
| 3 | keep | +1.4% | +16.5% | entry needs 24h volume at least 0.7x the 10 day average |
| 4 | discard (held out) | +4.5% | +16.4% | block re-entry for 96 bars after an exit |
| 5 | discard (held out) | +3.2% | +14.3% | weight by trend strength over realized volatility |
| 6 | keep | +6.6% | +16.9% | per-coin weight cap 0.5 to 0.7 |
| 7 | discard (held out) | +11.9% | +16.5% | minimum 72 bar hold after entry |
| 8 | discard (held out) | +8.3% | +16.3% | retrade threshold 0.2 to 0.25 |

Equal-weight buy and hold over the same windows: -17.3% train, +4.2% held out.

What the numbers say, and what they do not:

- The baseline lost 90% because it traded 451 times and paid 0.5% each way. The first keep cut
  turnover to 20x and did most of the work. Everything after it is a few points.
- Five of eight tries improved the training return, two of them by a lot, and every one of the five
  was discarded because the held-out return fell. That is the held-out check doing its job: the
  changes that fit the training window best were the ones that did not carry.
- The held-out number is not clean after this. The agent could see each try's held-out score in the
  ledger, so eight tries are eight selections on the held-out window. The cleanest single number is
  try 1's +14.4%, made before any selection happened. The final +16.9% should be read as "somewhere
  above buy and hold on one 36 day window", not as an expected return.
- 36 days of one basket is one sample of one regime. The rule that survived (slow trend with
  hysteresis, low turnover, volume confirmation) is a conventional shape rather than an exotic fit,
  which is mildly reassuring and no more than that. The next step, if any, is walk-forward: refetch,
  roll the windows, and see whether the same rule survives windows it was never tuned near.

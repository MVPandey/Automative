"""The file the agent edits.

Strategy.weights receives, for every coin, the full list of hourly closes and volumes (oldest first;
None where the coin has no bar) and returns one dict per bar: target portfolio weight per coin in
[0, 1], summing to at most 1. Anything not allocated is cash. The weight decided at bar t takes effect
at the close of bar t+1 and every unit of turnover costs 0.5%, so trade when it is worth it.

The baseline is the simplest thing that is not buy-and-hold: hold a coin, equal weight with the
others that qualify, while its close is above its 72 hour simple moving average.
"""

FAST = 72


def _sma(values, window):
    out = []
    total = 0.0
    count = 0
    buffer = []
    for v in values:
        buffer.append(v)
        if v is not None:
            total += v
            count += 1
        if len(buffer) > window:
            old = buffer.pop(0)
            if old is not None:
                total -= old
                count -= 1
        out.append(total / count if count == window else None)
    return out


class Strategy:
    def weights(self, closes, volumes):
        coins = list(closes)
        n = len(next(iter(closes.values()))) if closes else 0
        smas = {c: _sma(closes[c], FAST) for c in coins}
        rows = []
        for t in range(n):
            longs = [c for c in coins if closes[c][t] is not None and smas[c][t] is not None and closes[c][t] > smas[c][t]]
            rows.append({c: 1.0 / len(longs) for c in longs} if longs else {})
        return rows

"""Download hourly candles for the memecoins Coinbase quotes in USD, and split them into train and
held-out windows. Standard library only. Run once before `automative run start`:

    python fetch_data.py --days 120 --heldout-days 36

Writes data/train/<COIN>.csv and data/heldout/<COIN>.csv (time,open,high,low,close,volume, oldest
first) and data/manifest.json. Nothing here is read by the strategy; backtest.py loads the window it is
asked for.
"""

import argparse
import csv
import json
import ssl
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

COINS = ("DOGE", "SHIB", "PEPE", "BONK", "WIF", "DEGEN", "TOSHI")
API = "https://api.exchange.coinbase.com/products/{coin}-USD/candles"
GRANULARITY = 3600
PAGE = 300  # candles per request, Coinbase's maximum


def tls_context() -> ssl.SSLContext:
    """A context that verifies certificates even on python.org builds that ship without root CAs."""
    try:
        import certifi  # noqa: PLC0415 - optional

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    system_bundle = Path("/etc/ssl/cert.pem")
    if system_bundle.is_file():
        return ssl.create_default_context(cafile=str(system_bundle))
    return ssl.create_default_context()


def fetch(coin: str, start: datetime, end: datetime, context: ssl.SSLContext) -> list[list[float]]:
    rows: dict[int, list[float]] = {}
    cursor = start
    while cursor < end:
        page_end = min(cursor + timedelta(seconds=GRANULARITY * PAGE), end)
        url = f"{API.format(coin=coin)}?granularity={GRANULARITY}&start={cursor.isoformat()}&end={page_end.isoformat()}"
        req = urllib.request.Request(url, headers={"User-Agent": "automative-memecoin-example"})
        with urllib.request.urlopen(req, timeout=30, context=context) as resp:
            for t, low, high, open_, close, volume in json.load(resp):
                rows[int(t)] = [float(open_), float(high), float(low), float(close), float(volume)]
        cursor = page_end
        time.sleep(0.15)
    return [[t, *rows[t]] for t in sorted(rows)]


def write(path: Path, rows: list[list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "open", "high", "low", "close", "volume"])
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--heldout-days", type=int, default=36)
    args = parser.parse_args()
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=args.days)
    cut = end - timedelta(days=args.heldout_days)
    context = tls_context()
    manifest = {"fetched_at": end.isoformat(), "granularity_s": GRANULARITY, "train_end": cut.isoformat(), "coins": {}}
    for coin in COINS:
        rows = fetch(coin, start, end, context)
        train = [r for r in rows if r[0] < cut.timestamp()]
        held = [r for r in rows if r[0] >= cut.timestamp()]
        write(Path("data/train") / f"{coin}.csv", train)
        write(Path("data/heldout") / f"{coin}.csv", held)
        manifest["coins"][coin] = {"train_rows": len(train), "heldout_rows": len(held)}
        print(f"{coin}: {len(train)} train bars, {len(held)} held-out bars", file=sys.stderr)
    Path("data/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

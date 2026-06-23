"""One-off: fetch ETH's full available Bybit history (perp linear ETHUSDT),
mirroring the existing btc_long_{5m,15m,1h}.json backfill, for multi-year
backtests beyond the current ~1y eth_{5m,15m,1h}.json window.

Bybit ETHUSDT linear perp listing predates our current data window by several
years (BTCUSDT goes back to 2020-04 per btc_long_1h.json) — this asks for
4 years and lets fetch_klines naturally stop wherever Bybit's history starts.

Run (from repo root, needs pybit + network):
    docker run --rm --platform linux/arm64 -v "$PWD/data:/data" -e PYTHONPATH=/app \
        -v "$PWD/backend:/app" -w /app opt-app-bt:arm64 python3 services/fetch_eth_long.py
Writes data/eth_long_{5m,15m,1h}.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")
from services.backtest_data import fetch_klines

OUT = Path("/app/data")  # container layout — matches multifetch.py's convention
SYMBOL = "ETHUSDT"
YEARS_BACK = 4
IVMAP = {"5": "5m", "15": "15m", "60": "1h"}


def main():
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(YEARS_BACK * 365.25 * 86_400_000)
    for iv, label in IVMAP.items():
        print(f"[fetch_eth_long] fetching ETHUSDT {label} back to "
              f"{YEARS_BACK}y ago...", flush=True)
        candles = fetch_klines(SYMBOL, iv, start_ms, end_ms)  # type: ignore[arg-type]
        out_path = OUT / f"eth_long_{label}.json"
        out_path.write_text(json.dumps(candles))
        first_ts = candles[0]["start_ms"] if candles else None
        last_ts = candles[-1]["start_ms"] if candles else None
        print(f"[fetch_eth_long] wrote {out_path.name}: {len(candles)} candles "
              f"({first_ts} .. {last_ts})", flush=True)
    print("[fetch_eth_long] DONE", flush=True)


if __name__ == "__main__":
    main()

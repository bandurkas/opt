"""One-off: fetch 365d klines for extra coins (BTC/SOL/XRP) on the VPS.

Run inside the backend container (has pybit + Bybit reachability):
    docker exec opt-app-backend-1 python3 /app/services/multifetch.py
Writes data/{btc,sol,xrp}_{5m,15m,1h}.json under /app/data.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")
from services.backtest_data import fetch_set

OUT = Path("/app/data")
OUT.mkdir(exist_ok=True)
IVMAP = {"5": "5m", "15": "15m", "60": "1h"}
COINS = {"xaut": "XAUTUSDT"}  # gold (Tether Gold) — linear perp, listed ~2025-06

for prefix, sym in COINS.items():
    s = fetch_set(sym, days=365, intervals=("5", "15", "60"))
    for iv, candles in s.items():
        p = OUT / f"{prefix}_{IVMAP[iv]}.json"
        p.write_text(json.dumps(candles))
        print(f"[multifetch] wrote {p.name}: {len(candles)} candles", flush=True)
print("[multifetch] DONE", flush=True)

"""Background poller — runs as a separate docker service.

Every POLL_INTERVAL seconds:
  - fetch spot ETHUSDT
  - upsert last few candles on 5m / 15m / 1h
  - snapshot ATM ±N% options
  - once an hour: cleanup old data
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

# Ensure backend/ is on sys.path when running as a script via Docker CMD.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.engine import apply_schema, get_conn, put_conn  # noqa: E402
from db.repository import (  # noqa: E402
    cleanup_old,
    insert_option_snapshots,
    upsert_klines,
)
from services.backtest_data import fetch_set  # noqa: E402
from services.bybit_client import bybit_client  # noqa: E402


POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))
ATM_PCT = float(os.getenv("ATM_PCT", "8"))
BASE_COIN = os.getenv("POLLER_BASE_COIN", "ETH")
SPOT_SYMBOL = f"{BASE_COIN}USDT"

# Bybit kline `interval` values mapped to our DB labels.
TIMEFRAMES = [("5", "5m"), ("15", "15m"), ("60", "1h")]


async def tick() -> dict:
    """One poll iteration. Returns a stats dict for logging."""
    t0 = time.time()
    spot = bybit_client.get_spot_price(SPOT_SYMBOL)
    if spot <= 0:
        return {"error": "spot=0", "elapsed": time.time() - t0}

    kl_count = 0
    for bb_interval, label in TIMEFRAMES:
        # Always fetch a few extra so a newly-closed candle replaces the in-progress row.
        candles = bybit_client.get_klines(SPOT_SYMBOL, bb_interval, limit=5)
        kl_count += upsert_klines(SPOT_SYMBOL, label, candles)

    chain = bybit_client.get_options_tickers(BASE_COIN)
    atm = [o for o in chain if abs(o["strike"] - spot) / spot * 100 <= ATM_PCT]
    snap_count = insert_option_snapshots(atm, int(time.time() * 1000))

    return {
        "spot": spot,
        "klines_upserted": kl_count,
        "chain_size": len(chain),
        "atm_inserted": snap_count,
        "elapsed": round(time.time() - t0, 2),
    }


def backfill_if_needed(min_5m_candles: int = 500, days: int = 30) -> None:
    """One-shot: if klines table doesn't have enough 5m history, pull `days` from Bybit."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM klines WHERE symbol = %s AND interval = %s",
                (SPOT_SYMBOL, "5m"),
            )
            count = int(cur.fetchone()[0])
    finally:
        put_conn(conn)

    if count >= min_5m_candles:
        print(f"[poller] backfill skipped (klines 5m count={count} >= {min_5m_candles})", flush=True)
        return

    print(f"[poller] backfilling {days} days of klines (have only {count} 5m candles)...", flush=True)
    data = fetch_set(SPOT_SYMBOL, days=days, intervals=("5", "15", "60"))
    label_map = {"5": "5m", "15": "15m", "60": "1h"}
    total = 0
    for bb_interval, label in label_map.items():
        candles = data.get(bb_interval, [])
        total += upsert_klines(SPOT_SYMBOL, label, candles)
        print(f"[poller]   upserted {len(candles)} {label} candles", flush=True)
    print(f"[poller] backfill done ({total} rows)", flush=True)


async def loop():
    apply_schema()
    print(f"[poller] schema ready, polling every {POLL_INTERVAL}s, ATM±{ATM_PCT}%", flush=True)
    try:
        backfill_if_needed()
    except Exception as e:  # noqa: BLE001
        print(f"[poller] backfill error: {e!r}", flush=True)

    last_cleanup = 0.0
    while True:
        try:
            stats = await tick()
            print(f"[poller] tick: {stats}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[poller] error: {e!r}", flush=True)

        now = time.time()
        if now - last_cleanup > 3600:
            try:
                deleted = cleanup_old()
                print(f"[poller] cleanup: {deleted}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[poller] cleanup error: {e!r}", flush=True)
            last_cleanup = now

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(loop())

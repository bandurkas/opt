from __future__ import annotations

import json
import time
from typing import Any, Iterable

from psycopg2.extras import execute_values, RealDictCursor

from .engine import get_conn, put_conn


# ───────────────────────── Klines ─────────────────────────

def upsert_klines(symbol: str, interval: str, candles: list[dict]) -> int:
    if not candles:
        return 0
    rows = [
        (symbol, interval, c["start_ms"], c["open"], c["high"], c["low"], c["close"], c["volume"])
        for c in candles
    ]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO klines (symbol, interval, start_ms, open, high, low, close, volume)
                VALUES %s
                ON CONFLICT (symbol, interval, start_ms) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume
                """,
                rows,
            )
        conn.commit()
        return len(rows)
    finally:
        put_conn(conn)


def recent_klines(symbol: str, interval: str, limit: int = 200) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT start_ms, open, high, low, close, volume
                  FROM klines
                 WHERE symbol = %s AND interval = %s
                 ORDER BY start_ms DESC
                 LIMIT %s
                """,
                (symbol, interval, limit),
            )
            rows = cur.fetchall()
    finally:
        put_conn(conn)
    out = []
    for r in reversed(rows):
        out.append({
            "start_ms": int(r["start_ms"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"]),
        })
    return out


# ───────────────────────── Option snapshots ─────────────────────────

def insert_option_snapshots(items: list[dict], ts_ms: int) -> int:
    if not items:
        return 0
    rows = [
        (
            it["symbol"], ts_ms, it["base_coin"], it["side"],
            it["strike"], it["expiry_ms"],
            it["bid"] or None, it["ask"] or None, it["mark_price"] or None,
            it["mark_iv"] or None, it["delta"] or None, it["gamma"] or None,
            it["vega"] or None, it["theta"] or None,
            it["open_interest"] or None, it["volume_24h"] or None,
            it["underlying_price"] or None,
        )
        for it in items
    ]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO option_snapshots (
                    symbol, ts_ms, base_coin, side, strike, expiry_ms,
                    bid, ask, mark_price, mark_iv,
                    delta, gamma, vega, theta,
                    open_interest, volume_24h, underlying_price
                ) VALUES %s
                ON CONFLICT (symbol, ts_ms) DO NOTHING
                """,
                rows,
            )
        conn.commit()
        return len(rows)
    finally:
        put_conn(conn)


def iv_history(symbol: str, hours: int) -> list[tuple[int, float]]:
    """Return [(ts_ms, mark_iv), ...] for the last N hours, oldest first."""
    cutoff_ms = int(time.time() * 1000) - hours * 3_600_000
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ts_ms, mark_iv FROM option_snapshots
                 WHERE symbol = %s AND ts_ms >= %s AND mark_iv IS NOT NULL
                 ORDER BY ts_ms ASC
                """,
                (symbol, cutoff_ms),
            )
            return [(int(r[0]), float(r[1])) for r in cur.fetchall()]
    finally:
        put_conn(conn)


def latest_snapshot_age_seconds(symbol: str | None = None) -> float | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if symbol:
                cur.execute(
                    "SELECT MAX(ts_ms) FROM option_snapshots WHERE symbol = %s",
                    (symbol,),
                )
            else:
                cur.execute("SELECT MAX(ts_ms) FROM option_snapshots")
            row = cur.fetchone()
    finally:
        put_conn(conn)
    if not row or row[0] is None:
        return None
    return (time.time() * 1000 - row[0]) / 1000


# ───────────────────────── Signals ─────────────────────────

def persist_signal(
    *, generated_at_ms: int, symbol: str, side: str, strike: float | None,
    expiry_ms: int | None, score: float, signal_type: str, payload: dict,
) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO signals (
                    generated_at_ms, symbol, side, strike, expiry_ms,
                    score, signal_type, payload
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (
                    generated_at_ms, symbol, side, strike, expiry_ms,
                    score, signal_type, json.dumps(payload),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    finally:
        put_conn(conn)


def recent_signals(limit: int = 50) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM signals ORDER BY generated_at_ms DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)


# ───────────────────────── Housekeeping ─────────────────────────

def cleanup_old(klines_days: int = 30, snapshots_days: int = 7) -> dict:
    cutoff_klines = int(time.time() * 1000) - klines_days * 86_400_000
    cutoff_snaps = int(time.time() * 1000) - snapshots_days * 86_400_000
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM klines WHERE start_ms < %s", (cutoff_klines,))
            kl_deleted = cur.rowcount
            cur.execute("DELETE FROM option_snapshots WHERE ts_ms < %s", (cutoff_snaps,))
            sn_deleted = cur.rowcount
        conn.commit()
        return {"klines_deleted": kl_deleted, "snapshots_deleted": sn_deleted}
    finally:
        put_conn(conn)

"""BTC straddle repository — btc_straddle_positions, btc_straddle_equity_snapshots,
btc_straddle_state. Same function surface as paper_repo.py (by design — reconcile.py
and live_safety wiring are written against that surface, so this module is a
drop-in for the BTC loop without any bespoke glue)."""
from __future__ import annotations

import json
import time
from typing import Any

from psycopg2.extras import RealDictCursor

from .engine import get_conn, put_conn


# ───────────────────────── State (singleton) ─────────────────────────

def ensure_state(start_equity_usd: float) -> dict:
    """Get or create the singleton btc_straddle_state row."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM btc_straddle_state WHERE id = 1")
            row = cur.fetchone()
            if row:
                return dict(row)
            now_ms = int(time.time() * 1000)
            cur.execute(
                """
                INSERT INTO btc_straddle_state (id, started_at_ms, start_equity_usd)
                VALUES (1, %s, %s)
                RETURNING *
                """,
                (now_ms, start_equity_usd),
            )
            conn.commit()
            return dict(cur.fetchone())
    finally:
        put_conn(conn)


def update_state(*, last_cycle_id: int | None = None,
                 cb_cooldown_until_ms: int | None = None,
                 consec_losses: int | None = None,
                 recent_pnls: list[float] | None = None) -> None:
    fields = []
    values: list[Any] = []
    if last_cycle_id is not None:
        fields.append("last_cycle_id = %s")
        values.append(last_cycle_id)
    if cb_cooldown_until_ms is not None:
        fields.append("cb_cooldown_until_ms = %s")
        values.append(cb_cooldown_until_ms)
    if consec_losses is not None:
        fields.append("consec_losses = %s")
        values.append(consec_losses)
    if recent_pnls is not None:
        fields.append("recent_pnls_json = %s::jsonb")
        values.append(json.dumps(recent_pnls))
    if not fields:
        return
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE btc_straddle_state SET {', '.join(fields)} WHERE id = 1",
                values,
            )
        conn.commit()
    finally:
        put_conn(conn)


def get_state() -> dict | None:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM btc_straddle_state WHERE id = 1")
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_conn(conn)


# ───────────────────────── Positions ─────────────────────────

def open_position(*, cycle_id: int, leg: str, opened_at_ms: int,
                  underlying_at_open: float, strike: float, expiry_ms: int,
                  contracts: float, size_usd: float,
                  entry_credit_usd: float, entry_credit_pct: float,
                  entry_source: str, margin_per_lot_usd: float,
                  sl_dollar_trip_usd: float,
                  signal_payload: dict | None = None) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO btc_straddle_positions (
                    cycle_id, leg, opened_at_ms, underlying_at_open, strike, expiry_ms,
                    contracts, size_usd, entry_credit_usd, entry_credit_pct,
                    entry_source, margin_per_lot_usd, sl_dollar_trip_usd,
                    signal_payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                RETURNING id
                """,
                (
                    cycle_id, leg, opened_at_ms, underlying_at_open, strike, expiry_ms,
                    contracts, size_usd, entry_credit_usd, entry_credit_pct,
                    entry_source, margin_per_lot_usd, sl_dollar_trip_usd,
                    json.dumps(signal_payload or {}),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return int(row[0]) if row else 0
    finally:
        put_conn(conn)


def close_position(position_id: int, *, closed_at_ms: int,
                   exit_debit_usd: float, pnl_pct: float, pnl_usd: float,
                   exit_reason: str) -> int:
    """Close a position. Returns rows_affected (0 if already closed)."""
    status_map = {
        "tp2": "closed_tp2",
        "quick_tp": "closed_tp2",     # combined pair-level take-profit — same bucket as tp2
        "sl": "closed_sl",
        "sl_paired": "closed_sl",     # other leg force-closed alongside a tripped sibling
        "time_stop": "closed_time",
        "reconciled": "closed_reconciled",
    }
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # ⚠️ psycopg2 gotcha (matches paper_repo.close_position): every literal
            # '%' in this parameterized SQL must be doubled ('%%').
            cur.execute(
                """
                UPDATE btc_straddle_positions SET
                  status = %s,
                  closed_at_ms = %s,
                  exit_debit_usd = %s,
                  pnl_pct = %s,
                  pnl_usd = %s,
                  exit_reason = %s
                WHERE id = %s AND status NOT LIKE 'closed_%%'
                """,
                (
                    status_map.get(exit_reason, "closed_time"),
                    closed_at_ms, exit_debit_usd, pnl_pct, pnl_usd,
                    exit_reason, position_id,
                ),
            )
            conn.commit()
            return cur.rowcount
    finally:
        put_conn(conn)


def open_positions() -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM btc_straddle_positions
                 WHERE status = 'open'
                 ORDER BY opened_at_ms DESC
                """,
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)


def recent_positions(limit: int = 50) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM btc_straddle_positions ORDER BY opened_at_ms DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)


def position_stats() -> dict:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                  COUNT(*) FILTER (WHERE status LIKE 'closed_%')                   AS n_closed,
                  COUNT(*) FILTER (WHERE status = 'open')                          AS n_open,
                  COUNT(*) FILTER (WHERE status LIKE 'closed_%' AND pnl_pct > 0)   AS wins,
                  COUNT(*) FILTER (WHERE status LIKE 'closed_%' AND pnl_pct <= 0)  AS losses,
                  COALESCE(SUM(pnl_usd) FILTER (WHERE status LIKE 'closed_%'), 0)  AS realized_usd,
                  COALESCE(AVG(pnl_pct) FILTER (WHERE status LIKE 'closed_%'), 0)  AS avg_pnl_pct
                FROM btc_straddle_positions
                """
            )
            row = dict(cur.fetchone())
            for k in row:
                if hasattr(row[k], "__float__"):
                    row[k] = float(row[k]) if k in ("realized_usd", "avg_pnl_pct") else int(row[k])
            return row
    finally:
        put_conn(conn)


# ───────────────────────── Equity snapshots ─────────────────────────

def insert_equity_snapshot(*, ts_ms: int, equity_usd: float,
                           realized_usd: float, unrealized_usd: float,
                           n_open: int, n_closed: int,
                           max_dd_pct: float | None = None) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO btc_straddle_equity_snapshots
                  (ts_ms, equity_usd, realized_usd, unrealized_usd, n_open, n_closed, max_dd_pct)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ts_ms) DO UPDATE SET
                  equity_usd = EXCLUDED.equity_usd,
                  realized_usd = EXCLUDED.realized_usd,
                  unrealized_usd = EXCLUDED.unrealized_usd,
                  n_open = EXCLUDED.n_open,
                  n_closed = EXCLUDED.n_closed,
                  max_dd_pct = EXCLUDED.max_dd_pct
                """,
                (ts_ms, equity_usd, realized_usd, unrealized_usd,
                 n_open, n_closed, max_dd_pct),
            )
        conn.commit()
    finally:
        put_conn(conn)


def equity_history(hours: int = 168) -> list[dict]:
    cutoff = int(time.time() * 1000) - hours * 3_600_000
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ts_ms, equity_usd, realized_usd, unrealized_usd, n_open, n_closed, max_dd_pct
                  FROM btc_straddle_equity_snapshots
                 WHERE ts_ms >= %s
                 ORDER BY ts_ms ASC
                """,
                (cutoff,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)


def latest_equity() -> dict | None:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM btc_straddle_equity_snapshots ORDER BY ts_ms DESC LIMIT 1"
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_conn(conn)


def peak_equity_since(ts_ms_floor: int) -> float | None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(equity_usd) FROM btc_straddle_equity_snapshots WHERE ts_ms >= %s",
                (ts_ms_floor,),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return float(row[0])
            return None
    finally:
        put_conn(conn)


def realized_pnl_since(ts_ms: int) -> float:
    """Signed sum of realized pnl_usd for positions closed at/after ts_ms. Used
    by the live daily-loss-limit gate, same as paper_repo.realized_pnl_since."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(pnl_usd), 0) FROM btc_straddle_positions "
                "WHERE status ~ '^closed' AND closed_at_ms >= %s",
                (ts_ms,),
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0
    finally:
        put_conn(conn)


def exit_reason_counts() -> dict[str, int]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(exit_reason, 'unknown') AS r, COUNT(*) AS c
                  FROM btc_straddle_positions
                 WHERE status LIKE 'closed_%'
                 GROUP BY exit_reason
                """,
            )
            return {row[0]: int(row[1]) for row in cur.fetchall()}
    finally:
        put_conn(conn)

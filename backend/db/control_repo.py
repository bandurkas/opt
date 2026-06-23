"""Mission Control: per-bot pause flag + close-all command, both DB-backed so
the dashboard (running in the `backend` API container) can signal the trading
loops (separate containers) without any direct process coupling — each loop
polls its own row once per tick, same pattern as the existing CB-cooldown /
state singletons.
"""
from __future__ import annotations

import time

from psycopg2.extras import RealDictCursor

from .engine import get_conn, put_conn

BOT_NAMES = ("eth_signal", "btc_straddle", "eth_straddle")


def _row(bot_name: str) -> dict:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM bot_control WHERE bot_name = %s", (bot_name,))
            row = cur.fetchone()
            if row:
                return dict(row)
            now_ms = int(time.time() * 1000)
            cur.execute(
                """
                INSERT INTO bot_control (bot_name, paused, close_all_requested, updated_at_ms)
                VALUES (%s, false, false, %s)
                ON CONFLICT (bot_name) DO NOTHING
                RETURNING *
                """,
                (bot_name, now_ms),
            )
            conn.commit()
            row = cur.fetchone()
            if row:
                return dict(row)
            cur.execute("SELECT * FROM bot_control WHERE bot_name = %s", (bot_name,))
            return dict(cur.fetchone())
    finally:
        put_conn(conn)


def is_paused(bot_name: str) -> bool:
    return bool(_row(bot_name)["paused"])


def is_close_all_requested(bot_name: str) -> bool:
    return bool(_row(bot_name)["close_all_requested"])


def set_paused(bot_name: str, paused: bool, *, by: str = "dashboard") -> dict:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            now_ms = int(time.time() * 1000)
            cur.execute(
                """
                INSERT INTO bot_control (bot_name, paused, close_all_requested, updated_at_ms, updated_by)
                VALUES (%s, %s, false, %s, %s)
                ON CONFLICT (bot_name) DO UPDATE
                    SET paused = EXCLUDED.paused,
                        updated_at_ms = EXCLUDED.updated_at_ms,
                        updated_by = EXCLUDED.updated_by
                RETURNING *
                """,
                (bot_name, paused, now_ms, by),
            )
            conn.commit()
            return dict(cur.fetchone())
    finally:
        put_conn(conn)


def request_close_all(bot_name: str, *, by: str = "dashboard") -> dict:
    """Flags the bot for an emergency flatten on its next tick. Also pauses it
    (the loop clears close_all_requested itself once done; paused stays true
    until the user explicitly resumes — prevents instant re-entry)."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            now_ms = int(time.time() * 1000)
            cur.execute(
                """
                INSERT INTO bot_control (bot_name, paused, close_all_requested, updated_at_ms, updated_by)
                VALUES (%s, true, true, %s, %s)
                ON CONFLICT (bot_name) DO UPDATE
                    SET paused = true,
                        close_all_requested = true,
                        updated_at_ms = EXCLUDED.updated_at_ms,
                        updated_by = EXCLUDED.updated_by
                RETURNING *
                """,
                (bot_name, now_ms, by),
            )
            conn.commit()
            return dict(cur.fetchone())
    finally:
        put_conn(conn)


def clear_close_all_requested(bot_name: str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bot_control SET close_all_requested = false WHERE bot_name = %s",
                (bot_name,),
            )
            conn.commit()
    finally:
        put_conn(conn)


def status_all() -> list[dict]:
    return [_row(name) for name in BOT_NAMES]

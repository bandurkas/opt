"""Exchange accounts + encrypted credentials. Three accounts exist — ONE
Bybit account per bot, each with its own key and its own wallet balance (no
shared-capital-split bookkeeping needed, unlike a single account split across
strategies): 'eth_signal' (ETH signal bot), 'btc_straddle' (BTC 24h straddle),
'eth_straddle' (ETH 24h straddle). Names mirror db.control_repo.BOT_NAMES —
same bot, same identity, used as the join key between pause/close-all state
and which Bybit credentials that bot's process authenticates with."""
from __future__ import annotations

import time

from psycopg2.extras import RealDictCursor

from .engine import get_conn, put_conn

# Mirrors db.control_repo.BOT_NAMES — not imported from there to keep this
# module importable standalone (control_repo pulls in no extra deps either,
# but the two lists are conceptually independent: this is "which Bybit
# account", that is "which bot's pause/close-all flag").
ACCOUNT_NAMES = ("eth_signal", "btc_straddle", "eth_straddle")

DEFAULT_ACCOUNT_NAME = "default"  # last-resort fallback if MC_ACCOUNT_NAME is unset


def ensure_account(name: str) -> dict:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM accounts WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                return dict(row)
            cur.execute(
                """
                INSERT INTO accounts (name, exchange, is_active, created_at_ms)
                VALUES (%s, 'bybit', true, %s)
                ON CONFLICT (name) DO NOTHING
                RETURNING *
                """,
                (name, int(time.time() * 1000)),
            )
            conn.commit()
            row = cur.fetchone()
            if row:
                return dict(row)
            cur.execute("SELECT * FROM accounts WHERE name = %s", (name,))
            return dict(cur.fetchone())
    finally:
        put_conn(conn)


def ensure_all_bot_accounts() -> list[dict]:
    """Pre-seed the 3 bot accounts so the settings UI always has 3 slots to
    show, even before any key has ever been entered."""
    return [ensure_account(name) for name in ACCOUNT_NAMES]


def list_accounts() -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM accounts ORDER BY id")
            return [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)


def get_credentials_row(account_id: int) -> dict | None:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM exchange_credentials WHERE account_id = %s", (account_id,)
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_conn(conn)


def upsert_credentials(account_id: int, *, api_key_encrypted: str, api_secret_encrypted: str) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO exchange_credentials (account_id, api_key_encrypted, api_secret_encrypted, updated_at_ms)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (account_id) DO UPDATE
                    SET api_key_encrypted = EXCLUDED.api_key_encrypted,
                        api_secret_encrypted = EXCLUDED.api_secret_encrypted,
                        updated_at_ms = EXCLUDED.updated_at_ms
                """,
                (account_id, api_key_encrypted, api_secret_encrypted, int(time.time() * 1000)),
            )
            conn.commit()
    finally:
        put_conn(conn)

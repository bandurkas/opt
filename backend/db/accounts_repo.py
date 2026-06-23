"""Exchange accounts + encrypted credentials. Three accounts exist — ONE
Bybit account per bot, each with its own key and its own wallet balance (no
shared-capital-split bookkeeping needed, unlike a single account split across
strategies). Account names are the bots' call signs (user-assigned), kept
deliberately SEPARATE from db.control_repo.BOT_NAMES (the technical
pause/close-all key, already deployed/in use): this module is "which Bybit
account", control_repo is "which bot's pause/close-all flag" — renaming a
call sign here never touches already-running control state.

  - 'Boba1'   — BTC 24h straddle    (control_repo.BOT_NAMES: 'btc_straddle')
  - 'Grogu1'  — ETH 24h straddle    (control_repo.BOT_NAMES: 'eth_straddle')
  - 'Sniper1' — ETH signal bot      (control_repo.BOT_NAMES: 'eth_signal')
"""
from __future__ import annotations

import time

from psycopg2.extras import RealDictCursor

from .engine import get_conn, put_conn

ACCOUNT_NAMES = ("Boba1", "Grogu1", "Sniper1")

# Human-readable strategy label per account, for UI display alongside the call sign.
ACCOUNT_LABELS = {
    "Boba1": "BTC 24h straddle",
    "Grogu1": "ETH 24h straddle",
    "Sniper1": "ETH signal bot",
}

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

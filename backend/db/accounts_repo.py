"""Exchange accounts + encrypted credentials. One row exists today (the
default Bybit account the bots already trade); the schema is account_id-keyed
so adding more accounts later is a data change, not a schema change."""
from __future__ import annotations

import time

from psycopg2.extras import RealDictCursor

from .engine import get_conn, put_conn

DEFAULT_ACCOUNT_NAME = "default"


def ensure_default_account() -> dict:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM accounts WHERE name = %s", (DEFAULT_ACCOUNT_NAME,))
            row = cur.fetchone()
            if row:
                return dict(row)
            cur.execute(
                """
                INSERT INTO accounts (name, exchange, is_active, created_at_ms)
                VALUES (%s, 'bybit', true, %s)
                RETURNING *
                """,
                (DEFAULT_ACCOUNT_NAME, int(time.time() * 1000)),
            )
            conn.commit()
            return dict(cur.fetchone())
    finally:
        put_conn(conn)


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

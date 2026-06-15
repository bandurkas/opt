"""Ensure the target database in DATABASE_URL exists, then exit (P6).

The live `trader` service uses a SEPARATE database (options_trader) so its data
never mixes with the paper shadow. Postgres does not auto-create it, and
CREATE DATABASE can't run inside a transaction or against a missing target — so
this connects to the maintenance `postgres` DB with autocommit and creates the
target if absent. Idempotent; safe to run on every container start.

Run: python db/bootstrap.py   (reads DATABASE_URL from env)
"""
from __future__ import annotations

import os
import sys
import time
from urllib.parse import urlparse, urlunparse


def ensure_database(dsn: str | None = None, *, retries: int = 30) -> None:
    dsn = dsn or os.getenv("DATABASE_URL", "")
    if not dsn:
        print("[bootstrap] no DATABASE_URL — nothing to do", flush=True)
        return

    parsed = urlparse(dsn)
    target = (parsed.path or "/").lstrip("/")
    if not target:
        print("[bootstrap] DATABASE_URL has no db name — skipping", flush=True)
        return

    # Connect to the maintenance DB to (maybe) create the target.
    admin_dsn = urlunparse(parsed._replace(path="/postgres"))

    import psycopg2  # local import so module stays importable without the driver
    from psycopg2 import sql

    last_err: Exception | None = None
    for _ in range(retries):
        try:
            conn = psycopg2.connect(admin_dsn)
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,))
                    if cur.fetchone():
                        print(f"[bootstrap] database '{target}' already exists", flush=True)
                    else:
                        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
                        print(f"[bootstrap] created database '{target}'", flush=True)
            finally:
                conn.close()
            return
        except psycopg2.OperationalError as e:  # postgres not up yet
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"[bootstrap] postgres unreachable: {last_err}")


if __name__ == "__main__":
    ensure_database()
    sys.exit(0)

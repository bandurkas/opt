import os
import time
from pathlib import Path

import psycopg2
from psycopg2.pool import SimpleConnectionPool


_pool: SimpleConnectionPool | None = None


def _dsn() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://user:password@postgres:5432/options_assistant",
    )


def init_pool(minconn: int = 1, maxconn: int = 5, retries: int = 30) -> SimpleConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    last_err: Exception | None = None
    for _ in range(retries):
        try:
            _pool = SimpleConnectionPool(minconn, maxconn, dsn=_dsn())
            return _pool
        except psycopg2.OperationalError as e:
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"Postgres unreachable: {last_err}")


def get_conn():
    pool = init_pool()
    return pool.getconn()


def put_conn(conn) -> None:
    if _pool is None:
        conn.close()
        return
    _pool.putconn(conn)


def apply_schema() -> None:
    """Run schema.sql — idempotent (CREATE IF NOT EXISTS everywhere)."""
    sql = (Path(__file__).parent / "schema.sql").read_text()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        put_conn(conn)

"""Unit tests for db.btc_straddle_repo's per-pair manual-close-request
functions (request_close_pair / pop_close_requested_cycles). Fake DB
connection (no Postgres, no network), same pattern as test_control_repo.py.

Run: cd backend && PYTHONPATH=. python3 tests/test_btc_straddle_close_requests.py
Or:  cd backend && python3 -m pytest tests/test_btc_straddle_close_requests.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import btc_straddle_repo as repo


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.committed = True


def _patch(rows):
    cur = FakeCursor(rows)
    conn = FakeConn(cur)
    orig_get, orig_put = repo.get_conn, repo.put_conn
    repo.get_conn = lambda: conn
    repo.put_conn = lambda c: None

    def restore():
        repo.get_conn, repo.put_conn = orig_get, orig_put
    return conn, cur, restore


def test_request_close_pair_inserts_with_on_conflict_do_nothing():
    conn, cur, restore = _patch([])
    try:
        repo.request_close_pair(1234500123, by="telegram")
        assert conn.committed is True
        assert any("INSERT INTO btc_straddle_close_requests" in s for s in cur.executed)
        assert any("ON CONFLICT (cycle_id) DO NOTHING" in s for s in cur.executed)
    finally:
        restore()
    print("✓ request_close_pair upserts idempotently + commits")


def test_pop_close_requested_cycles_returns_and_clears():
    conn, cur, restore = _patch([(1234500123,), (1234500456,)])
    try:
        out = repo.pop_close_requested_cycles()
        assert out == [1234500123, 1234500456]
        assert conn.committed is True
        assert any("DELETE FROM btc_straddle_close_requests" in s for s in cur.executed)
        assert any("RETURNING cycle_id" in s for s in cur.executed)
    finally:
        restore()
    print("✓ pop_close_requested_cycles deletes+returns in one round trip")


def test_pop_close_requested_cycles_empty_when_none_pending():
    conn, cur, restore = _patch([])
    try:
        out = repo.pop_close_requested_cycles()
        assert out == []
    finally:
        restore()
    print("✓ pop_close_requested_cycles returns [] with nothing pending")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} btc_straddle close-request tests passed ✓")


if __name__ == "__main__":
    main()

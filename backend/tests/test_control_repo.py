"""Unit tests for db.control_repo — Mission Control pause/close-all flags.
Fake DB connection (no Postgres, no network), same pattern as test_paper_repo.py.

Run: cd backend && PYTHONPATH=. python3 tests/test_control_repo.py
Or:  cd backend && python3 -m pytest tests/test_control_repo.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import control_repo


class FakeCursor:
    def __init__(self, row):
        self._row = row
        self.executed: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.committed = True


def _patch(row):
    cur = FakeCursor(row)
    conn = FakeConn(cur)
    orig_get, orig_put = control_repo.get_conn, control_repo.put_conn
    control_repo.get_conn = lambda: conn
    control_repo.put_conn = lambda c: None

    def restore():
        control_repo.get_conn, control_repo.put_conn = orig_get, orig_put
    return conn, cur, restore


def test_is_paused_reads_existing_row():
    conn, cur, restore = _patch({"bot_name": "eth_signal", "paused": True,
                                  "close_all_requested": False, "updated_at_ms": 0})
    try:
        assert control_repo.is_paused("eth_signal") is True
    finally:
        restore()
    print("✓ is_paused reflects an existing paused=true row")


def test_is_paused_false_for_existing_row():
    conn, cur, restore = _patch({"bot_name": "eth_signal", "paused": False,
                                  "close_all_requested": False, "updated_at_ms": 0})
    try:
        assert control_repo.is_paused("eth_signal") is False
    finally:
        restore()
    print("✓ is_paused reflects an existing paused=false row")


def test_set_paused_commits():
    conn, cur, restore = _patch({"bot_name": "btc_straddle", "paused": True,
                                  "close_all_requested": False, "updated_at_ms": 123})
    try:
        out = control_repo.set_paused("btc_straddle", True, by="test")
        assert out["paused"] is True
        assert conn.committed is True
        assert any("INSERT INTO bot_control" in s for s in cur.executed)
    finally:
        restore()
    print("✓ set_paused upserts + commits")


def test_request_close_all_also_pauses():
    conn, cur, restore = _patch({"bot_name": "eth_straddle", "paused": True,
                                  "close_all_requested": True, "updated_at_ms": 456})
    try:
        out = control_repo.request_close_all("eth_straddle", by="test")
        assert out["paused"] is True, "close-all must imply paused (no instant re-entry)"
        assert out["close_all_requested"] is True
        assert conn.committed is True
    finally:
        restore()
    print("✓ request_close_all sets close_all_requested AND paused")


def test_clear_close_all_requested_runs_update():
    conn, cur, restore = _patch(None)
    try:
        control_repo.clear_close_all_requested("eth_signal")
        assert conn.committed is True
        assert any("UPDATE bot_control SET close_all_requested = false" in s for s in cur.executed)
    finally:
        restore()
    print("✓ clear_close_all_requested issues the UPDATE and commits")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} control_repo tests passed ✓")


if __name__ == "__main__":
    main()

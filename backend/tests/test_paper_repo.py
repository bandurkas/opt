"""Unit tests for paper_repo.record_trade_outcome — the atomic circuit-breaker
write path. Uses a fake DB connection (no Postgres, no network).

Covers the rowcount guard: if the paper_state row (id=1) is missing, the UPDATE
hits 0 rows and the breaker advance would be silently dropped — the guard must
turn that into a loud RuntimeError and roll back instead of returning a result
that was never persisted.

Run standalone:   cd backend && PYTHONPATH=. python3 tests/test_paper_repo.py
Or via pytest:    cd backend && python3 -m pytest tests/test_paper_repo.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import paper_repo


class FakeCursor:
    """Minimal psycopg2-cursor stand-in. Returns `row` on fetchone() and reports
    `update_rowcount` after the UPDATE. Records the SQL it ran."""

    def __init__(self, row, update_rowcount):
        self._row = row
        self._update_rowcount = update_rowcount
        self.rowcount = 0
        self.executed: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)
        # rowcount reflects the *last* statement, like psycopg2.
        self.rowcount = self._update_rowcount if sql.strip().upper().startswith("UPDATE") else 1

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _patch(row, update_rowcount):
    """Wire paper_repo to a fake connection; return (conn, restore_fn)."""
    cur = FakeCursor(row, update_rowcount)
    conn = FakeConn(cur)
    orig_get, orig_put = paper_repo.get_conn, paper_repo.put_conn
    returned = []
    paper_repo.get_conn = lambda: conn
    paper_repo.put_conn = lambda c: returned.append(c)
    conn._returned = returned  # so tests can assert the conn went back to the pool

    def restore():
        paper_repo.get_conn, paper_repo.put_conn = orig_get, orig_put
    return conn, restore


def _decide(consec, cb_until, pnls, pnl_pct, now_ms):
    # Trivial deterministic transition, isolates the repo from CB business logic.
    return {"consec_losses": consec + 1, "cb_cooldown_until_ms": cb_until,
            "recent_pnls": list(pnls) + [pnl_pct]}


def test_happy_path_commits_and_returns():
    row = {"consec_losses": 0, "cb_cooldown_until_ms": 0, "recent_pnls_json": []}
    conn, restore = _patch(row, update_rowcount=1)
    try:
        out = paper_repo.record_trade_outcome(-5.0, 1_700_000_000_000, _decide)
        assert out["consec_losses"] == 1, out
        assert conn.committed is True and conn.rolled_back is False
        assert conn._returned == [conn], "connection must be returned to the pool"
    finally:
        restore()
    print("✓ happy path: commits, returns next state, returns conn to pool")


def test_missing_row_raises_and_rolls_back():
    conn, restore = _patch(row=None, update_rowcount=0)  # no paper_state row
    try:
        raised = False
        try:
            paper_repo.record_trade_outcome(-5.0, 1_700_000_000_000, _decide)
        except RuntimeError:
            raised = True
        assert raised, "missing state row must raise RuntimeError, not silently no-op"
        assert conn.committed is False, "must not commit a dropped write"
        assert conn.rolled_back is True, "must roll back on the guard failure"
        assert conn._returned == [conn], "connection must still be returned to the pool"
    finally:
        restore()
    print("✓ missing state row -> RuntimeError + rollback + conn returned")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} paper_repo tests passed ✓")


if __name__ == "__main__":
    main()

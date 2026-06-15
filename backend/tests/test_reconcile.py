"""Unit tests for reconcile (P5) — pure diff + exchange parsing, no DB/network.

Run: cd backend && PYTHONPATH=. python3 tests/test_reconcile.py
"""
from __future__ import annotations

import sys

from services import reconcile as rc


class FakePositions:
    def __init__(self, rows):
        self._rows = rows

    def positions(self, base_coin="ETH"):
        return self._rows


def _pos(pid, sym):
    return {"id": pid, "signal_payload": {"symbol": sym}}


def test_diff_all_match() -> None:
    db = [_pos(1, "A"), _pos(2, "B")]
    closed, untracked = rc.diff_positions(db, {"A": 0.2, "B": 0.1})
    assert closed == [] and untracked == []


def test_diff_externally_closed() -> None:
    db = [_pos(1, "A"), _pos(2, "B")]
    closed, untracked = rc.diff_positions(db, {"A": 0.2})  # B flat on exchange
    assert [p["id"] for p in closed] == [2]
    assert untracked == []


def test_diff_untracked() -> None:
    db = [_pos(1, "A")]
    closed, untracked = rc.diff_positions(db, {"A": 0.2, "C": 0.3})
    assert closed == [] and untracked == ["C"]


def test_diff_mixed() -> None:
    db = [_pos(1, "A"), _pos(2, "B")]
    closed, untracked = rc.diff_positions(db, {"A": 0.2, "C": 0.3})  # B flat, C untracked
    assert [p["id"] for p in closed] == [2]
    assert untracked == ["C"]


def test_diff_all_flat() -> None:
    db = [_pos(1, "A"), _pos(2, "B")]
    closed, untracked = rc.diff_positions(db, {})
    assert sorted(p["id"] for p in closed) == [1, 2]
    assert untracked == []


def test_diff_position_without_symbol_skipped() -> None:
    db = [{"id": 1, "signal_payload": {}}, _pos(2, "B")]
    closed, untracked = rc.diff_positions(db, {"B": 0.1})
    assert closed == [] and untracked == []  # #1 has no symbol → ignored


def test_exchange_sizes_filters_and_abs() -> None:
    c = FakePositions([
        {"symbol": "A", "size": "0.2"},
        {"symbol": "B", "size": "0"},     # flat → excluded
        {"symbol": "C", "size": "-0.1"},  # short → abs 0.1
        {"symbol": "", "size": "0.5"},    # no symbol → excluded
    ])
    sizes = rc.exchange_position_sizes(c)
    assert sizes == {"A": 0.2, "C": 0.1}


def test_exchange_sizes_read_failure_returns_none() -> None:
    assert rc.exchange_position_sizes(FakePositions(None)) is None


def test_is_blocked_default_false() -> None:
    assert rc.is_blocked() is False


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"✓ {t.__name__}")
        passed += 1
    print(f"\nAll {passed} reconcile tests passed ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

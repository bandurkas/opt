"""Unit tests for eth_straddle_loop.guarded_mark_for_close — the tick-to-tick
jump filter for the SL/TP2 close decision (deployed 2026-06-26, see
project_grogu_execution_guard_deferred.md for the backtest numbers).

Run: cd backend && PYTHONPATH=. python3 tests/test_eth_straddle_jump_detector.py
"""
from __future__ import annotations

import sys

from services import eth_straddle_loop as loop


def _reset():
    loop._last_accepted_mark.clear()


def test_first_tick_always_accepted() -> None:
    _reset()
    chain = {"C-3000-99": {"ask": "10.0"}}
    m = loop.guarded_mark_for_close(1, "C", 3000, 99, chain)
    assert m == 10.0, m
    assert loop._last_accepted_mark[1] == 10.0


def test_small_move_accepted_and_updates_baseline() -> None:
    _reset()
    chain1 = {"C-3000-99": {"ask": "10.0"}}
    chain2 = {"C-3000-99": {"ask": "13.0"}}  # +30%, under the 50% threshold
    loop.guarded_mark_for_close(1, "C", 3000, 99, chain1)
    m = loop.guarded_mark_for_close(1, "C", 3000, 99, chain2)
    assert m == 13.0, m
    assert loop._last_accepted_mark[1] == 13.0


def test_jump_over_threshold_rejected_reuses_last() -> None:
    _reset()
    chain1 = {"C-3000-99": {"ask": "10.0"}}
    chain2 = {"C-3000-99": {"ask": "70.0"}}  # +600%, an outlier print
    loop.guarded_mark_for_close(1, "C", 3000, 99, chain1)
    m = loop.guarded_mark_for_close(1, "C", 3000, 99, chain2)
    assert m == 10.0, m  # rejected — reused last accepted, not the outlier
    assert loop._last_accepted_mark[1] == 10.0  # baseline NOT advanced by the rejected tick


def test_positions_tracked_independently() -> None:
    _reset()
    chain = {"C-3000-99": {"ask": "10.0"}, "P-3000-99": {"ask": "5.0"}}
    loop.guarded_mark_for_close(1, "C", 3000, 99, chain)
    loop.guarded_mark_for_close(2, "P", 3000, 99, chain)
    assert loop._last_accepted_mark == {1: 10.0, 2: 5.0}


def test_no_chain_data_returns_none() -> None:
    _reset()
    assert loop.guarded_mark_for_close(1, "C", 3000, 99, None) is None
    assert loop.guarded_mark_for_close(1, "C", 3000, 99, {}) is None


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    if failed:
        print(f"\n{failed}/{len(tests)} FAILED")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")

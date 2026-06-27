"""Unit tests for check_cluster_worst_leg (2026-06-27 cluster-stop fix).

Isolates the pure decision logic from paper_loop.py by monkeypatching
current_mark() (no live Bybit call) and _do_close() (no DB/Telegram) — these
tests only verify: which leg gets picked as "worst", whether the threshold
gate fires correctly, and that sides/singletons are handled as designed.

Run: cd backend && PYTHONPATH=. python3 -m pytest tests/test_cluster_worst_leg.py -v
Or standalone: cd backend && PYTHONPATH=. python3 tests/test_cluster_worst_leg.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import paper_loop
from services.strategy_config import CLUSTER_STOP_WORST_LEG_FRAC


def _pos(pid: int, side: str, entry_credit: float) -> dict:
    return {"id": pid, "side": side, "strike": 1525.0, "expiry_ms": 1234,
            "entry_credit_usd": entry_credit}


def _run(open_pos, marks: dict[int, float]):
    """marks: {position_id: current_mark_value (or None for "no live data")}.
    Returns the list of (position, mark, reason) tuples _do_close was called with."""
    calls = []
    orig_mark = paper_loop.current_mark
    orig_close = paper_loop._do_close
    paper_loop.current_mark = lambda p, spot, chain_dict: marks.get(p["id"])
    paper_loop._do_close = lambda p, mark, reason, now_ms: (calls.append((p, mark, reason)) or True)
    try:
        n = paper_loop.check_cluster_worst_leg(open_pos, spot=1500.0, chain_dict={})
    finally:
        paper_loop.current_mark = orig_mark
        paper_loop._do_close = orig_close
    return n, calls


def test_single_position_never_triggers():
    """Need 2+ concurrently open on the same side — a lone position must
    never be force-closed by this mechanism (its own SL handles it)."""
    pos = [_pos(1, "C", 20.0)]
    n, calls = _run(pos, {1: 100.0})  # huge loss, but only 1 open
    assert n == 0 and calls == []


def test_two_positions_neither_breaches():
    pos = [_pos(1, "C", 20.0), _pos(2, "C", 25.0)]
    # losses well under 40% of credit
    n, calls = _run(pos, {1: 25.0, 2: 28.0})
    assert n == 0 and calls == []


def test_worst_leg_only_is_closed_not_the_other():
    pos = [_pos(1, "C", 20.0), _pos(2, "C", 25.0)]
    # #1: loss=15 -> 75% of credit (breaches 40%). #2: loss=2 -> 8% (fine).
    n, calls = _run(pos, {1: 35.0, 2: 27.0})
    assert n == 1
    assert len(calls) == 1
    closed_pos, mark, reason = calls[0]
    assert closed_pos["id"] == 1
    assert mark == 35.0
    assert reason == "cluster_stop_worst_leg"


def test_picks_the_worst_of_three_not_first_or_last():
    """2026-06-26-shaped scenario: 3 concurrent Calls, the MIDDLE one (by
    open order) is actually the worst by unrealized loss — must still be
    the one picked, not positionally biased."""
    pos = [_pos(12, "C", 31.37), _pos(13, "C", 25.38), _pos(14, "C", 28.55)]
    marks = {12: 40.0, 13: 50.0, 14: 38.0}  # #13 has the biggest loss
    n, calls = _run(pos, marks)
    assert n == 1
    assert calls[0][0]["id"] == 13


def test_threshold_is_exclusive_at_exactly_the_boundary():
    pos = [_pos(1, "C", 20.0), _pos(2, "C", 20.0)]
    boundary_mark = 20.0 * (1 + CLUSTER_STOP_WORST_LEG_FRAC)  # exactly 40% loss
    n, calls = _run(pos, {1: boundary_mark, 2: boundary_mark})
    assert n == 0, "loss exactly AT the threshold must not trigger (strict >)"
    n2, calls2 = _run(pos, {1: boundary_mark + 0.01, 2: boundary_mark})
    assert n2 == 1 and calls2[0][0]["id"] == 1


def test_sides_are_independent():
    """A Call cluster breaching threshold must not affect Put positions, and
    a lone Put (even if it would have triggered) must not be touched without
    a second Put alongside it."""
    pos = [_pos(1, "C", 20.0), _pos(2, "C", 25.0), _pos(3, "P", 20.0)]
    n, calls = _run(pos, {1: 35.0, 2: 27.0, 3: 100.0})
    assert n == 1
    assert calls[0][0]["id"] == 1  # only the Call cluster's worst leg


def test_missing_live_mark_excludes_that_leg_from_consideration():
    """If current_mark() returns None for one leg (no live chain data), that
    leg must be skipped entirely (same no-BS-fallback rule as TP/SL) — not
    crash, not get force-closed on missing data."""
    pos = [_pos(1, "C", 20.0), _pos(2, "C", 25.0)]
    n, calls = _run(pos, {1: None, 2: 40.0})
    # only #2 has live data, and with <2 legs having data, no cluster check applies
    assert n == 0 and calls == []


def test_no_open_positions_is_a_noop():
    n, calls = _run([], {})
    assert n == 0 and calls == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK: {t.__name__}")
    print(f"\n{len(tests)} tests passed")

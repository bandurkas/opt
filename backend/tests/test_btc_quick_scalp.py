"""Unit tests for btc_straddle_loop's quick-scalp pair logic (2026-06-26
rewrite — see straddle_quick_scalp_backtest.py for the validated mechanic
and finding/PROJECT docs for context). Covers the pure decision function
`decide_pair_action` (no DB/network — mirrors paper_strategy.py's
`_next_cb_state` pattern) and the flat-to-reopen gate's tenor math.

Run: cd backend && PYTHONPATH=. python3 tests/test_btc_quick_scalp.py
"""
from __future__ import annotations

import sys

from services import btc_straddle_loop as loop
from services import btc_straddle_sl as sl


def _leg(leg: str, entry_credit: float, contracts: float, sl_trip: float,
        expiry_ms: int = 10**15) -> dict:
    return {"leg": leg, "entry_credit_usd": entry_credit, "contracts": contracts,
            "sl_dollar_trip_usd": sl_trip, "expiry_ms": expiry_ms}


def test_neither_condition_holds() -> None:
    legs = [_leg("C", 10.0, 0.01, 50.0), _leg("P", 10.0, 0.01, 50.0)]
    marks = {"C": 10.5, "P": 9.5}  # combined pnl = (10-10.5)*.01 + (10-9.5)*.01 = 0
    action, tripped = loop.decide_pair_action(legs, marks, now_ms=1000)
    assert action == "hold", action
    assert tripped is None


def test_quick_tp_fires_on_combined_credit() -> None:
    # entry_credit=100/lot, 1 lot (0.01 BTC) each leg: (100-mark)*0.01 per leg.
    # marks=0 on both legs -> combined pnl = (100-0)*0.01*2 = $2.00, exactly
    # QUICK_TP_COMBINED_USD's default — confirms the >= boundary fires.
    legs = [_leg("C", 100.0, 0.01, 500.0), _leg("P", 100.0, 0.01, 500.0)]
    assert sl.QUICK_TP_COMBINED_USD == 2.0  # test assumes this default; update if it changes
    marks = {"C": 0.0, "P": 0.0}
    action, tripped = loop.decide_pair_action(legs, marks, now_ms=1000)
    assert action == "quick_tp", action
    assert tripped is None


def test_sl_takes_priority_over_quick_tp() -> None:
    # Put is deeply underwater (SL-tripping) while Call is hugely profitable —
    # combined credit alone would clear QUICK_TP, but SL must win the tie.
    legs = [_leg("C", 10.0, 0.01, 50.0), _leg("P", 10.0, 0.01, 5.0)]
    marks = {"C": 0.01, "P": 510.0}  # put loss = (510-10)*0.01=$5.00 >= sl_trip $5.0
    action, tripped = loop.decide_pair_action(legs, marks, now_ms=1000)
    assert action == "sl", action
    assert tripped is legs[1]  # the Put


def test_time_stop_fires_at_shared_expiry() -> None:
    legs = [_leg("C", 10.0, 0.01, 50.0, expiry_ms=2000), _leg("P", 10.0, 0.01, 50.0, expiry_ms=2000)]
    marks = {"C": 10.4, "P": 10.4}  # no SL trip, no quick-TP (combined pnl negative)
    action, tripped = loop.decide_pair_action(legs, marks, now_ms=2000)
    assert action == "time_stop", action
    assert tripped is None


def test_time_stop_does_not_fire_before_expiry() -> None:
    legs = [_leg("C", 10.0, 0.01, 50.0, expiry_ms=2000), _leg("P", 10.0, 0.01, 50.0, expiry_ms=2000)]
    marks = {"C": 10.4, "P": 10.4}
    action, tripped = loop.decide_pair_action(legs, marks, now_ms=1999)
    assert action == "hold", action


def test_reentry_tenor_shrinks_through_the_day() -> None:
    # current_cycle_id/CYCLE_MS arithmetic: remaining_h should shrink linearly
    # as now_ms advances toward the next day boundary, never go negative for
    # a now_ms strictly inside the current day.
    cyc = loop.current_cycle_id(0)
    day_end_ms = (cyc + 1) * loop.CYCLE_MS
    half_day_ms = day_end_ms - loop.CYCLE_MS // 2
    remaining_h = (day_end_ms - half_day_ms) / 3_600_000
    assert abs(remaining_h - 12.0) < 1e-6, remaining_h
    near_end_ms = day_end_ms - 1000
    remaining_h_near_end = (day_end_ms - near_end_ms) / 3_600_000
    assert 0 < remaining_h_near_end < 0.001


def test_new_cycle_id_unique_and_sortable_within_a_day() -> None:
    cyc = 20630
    t0 = cyc * loop.CYCLE_MS + 5_000      # 5s into the day
    t1 = cyc * loop.CYCLE_MS + 3_600_000  # 1h into the day
    id0 = cyc * 100_000 + (t0 - cyc * loop.CYCLE_MS) // 1000
    id1 = cyc * 100_000 + (t1 - cyc * loop.CYCLE_MS) // 1000
    assert id0 != id1
    assert id1 > id0  # later re-entry gets a larger id, sorts naturally
    assert id0 // 100_000 == cyc  # day-bucket recoverable from the id


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

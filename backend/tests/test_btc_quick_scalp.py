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
    # entry_credit=1100/lot, 1 lot (0.01 BTC) each leg: (1100-mark)*0.01 per leg.
    # marks=0 on both legs -> combined pnl = 1100*0.01*2 = $22.00, exactly
    # QUICK_TP_COMBINED_USD's default — confirms the >= boundary fires.
    legs = [_leg("C", 1100.0, 0.01, 5000.0), _leg("P", 1100.0, 0.01, 5000.0)]
    assert sl.QUICK_TP_COMBINED_USD == 22.0  # test assumes this default; update if it changes
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
    # a now_ms strictly inside the current day. Day boundary is anchored at
    # ANCHOR_HOUR_UTC (not epoch 0) — cycle_day_start_ms() is the source of
    # truth, matching what the live loop computes each tick.
    cyc = loop.current_cycle_id(0)
    day_start_ms = loop.cycle_day_start_ms(cyc)
    day_end_ms = day_start_ms + loop.CYCLE_MS
    half_day_ms = day_end_ms - loop.CYCLE_MS // 2
    remaining_h = (day_end_ms - half_day_ms) / 3_600_000
    assert abs(remaining_h - 12.0) < 1e-6, remaining_h
    near_end_ms = day_end_ms - 1000
    remaining_h_near_end = (day_end_ms - near_end_ms) / 3_600_000
    assert 0 < remaining_h_near_end < 0.001


def test_new_cycle_id_unique_and_sortable_within_a_day() -> None:
    # Seconds-into-day MUST be measured from cycle_day_start_ms(cyc), not
    # cyc*CYCLE_MS directly — with ANCHOR_OFFSET_MS > 0, cyc*CYCLE_MS sits
    # BEFORE the real day start, so "now_ms - cyc*CYCLE_MS" would run past
    # 100_000s and collide with the next cycle's id range.
    cyc = 20630
    day_start_ms = loop.cycle_day_start_ms(cyc)
    t0 = day_start_ms + 5_000      # 5s into the day
    t1 = day_start_ms + 3_600_000  # 1h into the day
    id0 = cyc * 100_000 + (t0 - day_start_ms) // 1000
    id1 = cyc * 100_000 + (t1 - day_start_ms) // 1000
    assert id0 != id1
    assert id1 > id0  # later re-entry gets a larger id, sorts naturally
    assert id0 // 100_000 == cyc  # day-bucket recoverable from the id
    # the whole day (0..CYCLE_MS-1 seconds-into-day) must stay under 100_000s
    last_second_ms = day_start_ms + loop.CYCLE_MS - 1000
    id_last = cyc * 100_000 + (last_second_ms - day_start_ms) // 1000
    assert id_last // 100_000 == cyc, id_last  # must NOT overflow into cyc+1's range


def _init_alert_state() -> dict:
    return {"peak": 0.0, "last_milestone": 0.0, "pullback_alerted_peak": 0.0}


def test_profit_alert_no_alert_below_first_threshold() -> None:
    state, kind, level = loop.next_profit_alert(_init_alert_state(), 2.9)
    assert kind is None, kind


def test_profit_alert_first_milestone_fires_at_3() -> None:
    state, kind, level = loop.next_profit_alert(_init_alert_state(), 3.0)
    assert (kind, level) == ("milestone", 3.0)


def test_profit_alert_sequential_milestones() -> None:
    state = _init_alert_state()
    state, kind, level = loop.next_profit_alert(state, 3.0)
    assert (kind, level) == ("milestone", 3.0)
    state, kind, level = loop.next_profit_alert(state, 3.5)
    assert kind is None, kind  # not yet at the next threshold ($5)
    state, kind, level = loop.next_profit_alert(state, 5.0)
    assert (kind, level) == ("milestone", 5.0)
    state, kind, level = loop.next_profit_alert(state, 7.0)
    assert (kind, level) == ("milestone", 7.0)


def test_profit_alert_big_jump_advances_to_highest_crossed_level_once() -> None:
    state = _init_alert_state()
    state, kind, level = loop.next_profit_alert(state, 9.0)
    assert (kind, level) == ("milestone", 9.0)  # one alert, not 3/5/7/9 stacked
    state, kind, level = loop.next_profit_alert(state, 9.0)
    assert kind is None, kind  # same level again — no repeat


def test_profit_alert_milestones_capped_below_quick_tp() -> None:
    assert sl.QUICK_TP_COMBINED_USD == 22.0  # test assumes this default
    state = _init_alert_state()
    state["last_milestone"] = 19.0
    state["peak"] = 19.0
    state, kind, level = loop.next_profit_alert(state, 21.0)
    assert (kind, level) == ("milestone", 21.0)
    state, kind, level = loop.next_profit_alert(state, 22.0)
    assert kind is None, kind  # quick_tp territory — milestones stop, auto-exit takes over


def test_profit_alert_pullback_fires_from_peak() -> None:
    state = _init_alert_state()
    state, kind, level = loop.next_profit_alert(state, 4.0)
    assert kind == "milestone"
    state, kind, level = loop.next_profit_alert(state, 2.0)  # $4 peak - $2 = $2 pullback
    assert (kind, level) == ("pullback", 4.0)


def test_profit_alert_pullback_does_not_repeat_at_same_peak() -> None:
    state = _init_alert_state()
    state, _, _ = loop.next_profit_alert(state, 4.0)
    state, kind, _ = loop.next_profit_alert(state, 2.0)
    assert kind == "pullback"
    state, kind, _ = loop.next_profit_alert(state, 1.0)  # still below the alerted peak
    assert kind is None, kind


def test_profit_alert_pullback_rearms_after_new_higher_peak() -> None:
    state = _init_alert_state()
    state, _, _ = loop.next_profit_alert(state, 4.0)
    state, kind, _ = loop.next_profit_alert(state, 2.0)
    assert kind == "pullback"
    state, kind, _ = loop.next_profit_alert(state, 6.0)  # new peak
    assert kind == "milestone"
    state, kind, level = loop.next_profit_alert(state, 4.0)  # $6 peak - $2 = $4
    assert (kind, level) == ("pullback", 6.0)


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

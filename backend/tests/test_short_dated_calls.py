"""Unit tests for short-dated (24h) Calls — per-side expiry + re-tuned Call exits.

Covers IMPL_SHORT_DATED_CALLS.md §4: Calls target a 24h contract, Puts stay 168h,
the re-tuned CALL_EXIT (tp0.4/0.8 sl0.75 hold24) is wired, and option selection
reads per-side target expiry while MTM/close read per-position expiry.

Run:  cd backend && PYTHONPATH=. python3 tests/test_short_dated_calls.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.strategy_config import (
    CALL_TARGET_EXPIRY_H,
    PUT_TARGET_EXPIRY_H,
    get_side_expiry_h,
    get_side_exits,
)
from services.paper_loop import pick_bybit_atm_option

H = 3_600_000  # ms per hour


# ── §4.1: get_side_expiry_h ──
def test_side_expiry_call_short_put_long():
    assert get_side_expiry_h("C") == 24, CALL_TARGET_EXPIRY_H
    assert get_side_expiry_h("P") == 168, PUT_TARGET_EXPIRY_H
    # case-insensitive; None/"" default to Call (short) per spec
    assert get_side_expiry_h("c") == 24
    assert get_side_expiry_h("p") == 168
    assert get_side_expiry_h(None) == 24
    assert get_side_expiry_h("") == 24
    print("✓ get_side_expiry_h: C/c/None->24, P/p->168")


# ── re-tuned CALL_EXIT, PUT_EXIT untouched ──
def test_call_exit_retuned_put_untouched():
    c = get_side_exits("C")
    assert (c["tp1_pct"], c["tp2_pct"], c["sl_pct"], c["hold_h"]) == (0.40, 0.80, 0.75, 24), c
    p = get_side_exits("P")
    assert p["hold_h"] == 96, p  # Put 4-day hold unchanged
    print("✓ CALL_EXIT=tp0.4/0.8 sl0.75 h24; PUT_EXIT unchanged")


def _chain(now_ms: float) -> list[dict]:
    """Daily (18h) + weekly (168h) + monthly (720h) ATM options, both sides."""
    out = []
    for side in ("C", "P"):
        for h, strike in ((18, 3000), (168, 3000), (720, 3000)):
            out.append({"side": side, "strike": strike, "bid": 10.0, "ask": 12.0,
                        "expiry_ms": now_ms + h * H, "symbol": f"ETH-{h}h-{strike}-{side}"})
    return out


# ── §4.2: pick_bybit_atm_option honors per-side target ──
def test_call_picks_daily_put_picks_weekly():
    now = int(time.time() * 1000)
    chain = _chain(now)
    spot = 3000.0
    call = pick_bybit_atm_option(chain, spot, get_side_expiry_h("C"), "C")
    assert call and call["side"] == "C", call
    assert round((call["expiry_ms"] - now) / H) == 18, call  # nearest to 24h => daily 18h
    put = pick_bybit_atm_option(chain, spot, get_side_expiry_h("P"), "P")
    assert put and put["side"] == "P", put
    assert round((put["expiry_ms"] - now) / H) == 168, put   # nearest to 168h => weekly
    print("✓ Call->daily(18h), Put->weekly(168h)")


def test_filters_bid_and_min_6h():
    now = int(time.time() * 1000)
    spot = 3000.0
    # Only a too-soon (3h) contract and a zero-bid daily — both must be rejected.
    chain = [
        {"side": "C", "strike": 3000, "bid": 10.0, "ask": 12.0, "expiry_ms": now + 3 * H},
        {"side": "C", "strike": 3000, "bid": 0.0, "ask": 12.0, "expiry_ms": now + 18 * H},
    ]
    assert pick_bybit_atm_option(chain, spot, 24, "C") is None
    # Add a valid daily with bid>0 and >6h -> now selectable.
    chain.append({"side": "C", "strike": 3000, "bid": 5.0, "ask": 6.0, "expiry_ms": now + 20 * H})
    pick = pick_bybit_atm_option(chain, spot, 24, "C")
    assert pick and round((pick["expiry_ms"] - now) / H) == 20, pick
    print("✓ filters: bid>0 and expiry>now+6h enforced")


def test_atm_strike_selection():
    now = int(time.time() * 1000)
    spot = 3007.0  # rounds to 3000 on $25 grid
    chain = [
        {"side": "C", "strike": s, "bid": 10.0, "ask": 12.0, "expiry_ms": now + 18 * H}
        for s in (2950, 3000, 3050)
    ]
    pick = pick_bybit_atm_option(chain, spot, 24, "C")
    assert pick["strike"] == 3000, pick
    print("✓ ATM strike picked within chosen expiry")


# ── §4.3: mixed expiries are per-position (offsets differ by side) ──
def test_mixed_expiry_offsets_distinct():
    call_off = get_side_expiry_h("C") * H
    put_off = get_side_expiry_h("P") * H
    assert put_off == 7 * call_off, (call_off, put_off)  # 168 = 7 * 24
    print("✓ Call/Put expiry offsets distinct (168h = 7*24h)")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} short-dated-call tests passed ✓")


if __name__ == "__main__":
    main()

"""Unit tests for btc_straddle_sl — dollar-margin stop math, no network/DB.

Run: cd backend && PYTHONPATH=. python3 tests/test_btc_straddle_sl.py
"""
from __future__ import annotations

import sys

from services import btc_straddle_sl as sl


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def test_margin_per_lot_known_value() -> None:
    # (0.10*65000 + 300) * 0.01 = 68.0
    m = sl.margin_per_lot(65000, 300, im_rate=0.10, lot=0.01)
    assert _approx(m, 68.0), m


def test_margin_per_lot_guards() -> None:
    assert sl.margin_per_lot(0, 300) == 0.0
    assert sl.margin_per_lot(65000, -1) == 0.0
    assert sl.margin_per_lot(65000, 300, lot=0) == 0.0


def test_sl_dollar_trip_default_frac() -> None:
    # trip = 2.0 * margin_per_lot
    assert _approx(sl.sl_dollar_trip(68.0), 136.0)
    assert _approx(sl.sl_dollar_trip(68.0, sl_dollar_frac=1.0), 68.0)


def test_is_tripped_below_threshold_not_tripped() -> None:
    # entry credit 300, buyback ask 350 -> loss 50/contract * qty 0.01 = 0.50
    # trip_per_lot 136 * (0.01/0.01) = 136 -> not tripped
    assert sl.is_tripped(entry_credit=300, current_buyback_ask=350, qty=0.01,
                        sl_trip_per_lot_usd=136.0, lot=0.01) is False


def test_is_tripped_at_threshold() -> None:
    # loss = (300+136 - 300)*0.01... construct exact equality: want unrealized_loss == trip
    # trip = 136 * (qty/lot) ; qty=0.01,lot=0.01 -> trip=136. unrealized_loss = (ask-entry)*qty
    # need (ask-300)*0.01 = 136 -> ask = 300 + 13600 (absurd for 1 lot; use qty scaled instead)
    # Use qty=1 (100 lots) so the dollar trip scales to a realistic ask move.
    # trip = 136 * (1/0.01) = 13600 ; unrealized_loss = (ask-300)*1 = 13600 -> ask=13900
    assert sl.is_tripped(entry_credit=300, current_buyback_ask=13900, qty=1.0,
                        sl_trip_per_lot_usd=136.0, lot=0.01) is True


def test_is_tripped_scales_with_qty() -> None:
    # Double the qty -> double the dollar loss AND double the trip -> same trip outcome
    # at the same per-contract price move (bounded -106%-of-margin style check).
    tripped_1x = sl.is_tripped(entry_credit=300, current_buyback_ask=600, qty=0.01,
                               sl_trip_per_lot_usd=136.0, lot=0.01)
    tripped_2x = sl.is_tripped(entry_credit=300, current_buyback_ask=600, qty=0.02,
                               sl_trip_per_lot_usd=136.0, lot=0.01)
    assert tripped_1x == tripped_2x  # same %-move outcome regardless of position size


def test_is_tripped_guards() -> None:
    assert sl.is_tripped(entry_credit=300, current_buyback_ask=9999, qty=0,
                        sl_trip_per_lot_usd=136.0, lot=0.01) is False
    assert sl.is_tripped(entry_credit=300, current_buyback_ask=9999, qty=0.01,
                        sl_trip_per_lot_usd=136.0, lot=0) is False


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"✓ {t.__name__}")
        passed += 1
    print(f"\nAll {passed} btc_straddle_sl tests passed ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

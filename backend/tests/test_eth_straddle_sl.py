"""Unit tests for eth_straddle_sl — dollar-margin stop math, no network/DB.

Mirrors test_btc_straddle_sl.py with ETH's own constants (IM_RATE=0.10,
LOT_ETH=0.10, SL_DOLLAR_FRAC=0.30 — see eth_straddle_sl.py's module
docstring for why 0.35 was investigated 2026-06-24 and NOT deployed
standalone (it's only valid bundled with the shadow entry filter).

Run: cd backend && PYTHONPATH=. python3 tests/test_eth_straddle_sl.py
"""
from __future__ import annotations

import sys

from services import eth_straddle_sl as sl


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def test_margin_per_lot_known_value() -> None:
    # (0.10*3850 + 40) * 0.10 = 42.5
    m = sl.margin_per_lot(3850, 40, im_rate=0.10, lot=0.10)
    assert _approx(m, 42.5), m


def test_margin_per_lot_guards() -> None:
    assert sl.margin_per_lot(0, 40) == 0.0
    assert sl.margin_per_lot(3850, -1) == 0.0
    assert sl.margin_per_lot(3850, 40, lot=0) == 0.0


def test_sl_dollar_trip_default_frac() -> None:
    # trip = 0.30 * margin_per_lot (current SL_DOLLAR_FRAC)
    assert _approx(sl.sl_dollar_trip(42.5), 12.75)
    assert _approx(sl.sl_dollar_trip(42.5, sl_dollar_frac=1.0), 42.5)


def test_is_tripped_below_threshold_not_tripped() -> None:
    # entry credit 40, buyback ask 50 -> loss 10/contract * qty 0.10 = 1.0
    # trip_per_lot 12.75 * (0.10/0.10) = 12.75 -> not tripped
    assert sl.is_tripped(entry_credit=40, current_buyback_ask=50, qty=0.10,
                        sl_trip_per_lot_usd=12.75, lot=0.10) is False


def test_is_tripped_at_threshold() -> None:
    # Use qty=1 (10 lots) so the dollar trip scales to a realistic ask move.
    # trip = 12.75 * (1/0.10) = 127.5 ; unrealized_loss = (ask-40)*1 = 127.5 -> ask=167.5
    assert sl.is_tripped(entry_credit=40, current_buyback_ask=167.5, qty=1.0,
                        sl_trip_per_lot_usd=12.75, lot=0.10) is True


def test_is_tripped_scales_with_qty() -> None:
    # Double the qty -> double the dollar loss AND double the trip -> same
    # trip outcome at the same per-contract price move.
    tripped_1x = sl.is_tripped(entry_credit=40, current_buyback_ask=80, qty=0.10,
                               sl_trip_per_lot_usd=12.75, lot=0.10)
    tripped_2x = sl.is_tripped(entry_credit=40, current_buyback_ask=80, qty=0.20,
                               sl_trip_per_lot_usd=12.75, lot=0.10)
    assert tripped_1x == tripped_2x  # same %-move outcome regardless of position size


def test_is_tripped_guards() -> None:
    assert sl.is_tripped(entry_credit=40, current_buyback_ask=9999, qty=0,
                        sl_trip_per_lot_usd=12.75, lot=0.10) is False
    assert sl.is_tripped(entry_credit=40, current_buyback_ask=9999, qty=0.10,
                        sl_trip_per_lot_usd=12.75, lot=0) is False


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"✓ {t.__name__}")
        passed += 1
    print(f"\nAll {passed} eth_straddle_sl tests passed ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

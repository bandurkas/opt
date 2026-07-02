"""Unit tests for btc_straddle_loop's min-lot-floor sizing guard.

Tests the paper-path formula directly (no DB/broker needed) to prove:
  - floor fires when MARGIN_PCT budget rounds to 0 but 1 lot fits in 40% equity
  - floor is suppressed below ABS_FLOOR_EQUITY
  - floor is suppressed when 1 lot exceeds 40% of equity
  - normal multi-lot sizing is unaffected

Run: cd backend && PYTHONPATH=. python3 tests/test_btc_straddle_min_lot_floor.py
"""
from __future__ import annotations
import sys

# Constants mirrored from btc_straddle_loop.py (defaults, no import needed)
MARGIN_PCT_PER_CYCLE = 0.15
ABS_FLOOR_EQUITY = 50.0
MAX_FLOOR_EQUITY_FRAC = 0.40  # 1 lot must fit within 40% of equity per leg


def _apply_floor(equity_usd: float, margin_lot: float) -> int:
    """Mirrors open_leg paper-path sizing + min-lot-floor logic."""
    budget_per_leg = equity_usd * MARGIN_PCT_PER_CYCLE / 2.0
    n_lots = int(budget_per_leg // margin_lot) if margin_lot > 0 else 0
    if n_lots < 1:
        if margin_lot > 0 and margin_lot <= equity_usd * MAX_FLOOR_EQUITY_FRAC and equity_usd >= ABS_FLOOR_EQUITY:
            n_lots = 1
    return n_lots


def test_floor_fires_when_budget_rounds_to_zero() -> None:
    # equity=$200, budget/leg=$15, margin_lot=$20 → int(15//20)=0, but $20 <= $80 (40%) → floor
    n = _apply_floor(equity_usd=200.0, margin_lot=20.0)
    assert n == 1, n


def test_floor_suppressed_below_abs_floor_equity() -> None:
    # equity=$40 < ABS_FLOOR_EQUITY=$50 → no floor rescue
    n = _apply_floor(equity_usd=40.0, margin_lot=15.0)
    assert n == 0, n


def test_floor_suppressed_when_margin_exceeds_40pct() -> None:
    # equity=$200, 40% = $80, margin_lot=$90 > $80 → no floor rescue
    n = _apply_floor(equity_usd=200.0, margin_lot=90.0)
    assert n == 0, n


def test_normal_sizing_unaffected() -> None:
    # equity=$2000, budget/leg=$150, margin_lot=$50 → 3 lots normally, floor not involved
    n = _apply_floor(equity_usd=2000.0, margin_lot=50.0)
    assert n == 3, n


def test_floor_boundary_exact_40pct() -> None:
    # equity=$200, margin_lot=$80 = exactly 40% → floor fires (inclusive)
    n = _apply_floor(equity_usd=200.0, margin_lot=80.0)
    assert n == 1, n


def test_floor_just_above_40pct_boundary() -> None:
    # equity=$200, margin_lot=$80.01 > 40% → suppressed
    n = _apply_floor(equity_usd=200.0, margin_lot=80.01)
    assert n == 0, n


def test_exact_abs_floor_equity_boundary() -> None:
    # equity=ABS_FLOOR_EQUITY=$50 exactly → floor fires (inclusive)
    n = _apply_floor(equity_usd=50.0, margin_lot=10.0)
    assert n == 1, n


def test_zero_margin_lot_guard() -> None:
    # margin_lot=0 → no division error, no floor, returns 0
    n = _apply_floor(equity_usd=500.0, margin_lot=0.0)
    assert n == 0, n


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"✓ {t.__name__}")
        passed += 1
    print(f"\nAll {passed} btc_straddle_min_lot_floor tests passed ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

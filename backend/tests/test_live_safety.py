"""Unit tests for live_safety (P4) — pure gate math, no network/DB.

Run: cd backend && PYTHONPATH=. python3 tests/test_live_safety.py
"""
from __future__ import annotations

import sys

from services import live_safety as s


def _approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_spread_pct() -> None:
    assert _approx(s.spread_pct(10, 10.2), (0.2 / 10.1) * 100)


def test_spread_pct_guards() -> None:
    assert s.spread_pct(0, 10) is None
    assert s.spread_pct(10, 0) is None
    assert s.spread_pct(11, 10) is None  # ask < bid


def test_spread_ok() -> None:
    assert s.spread_ok(10, 10.2, max_spread_pct=15) is True
    assert s.spread_ok(10, 14, max_spread_pct=15) is False   # (4/12)*100 = 33%
    assert s.spread_ok(0, 10, max_spread_pct=15) is False    # unusable quote


def test_slippage_sell() -> None:
    assert _approx(s.slippage_pct(40, 38, "sell"), 5.0)    # filled lower = worse
    assert _approx(s.slippage_pct(40, 42, "sell"), -5.0)   # filled higher = better


def test_slippage_buy() -> None:
    assert _approx(s.slippage_pct(12, 13, "buy"), (1 / 12) * 100)  # paid more = worse
    assert _approx(s.slippage_pct(12, 11, "buy"), -(1 / 12) * 100)


def test_slippage_alarming() -> None:
    assert s.slippage_alarming(40, 28, "sell", max_slippage_pct=25) is True   # 30%
    assert s.slippage_alarming(40, 38, "sell", max_slippage_pct=25) is False  # 5%


def test_daily_loss_limit() -> None:
    assert s.daily_loss_limit_hit(-150, limit_usd=100) is True
    assert s.daily_loss_limit_hit(-50, limit_usd=100) is False
    assert s.daily_loss_limit_hit(+200, limit_usd=100) is False
    assert s.daily_loss_limit_hit(-150, limit_usd=0) is False   # limit off


def test_utc_day_start() -> None:
    day = 86_400_000
    assert s.utc_day_start_ms(day * 100 + 12_345) == day * 100
    assert s.utc_day_start_ms(day * 100) == day * 100


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"✓ {t.__name__}")
        passed += 1
    print(f"\nAll {passed} live_safety tests passed ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

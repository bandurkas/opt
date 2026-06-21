"""Unit tests for paper_loop.call_dollar_sl_pct — ETH Call dollar-margin SL, no network/DB.

Run: cd backend && PYTHONPATH=. python3 tests/test_call_dollar_sl.py
"""
from __future__ import annotations

import sys

from services.paper_loop import call_dollar_sl_pct
from services.strategy_config import CALL_SL_DOLLAR_FRAC


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def test_known_value_default_frac() -> None:
    # margin = 0.10*3000 + 30 = 330; sl_pct = 0.10*330/30 = 1.1
    pct = call_dollar_sl_pct(3000, 30, sl_dollar_frac=0.10, im_rate=0.10)
    assert _approx(pct, 1.1), pct


def test_default_frac_matches_config() -> None:
    # calling with no override uses CALL_SL_DOLLAR_FRAC from strategy_config (0.10 default)
    pct_default = call_dollar_sl_pct(3000, 30)
    pct_explicit = call_dollar_sl_pct(3000, 30, sl_dollar_frac=CALL_SL_DOLLAR_FRAC)
    assert _approx(pct_default, pct_explicit), (pct_default, pct_explicit)


def test_zero_or_negative_credit_guard() -> None:
    assert call_dollar_sl_pct(3000, 0) == 0.0
    assert call_dollar_sl_pct(3000, -5) == 0.0


def test_scales_with_strike() -> None:
    # higher strike -> bigger margin -> looser (bigger) sl_pct at the same entry credit
    low = call_dollar_sl_pct(2000, 30)
    high = call_dollar_sl_pct(4000, 30)
    assert high > low, (low, high)


def test_scales_inversely_with_entry_credit() -> None:
    # this is the whole point of the dollar-margin fix: smaller premium (e.g. near
    # expiry) -> smaller denominator -> LOOSER %-equivalent SL than a fixed sl_pct
    # would give, instead of the %-premium SL getting artificially tighter.
    rich = call_dollar_sl_pct(3000, 60)
    cheap = call_dollar_sl_pct(3000, 5)
    assert cheap > rich, (rich, cheap)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"✓ {t.__name__}")
        passed += 1
    print(f"\nAll {passed} call_dollar_sl tests passed ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

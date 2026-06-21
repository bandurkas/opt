"""Unit tests for live_sizing (P2) — pure math, no network.

Run: cd backend && PYTHONPATH=. python3 tests/test_live_sizing.py
"""
from __future__ import annotations

import sys

from services import live_sizing as ls


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def test_per_lot_im_known_value() -> None:
    # (im_rate*strike + premium) * lot = (0.10*1700 + 40) * 0.1 = 21.0
    im = ls.estimate_per_lot_im(1700, 40, im_rate=0.10)
    assert _approx(im, 21.0), im


def test_per_lot_im_guards() -> None:
    assert ls.estimate_per_lot_im(0, 40) == 0.0
    assert ls.estimate_per_lot_im(1700, -1) == 0.0
    assert ls.estimate_per_lot_im(1700, 40, lot=0) == 0.0


def test_max_lots_budget_binding() -> None:
    # available 1000 * util 0.5 = 500 budget; per_lot 21 -> 23 lots
    assert ls.max_lots(1000, 21, utilization=0.5) == 23


def test_max_lots_capital_cap_binding() -> None:
    # budget would be 500 but max_capital floors to 100 -> 100//21 = 4
    assert ls.max_lots(10000, 21, utilization=0.5, max_capital_usdt=100) == 4


def test_max_lots_lots_cap_binding() -> None:
    assert ls.max_lots(10000, 21, utilization=0.5, lots_cap=3) == 3


def test_max_lots_zero_cases() -> None:
    assert ls.max_lots(0, 21, utilization=0.5) == 0
    assert ls.max_lots(1000, 0, utilization=0.5) == 0
    assert ls.max_lots(1000, 21, utilization=0) == 0


def test_plan_refuses_below_min_wallet() -> None:
    r = ls.plan_lots(available_usdt=10, strike=1700, premium_mid=40,
                     min_wallet_usdt=50)
    assert r.n_lots == 0 and not r.ok
    assert "min_wallet" in (r.reason or "")


def test_plan_normal() -> None:
    r = ls.plan_lots(available_usdt=1000, strike=1700, premium_mid=40,
                     im_rate=0.10, utilization=0.5, lots_cap=0,
                     max_capital_usdt=0, min_wallet_usdt=50)
    assert r.n_lots == 23, r
    assert _approx(r.qty_eth, 2.3), r
    assert _approx(r.per_lot_im_usdt, 21.0), r
    assert _approx(r.est_im_usdt, 483.0), r
    assert r.reason is None and r.ok


def test_plan_sub_one_lot() -> None:
    # per_lot = (0.10*5000 + 100)*0.1 = 60; budget = 100*0.5 = 50 < 60 -> 0 lots
    r = ls.plan_lots(available_usdt=100, strike=5000, premium_mid=100,
                     im_rate=0.10, utilization=0.5, min_wallet_usdt=50)
    assert r.n_lots == 0 and not r.ok
    assert "1 lot IM" in (r.reason or ""), r


def test_plan_uses_config_defaults() -> None:
    # No explicit caps -> defaults from execution_config (util 0.5, min_wallet 50,
    # max_capital 1000). available 2000*0.5=1000 budget, capped to 1000; per_lot 21
    # -> 47 lots (1000//21).
    r = ls.plan_lots(available_usdt=2000, strike=1700, premium_mid=40)
    assert r.n_lots == 47, r


def test_plan_lots_btc_lot_size() -> None:
    # BTC: lot=0.01, im_rate=0.10, strike=65000, premium=300 ->
    # per_lot = (0.10*65000 + 300)*0.01 = 68.0
    r = ls.plan_lots(available_usdt=2000, strike=65000, premium_mid=300,
                     im_rate=0.10, utilization=0.5, lots_cap=0,
                     max_capital_usdt=0, min_wallet_usdt=50, lot_size=0.01)
    assert _approx(r.per_lot_im_usdt, 68.0), r
    assert _approx(r.qty_eth, r.n_lots * 0.01), r


def test_plan_lots_default_lot_size_is_eth_unchanged() -> None:
    # Same as test_plan_normal — proves adding lot_size didn't change ETH's default path.
    r = ls.plan_lots(available_usdt=1000, strike=1700, premium_mid=40,
                     im_rate=0.10, utilization=0.5, lots_cap=0,
                     max_capital_usdt=0, min_wallet_usdt=50)
    assert r.n_lots == 23 and _approx(r.qty_eth, 2.3), r


def test_reduce_lots() -> None:
    assert ls.reduce_lots(5) == 4
    assert ls.reduce_lots(1) == 0
    assert ls.reduce_lots(0) == 0
    assert ls.reduce_lots(5, step=2) == 3


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"✓ {t.__name__}")
        passed += 1
    print(f"\nAll {passed} live_sizing tests passed ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

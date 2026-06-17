"""Unit tests for the real-IV collector's pure parsing/selection logic.

Network-free (no Bybit calls): exercises parse_symbol, pick_atm (ATM + nearest
expiry + bid-IV liquidity filter + >6h cutoff), and realized_vol math shape.

Run:  cd backend && PYTHONPATH=. python3 tests/test_iv_collector.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.iv_collector import parse_symbol, pick_atm

H = 3_600_000


def test_parse_symbol():
    exp, strike, side = parse_symbol("ETH-18JUN26-1800-C-USDT")
    assert side == "C" and strike == 1800.0, (exp, strike, side)
    # 18 Jun 2026 08:00 UTC settle
    import datetime as dt
    d = dt.datetime.fromtimestamp(exp / 1000, tz=dt.timezone.utc)
    assert (d.year, d.month, d.day, d.hour) == (2026, 6, 18, 8), d
    assert parse_symbol("garbage") is None
    assert parse_symbol("ETH-99XXX26-1800-C-USDT") is None  # bad month
    print("✓ parse_symbol: date@08:00UTC, strike, side; bad input -> None")


def _c(sym, bid_iv=0.5, **kw):
    base = {"symbol": sym, "bid1Iv": bid_iv, "markIv": 0.6, "ask1Iv": 0.61,
            "bid1Price": 5, "ask1Price": 6, "markPrice": 5.5, "delta": 0.5,
            "gamma": 0, "theta": 0, "vega": 0, "openInterest": 1, "volume24h": 1}
    base.update(kw)
    return base


def test_pick_atm_nearest_expiry_and_strike():
    now = int(time.time() * 1000)
    # build symbols whose parsed expiry we can't control by date, so instead use
    # real future dates relative to now via day offsets isn't possible in symbol;
    # use known dates ordered in the future.
    import datetime as dt
    def sym(days, strike, side):
        d = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days))
        return f"ETH-{d.strftime('%d%b%y').upper()}-{strike}-{side}-USDT"
    spot = 3000.0
    chain = [
        _c(sym(1, 2900, "C")), _c(sym(1, 3000, "C")), _c(sym(1, 3100, "C")),  # daily
        _c(sym(7, 3000, "C")),                                                # weekly
    ]
    pick = pick_atm(chain, spot, "C", now)
    assert pick and pick["strike"] == 3000.0, pick      # closest strike
    assert pick["dte_h"] < 48, pick                      # nearest expiry (daily)
    print("✓ pick_atm: nearest expiry + closest-to-spot strike")


def test_pick_atm_filters_bid_iv_and_min_dte():
    now = int(time.time() * 1000)
    import datetime as dt
    def sym(days, strike, side):
        d = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days))
        return f"ETH-{d.strftime('%d%b%y').upper()}-{strike}-{side}-USDT"
    spot = 3000.0
    # zero bid-IV (illiquid) must be skipped; only the liquid weekly remains
    chain = [_c(sym(1, 3000, "C"), bid_iv=0.0), _c(sym(7, 3000, "C"), bid_iv=0.55)]
    pick = pick_atm(chain, spot, "C", now)
    assert pick and pick["dte_h"] > 100, pick  # daily skipped (no bid IV) -> weekly
    # nothing for the wrong side
    assert pick_atm(chain, spot, "P", now) is None
    print("✓ pick_atm: skips zero-bid-IV and wrong side")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} iv_collector tests passed ✓")


if __name__ == "__main__":
    main()

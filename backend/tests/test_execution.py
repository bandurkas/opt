"""Unit tests for ExecutionClient limit->market fallback, using a mock HTTP
session (no network, no keys). Run standalone:

    cd backend && PYTHONPATH=. python3 tests/test_execution.py

Or via pytest:  cd backend && python3 -m pytest tests/test_execution.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import execution_config as cfg
from services.execution import ExecutionClient

# Make fills resolve instantly (no real waiting in tests).
cfg.LIMIT_TIMEOUT_S = 0
cfg.LIMIT_POLL_S = 0

TICK = {"result": {"list": [{
    "priceFilter": {"tickSize": "0.1"},
    "lotSizeFilter": {"qtyStep": "0.1", "minOrderQty": "0.1"},
}]}}


class FakeHTTP:
    """Simulates Bybit: Limit orders fill `limit_fill` ETH at `limit_price`;
    Market orders fill fully at `market_price`. fee = 0.01*qty per leg.
    Set place_fails=True to simulate a rejected order."""

    def __init__(self, limit_fill=0.2, limit_price=10.0, market_price=9.5,
                 place_fails=False):
        self.orders: dict[str, dict] = {}
        self._n = 0
        self.limit_fill = limit_fill
        self.limit_price = limit_price
        self.market_price = market_price
        self.place_fails = place_fails
        self.placed: list[dict] = []

    def get_instruments_info(self, category, symbol):
        return TICK

    def place_order(self, **p):
        self.placed.append(p)
        if self.place_fails:
            return {"result": {}}
        self._n += 1
        oid = f"o{self._n}"
        qty = float(p["qty"])
        if p["orderType"] == "Limit":
            filled = min(self.limit_fill, qty)
            status = ("Filled" if abs(filled - qty) < 1e-9
                      else "PartiallyFilled" if filled > 0 else "New")
            self.orders[oid] = {"orderStatus": status, "cumExecQty": filled,
                                "avgPrice": self.limit_price if filled > 0 else 0,
                                "cumExecFee": 0.01 * filled}
        else:  # Market — fills fully
            self.orders[oid] = {"orderStatus": "Filled", "cumExecQty": qty,
                                "avgPrice": self.market_price, "cumExecFee": 0.01 * qty}
        return {"result": {"orderId": oid}}

    def get_open_orders(self, category, symbol, orderId):
        o = self.orders.get(orderId)
        return {"result": {"list": [o] if o else []}}

    def get_order_history(self, category, symbol, orderId):
        o = self.orders.get(orderId)
        return {"result": {"list": [o] if o else []}}

    def cancel_order(self, category, symbol, orderId):
        o = self.orders.get(orderId)
        if o and o["orderStatus"] in ("New", "PartiallyFilled"):
            o["orderStatus"] = "Cancelled"  # keeps cumExecQty
        return {"result": {}}


def _client(fake):
    return ExecutionClient(session=fake)


def test_limit_full_fill():
    fake = FakeHTTP(limit_fill=0.2, limit_price=10.0)
    r = _client(fake).sell_to_open("ETH-X", 0.2, ref_mid=10.0)
    assert r is not None and r.status == "Filled", r
    assert abs(r.filled_qty - 0.2) < 1e-9, r
    assert abs(r.avg_price - 10.0) < 1e-9, r
    # only one (limit) order placed
    assert len(fake.placed) == 1 and fake.placed[0]["orderType"] == "Limit"
    print("✓ limit full fill")


def test_limit_partial_then_market():
    fake = FakeHTTP(limit_fill=0.1, limit_price=10.0, market_price=9.0)
    r = _client(fake).sell_to_open("ETH-X", 0.2, ref_mid=10.0)
    assert r is not None and r.status == "Filled", r
    assert abs(r.filled_qty - 0.2) < 1e-9, r
    # weighted avg of 0.1@10 + 0.1@9 = 9.5
    assert abs(r.avg_price - 9.5) < 1e-6, r
    types = [o["orderType"] for o in fake.placed]
    assert types == ["Limit", "Market"], types
    # market leg qty = remaining 0.1
    assert abs(float(fake.placed[1]["qty"]) - 0.1) < 1e-9
    print("✓ partial limit + market sweep (weighted avg)")


def test_limit_nofill_then_market():
    fake = FakeHTTP(limit_fill=0.0, market_price=9.0)
    r = _client(fake).sell_to_open("ETH-X", 0.2, ref_mid=10.0)
    assert r is not None and r.status == "Filled", r
    assert abs(r.filled_qty - 0.2) < 1e-9 and abs(r.avg_price - 9.0) < 1e-9, r
    print("✓ no limit fill -> full market")


def test_buy_to_close_reduce_only():
    fake = FakeHTTP(limit_fill=0.2, limit_price=5.0)
    r = _client(fake).buy_to_close("ETH-X", 0.2, ref_mid=5.0)
    assert r is not None and r.is_filled, r
    assert fake.placed[0]["side"] == "Buy" and fake.placed[0]["reduceOnly"] is True
    print("✓ buy_to_close uses Buy + reduceOnly")


def test_place_failure_returns_none():
    fake = FakeHTTP(place_fails=True)
    r = _client(fake).sell_to_open("ETH-X", 0.2, ref_mid=10.0)
    assert r is None, r
    print("✓ place failure -> None (no assumed fill)")


def test_qty_below_min_returns_none():
    fake = FakeHTTP()
    r = _client(fake).sell_to_open("ETH-X", 0.04, ref_mid=10.0)  # rounds to 0
    assert r is None, r
    assert len(fake.placed) == 0, "must not place an order for sub-min qty"
    print("✓ sub-minimum qty -> None, no order placed")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} execution tests passed ✓")


if __name__ == "__main__":
    main()

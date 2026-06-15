"""Unit tests for broker (P3) — reduce-on-reject + never-assume-fill.

Uses a fake ExecutionClient (no network). Run:
  cd backend && PYTHONPATH=. python3 tests/test_broker.py
"""
from __future__ import annotations

import sys

from services import broker
from services.execution import OrderResult


class FakeClient:
    """Simulates Bybit: only fills orders at or below `max_fill_lots`; above that
    (too much margin) returns None — drives the reduce-on-reject loop."""

    def __init__(self, avail: float | None, *, max_fill_lots: int = 999,
                 close_fills: bool = True, fee_rate: float = 0.0003):
        self.avail = avail
        self.max_fill_lots = max_fill_lots
        self.close_fills = close_fills
        self.fee_rate = fee_rate
        self.sell_calls: list[float] = []
        self.close_calls: list[float] = []

    def available_usdt(self):
        return self.avail

    def wallet_usdt(self):
        return self.avail

    def sell_to_open(self, symbol, qty, ref_mid):
        self.sell_calls.append(qty)
        n_lots = round(qty / 0.1)
        if n_lots > self.max_fill_lots:
            return None  # exchange rejects (insufficient margin)
        return OrderResult(f"oid{n_lots}", ref_mid, qty, qty * ref_mid * self.fee_rate, "Filled")

    def buy_to_close(self, symbol, qty, ref_mid):
        self.close_calls.append(qty)
        if not self.close_fills:
            return None
        return OrderResult("coid", ref_mid, qty, qty * ref_mid * self.fee_rate, "Filled")


def test_open_full_fill() -> None:
    # avail 1000 → budget 500, per_lot 21 → 23 lots; exchange fills all.
    fc = FakeClient(1000.0)
    broker.set_client(fc)
    fill = broker.live_open("ETH-X-1700-P-USDT", 1700, 40)
    assert fill is not None and fill.n_lots == 23, fill
    assert abs(fill.qty_eth - 2.3) < 1e-9, fill
    assert fill.avg_price == 40 and fill.status == "Filled"
    assert len(fc.sell_calls) == 1  # filled on first try, no reduce


def test_open_reduce_on_reject() -> None:
    # Exchange only fills <=5 lots → loop must reduce 23→5.
    fc = FakeClient(1000.0, max_fill_lots=5)
    broker.set_client(fc)
    fill = broker.live_open("ETH-X-1700-P-USDT", 1700, 40)
    assert fill is not None and fill.n_lots == 5, fill
    assert len(fc.sell_calls) == 23 - 5 + 1  # tried 23,22,...,5


def test_open_all_reject_returns_none() -> None:
    fc = FakeClient(1000.0, max_fill_lots=0)
    broker.set_client(fc)
    assert broker.live_open("ETH-X-1700-P-USDT", 1700, 40) is None
    assert len(fc.sell_calls) == 23  # tried every lot count down to 1


def test_open_below_min_wallet_no_order() -> None:
    fc = FakeClient(10.0)  # < LIVE_MIN_WALLET_USDT (50) default
    broker.set_client(fc)
    assert broker.live_open("ETH-X-1700-P-USDT", 1700, 40) is None
    assert fc.sell_calls == []  # never hit the exchange


def test_open_no_available_balance() -> None:
    fc = FakeClient(None)  # wallet read failed
    broker.set_client(fc)
    assert broker.live_open("ETH-X-1700-P-USDT", 1700, 40) is None
    assert fc.sell_calls == []


def test_close_filled() -> None:
    fc = FakeClient(500.0)
    broker.set_client(fc)
    fill = broker.live_close("ETH-X-1700-P-USDT", 0.5, 12)
    assert fill is not None and abs(fill.qty_eth - 0.5) < 1e-9
    assert fill.avg_price == 12


def test_close_not_filled_returns_none() -> None:
    fc = FakeClient(500.0, close_fills=False)
    broker.set_client(fc)
    assert broker.live_close("ETH-X-1700-P-USDT", 0.5, 12) is None


def test_is_live_false_in_paper_mode() -> None:
    # Default env is TRADING_MODE=paper → never armed.
    assert broker.is_live() is False


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        broker.set_client(None)  # reset between tests
        t()
        print(f"✓ {t.__name__}")
        passed += 1
    print(f"\nAll {passed} broker tests passed ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

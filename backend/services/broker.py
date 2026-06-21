"""Broker indirection (P3): routes paper_loop's open/close to the REAL Bybit
exchange when armed, otherwise stays out of the way so paper trading runs its
existing simulation unchanged.

`is_live()` is the single gate — it is True only when
``execution_config.trading_armed()`` (TRADING_MODE != paper AND LIVE_ENABLED AND
no kill-switch). In paper mode it is False and none of this module's exchange
calls run.

Money-path rules (inherited from execution.py):
  - Never assume a fill. Every result is read back from the exchange; an order we
    can't confirm filled returns None and the caller treats it as not-done.
  - Open uses reduce-on-reject: size off the real wallet (live_sizing), then if
    the exchange rejects or doesn't fill, drop a lot and retry down to zero.
  - Close NEVER marks a position done unless the buy-back actually filled — else
    the DB and the exchange would diverge.
"""
from __future__ import annotations

from typing import Any, NamedTuple

from . import execution_config as cfg
from . import live_sizing


class LiveFill(NamedTuple):
    avg_price: float    # real weighted-avg fill (premium/ETH, USDT)
    qty_eth: float      # real filled qty (ETH)
    fee: float          # real total fees (USDT)
    n_lots: int         # lots that actually filled (qty_eth / 0.1)
    status: str         # 'Filled' | 'PartiallyFilled'


def is_live() -> bool:
    """True only when real orders are armed. Paper mode → False (no exchange calls)."""
    return cfg.trading_armed()


# Lazily-built singleton so importing this module never touches pybit/network.
_client: Any = None


def _get_client() -> Any:
    global _client
    if _client is None:
        from .execution import ExecutionClient
        _client = ExecutionClient()
    return _client


def set_client(client: Any) -> None:
    """Inject a client (for tests / reuse)."""
    global _client
    _client = client


def available_usdt() -> float | None:
    return _get_client().available_usdt()


def wallet_equity_usdt() -> float | None:
    """Real wallet balance (USDT) — the live equity base (replaces DB equity)."""
    return _get_client().wallet_usdt()


def live_open(symbol: str, strike: float, premium_mid: float, *,
              lot_size: float = live_sizing.LOT_ETH) -> LiveFill | None:
    """Size off the real wallet and sell-to-open, with reduce-on-reject.

    ``lot_size`` defaults to ETH's 0.1 so the ETH call site is unaffected; pass
    e.g. 0.01 for BTC's lot.

    Returns a LiveFill with REAL avg price / qty / fee, or None if nothing filled
    (insufficient margin all the way down, or unconfirmable fill).
    """
    client = _get_client()
    avail = client.available_usdt()
    if avail is None:
        print("[broker] live_open abort — could not read available USDT", flush=True)
        return None

    plan = live_sizing.plan_lots(available_usdt=avail, strike=strike, premium_mid=premium_mid,
                                 lot_size=lot_size)
    if not plan.ok:
        print(f"[broker] live_open skip — {plan.reason}", flush=True)
        return None

    n = plan.n_lots
    while n > 0:
        qty = round(n * lot_size, 4)
        res = client.sell_to_open(symbol, qty, premium_mid)
        if res is not None and res.is_filled:
            return LiveFill(res.avg_price, res.filled_qty, res.fees, n, res.status)
        # rejected / no confirmable fill → reduce a lot and retry (reduce-on-reject)
        n = live_sizing.reduce_lots(n)
        if n > 0:
            print(f"[broker] sell_to_open not filled — retry at {n} lot(s)", flush=True)
    print("[broker] live_open failed — no fill down to 0 lots", flush=True)
    return None


def live_close(symbol: str, qty: float, premium_mid: float, *,
               lot_size: float = live_sizing.LOT_ETH) -> LiveFill | None:
    """Buy-to-close the short. Returns the REAL fill, or None if NOT confirmed
    filled — in which case the caller must NOT mark the position closed."""
    client = _get_client()
    res = client.buy_to_close(symbol, qty, premium_mid)
    if res is not None and res.is_filled:
        n = int(round(res.filled_qty / lot_size))
        return LiveFill(res.avg_price, res.filled_qty, res.fees, n, res.status)
    print(f"[broker] live_close NOT filled for {symbol} qty={qty} — leaving open", flush=True)
    return None

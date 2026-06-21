"""Margin-bound position sizing for LIVE Bybit options trading (P2).

Unlike the paper model (which sizes off a fixed $400 equity × 15% and a 10%-IM
approximation), live sizing must come from the **real available USDT balance** on
the Bybit UTA, scaled by a safety buffer (``LIVE_MARGIN_UTILIZATION``), divided by
the per-lot initial margin, then clamped to the configured caps.

The exchange is the final authority. The IM here is an APPROXIMATION used only to
pre-size; if Bybit still rejects an order for margin, the broker layer (P3) calls
``reduce_lots`` and retries with fewer lots until it fills or reaches zero
(reduce-on-reject). No order is ever assumed to fit.

All amounts are USDT (Bybit ETH options are USDT-settled). qty is in ETH; the lot
unit is 0.1 ETH (min + step), so qty = n_lots × 0.1.

Pure / dependency-light (only execution_config defaults) so it unit-tests without
network — mirrors the design of execution.py.
"""
from __future__ import annotations

from typing import NamedTuple

from . import execution_config as cfg

# Bybit ETH option lot: min order qty and step are both 0.1 ETH.
LOT_ETH = 0.10


class SizingResult(NamedTuple):
    n_lots: int            # whole 0.1-ETH lots to short
    qty_eth: float         # n_lots × 0.1 (what goes to the order)
    est_im_usdt: float     # estimated initial margin the position will lock
    per_lot_im_usdt: float # estimated IM per single lot
    reason: str | None     # non-None when n_lots == 0 (why we can't/​won't size)

    @property
    def ok(self) -> bool:
        return self.n_lots > 0


def estimate_per_lot_im(strike: float, premium_mid: float, *,
                        im_rate: float | None = None, lot: float = LOT_ETH) -> float:
    """Approx initial margin (USDT) to short ONE 0.1-ETH lot of an ETH option.

    Bybit short-option IM ≈ (im_rate · strike + premium) · contracts. This mirrors
    the paper model so paper↔live sizing stay comparable. Approximation only — the
    exchange recomputes and is authoritative.
    """
    if im_rate is None:
        im_rate = cfg.LIVE_IM_RATE_EST
    if strike <= 0 or premium_mid < 0 or lot <= 0:
        return 0.0
    return (im_rate * strike + premium_mid) * lot


def max_lots(available_usdt: float, per_lot_im: float, *,
             utilization: float, lots_cap: int = 0,
             max_capital_usdt: float = 0.0) -> int:
    """Largest number of lots whose total estimated IM fits the margin budget.

    budget = available_usdt × utilization, optionally floored to max_capital_usdt
    (0 = unlimited). Result is clamped to lots_cap (0 = unlimited).
    """
    if available_usdt <= 0 or per_lot_im <= 0 or utilization <= 0:
        return 0
    budget = available_usdt * utilization
    if max_capital_usdt and max_capital_usdt > 0:
        budget = min(budget, max_capital_usdt)
    n = int(budget // per_lot_im)
    if lots_cap and lots_cap > 0:
        n = min(n, lots_cap)
    return max(0, n)


def plan_lots(*, available_usdt: float, strike: float, premium_mid: float,
              im_rate: float | None = None,
              utilization: float | None = None,
              lots_cap: int | None = None,
              max_capital_usdt: float | None = None,
              min_wallet_usdt: float | None = None,
              lot_size: float = LOT_ETH) -> SizingResult:
    """Full live sizing decision off the REAL available USDT balance.

    Refuses (n_lots=0) when the wallet is below ``min_wallet_usdt`` or the budget
    can't cover a single lot's IM. Caps default to the live execution config.
    ``lot_size`` defaults to ``LOT_ETH`` (0.1 ETH) so every existing ETH call site
    is unaffected; pass e.g. 0.01 for BTC's lot.
    """
    utilization = cfg.LIVE_MARGIN_UTILIZATION if utilization is None else utilization
    lots_cap = cfg.LIVE_PER_TRADE_LOTS_CAP if lots_cap is None else lots_cap
    max_capital_usdt = cfg.LIVE_MAX_CAPITAL_USDT if max_capital_usdt is None else max_capital_usdt
    min_wallet_usdt = cfg.LIVE_MIN_WALLET_USDT if min_wallet_usdt is None else min_wallet_usdt

    if available_usdt is None or available_usdt < min_wallet_usdt:
        return SizingResult(0, 0.0, 0.0, 0.0,
                            f"available ${available_usdt} < min_wallet ${min_wallet_usdt:.2f}")

    per_lot = estimate_per_lot_im(strike, premium_mid, im_rate=im_rate, lot=lot_size)
    if per_lot <= 0:
        return SizingResult(0, 0.0, 0.0, 0.0, f"bad strike/premium (strike={strike}, mid={premium_mid})")

    n = max_lots(available_usdt, per_lot, utilization=utilization,
                 lots_cap=lots_cap, max_capital_usdt=max_capital_usdt)
    if n <= 0:
        budget = available_usdt * utilization
        return SizingResult(0, 0.0, 0.0, round(per_lot, 4),
                            f"budget ${budget:.2f} < 1 lot IM ${per_lot:.2f}")
    return SizingResult(n, round(n * lot_size, 4), round(n * per_lot, 4), round(per_lot, 4), None)


def reduce_lots(n_lots: int, step: int = 1) -> int:
    """Reduce-on-reject: step the lot count down (exchange rejected for margin).

    Returns the new (smaller) lot count, floored at 0. The broker layer retries
    sell_to_open with this until it fills or reaches 0.
    """
    return max(0, int(n_lots) - max(1, int(step)))

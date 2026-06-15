"""Live-mode pre-open safety gates (P4).

Pure helpers (spread / slippage / daily-loss math) so they unit-test without
network or DB. All of these apply ONLY in live mode — callers gate on
``broker.is_live()``; in paper mode none of this runs and behaviour is unchanged.

Layers (checked before/after a live open):
  - kill-switch                : handled by trading_armed()/is_live() + a re-check
  - min wallet                 : enforced in live_sizing.plan_lots
  - daily realized-loss limit  : daily_loss_limit_hit() — halts new opens for the day
  - spread / liquidity guard   : spread_ok() — skip illiquid (wide bid/ask) options
  - post-fill slippage alert   : slippage_pct() — alert (don't block) on bad fills
"""
from __future__ import annotations

from . import execution_config as cfg


def spread_pct(bid: float, ask: float) -> float | None:
    """Bid/ask spread as % of mid, or None if quotes are unusable."""
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid * 100.0


def spread_ok(bid: float, ask: float, max_spread_pct: float | None = None) -> bool:
    """True if the option is liquid enough to trade (spread ≤ cap)."""
    cap = cfg.MAX_SPREAD_PCT if max_spread_pct is None else max_spread_pct
    sp = spread_pct(bid, ask)
    return sp is not None and sp <= cap


def slippage_pct(expected_mid: float, fill_avg: float, side: str) -> float:
    """Adverse slippage %, positive = worse than expected.

    side 'sell' (open): worse when filled BELOW mid (we got less premium).
    side 'buy' (close): worse when filled ABOVE mid (we paid more to buy back).
    """
    if expected_mid <= 0 or fill_avg <= 0:
        return 0.0
    if side == "sell":
        return (expected_mid - fill_avg) / expected_mid * 100.0
    return (fill_avg - expected_mid) / expected_mid * 100.0


def slippage_alarming(expected_mid: float, fill_avg: float, side: str,
                      max_slippage_pct: float | None = None) -> bool:
    cap = cfg.MAX_SLIPPAGE_PCT if max_slippage_pct is None else max_slippage_pct
    return slippage_pct(expected_mid, fill_avg, side) > cap


def daily_loss_limit_hit(realized_today_usd: float, limit_usd: float | None = None) -> bool:
    """True when today's realized loss exceeds the limit → halt new opens.

    ``realized_today_usd`` is signed (negative = net loss today). limit ≤ 0 = off.
    """
    lim = cfg.LIVE_DAILY_LOSS_LIMIT_USDT if limit_usd is None else limit_usd
    if lim <= 0:
        return False
    return (-realized_today_usd) >= lim


def utc_day_start_ms(now_ms: int) -> int:
    """Epoch-ms of 00:00 UTC for the day containing now_ms."""
    day_ms = 86_400_000
    return (now_ms // day_ms) * day_ms

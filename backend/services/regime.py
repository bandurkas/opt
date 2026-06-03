"""Trend vs range regime detection via ADX(14) on 1h."""
from __future__ import annotations

from .indicators import adx


def detect_regime(candles_1h: list[dict]) -> dict:
    a = adx(candles_1h, 14)
    if a is None:
        return {"regime": "unknown", "adx": None, "trend_strength": 0.0}
    # V3 (2026-06-03): trend cutoff raised 25→35 to widen the transition band.
    # Backtest 365d: lifts trade frequency 936→1509 with edge intact (+4.94%/trade,
    # 2 losing months) — fixes the trend-zone deadlock where Call was never eligible.
    if a > 35:
        regime = "trend"
        strength = min(1.0, (a - 35) / 25)  # 35→0, 60→1
    elif a < 20:
        regime = "range"
        strength = 0.0
    else:
        regime = "transition"
        strength = (a - 20) / 15  # 20→0, 35→1
    return {
        "regime": regime,
        "adx": round(a, 1),
        "trend_strength": round(strength, 2),
    }

"""Trend vs range regime detection via ADX(14) on 1h."""
from __future__ import annotations

from .indicators import adx


def detect_regime(candles_1h: list[dict]) -> dict:
    a = adx(candles_1h, 14)
    if a is None:
        return {"regime": "unknown", "adx": None, "trend_strength": 0.0}
    if a > 25:
        regime = "trend"
        strength = min(1.0, (a - 25) / 25)  # 25→0, 50→1
    elif a < 20:
        regime = "range"
        strength = 0.0
    else:
        regime = "transition"
        strength = (a - 20) / 5  # 20→0, 25→1
    return {
        "regime": regime,
        "adx": round(a, 1),
        "trend_strength": round(strength, 2),
    }

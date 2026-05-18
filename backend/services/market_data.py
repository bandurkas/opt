from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MarketSnapshot:
    spot: float
    direction: str        # "bullish" / "bearish" / "neutral"
    momentum_strong: bool
    volume_spike: bool
    rsi_1h: float
    ema_fast: float
    ema_slow: float
    change_1h_pct: float
    change_4h_pct: float
    nearest_resistance: float
    nearest_support: float
    fetched_at_ms: int


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _pivot_levels(candles: list[dict], spot: float) -> tuple[float, float]:
    """Find nearest resistance above and support below using recent swing highs/lows."""
    if not candles:
        return spot * 1.02, spot * 0.98
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    above = [h for h in highs if h > spot]
    below = [l for l in lows if l < spot]
    nearest_res = min(above) if above else spot * 1.02
    nearest_sup = max(below) if below else spot * 0.98
    return nearest_res, nearest_sup


def build_market_snapshot(spot: float, candles_1h: list[dict], now_ms: int) -> MarketSnapshot:
    """Derive trading-relevant market state from 1h candles + current spot."""
    closes = [c["close"] for c in candles_1h]
    volumes = [c["volume"] for c in candles_1h]

    ema_fast = _ema(closes[-30:], 9) if closes else spot
    ema_slow = _ema(closes[-30:], 21) if closes else spot
    rsi = _rsi(closes, 14)

    last_close = closes[-1] if closes else spot
    close_4h_ago = closes[-5] if len(closes) >= 5 else last_close
    close_1h_ago = closes[-2] if len(closes) >= 2 else last_close
    change_1h = ((last_close - close_1h_ago) / close_1h_ago * 100) if close_1h_ago else 0.0
    change_4h = ((last_close - close_4h_ago) / close_4h_ago * 100) if close_4h_ago else 0.0

    avg_vol = sum(volumes[-20:-1]) / max(1, len(volumes[-20:-1])) if len(volumes) >= 2 else 0.0
    last_vol = volumes[-1] if volumes else 0.0
    volume_spike = last_vol > avg_vol * 1.4 and avg_vol > 0

    bullish = ema_fast > ema_slow and rsi > 50 and change_4h > -0.5
    bearish = ema_fast < ema_slow and rsi < 50 and change_4h < 0.5
    if bullish and not bearish:
        direction = "bullish"
    elif bearish and not bullish:
        direction = "bearish"
    else:
        direction = "neutral"

    trend_strength = abs(ema_fast - ema_slow) / ema_slow * 100 if ema_slow else 0.0
    momentum_strong = trend_strength > 0.3 and (
        (direction == "bullish" and change_1h > 0) or (direction == "bearish" and change_1h < 0)
    )

    nearest_res, nearest_sup = _pivot_levels(candles_1h, spot)

    return MarketSnapshot(
        spot=spot,
        direction=direction,
        momentum_strong=momentum_strong,
        volume_spike=volume_spike,
        rsi_1h=round(rsi, 1),
        ema_fast=round(ema_fast, 2),
        ema_slow=round(ema_slow, 2),
        change_1h_pct=round(change_1h, 2),
        change_4h_pct=round(change_4h, 2),
        nearest_resistance=round(nearest_res, 2),
        nearest_support=round(nearest_sup, 2),
        fetched_at_ms=now_ms,
    )

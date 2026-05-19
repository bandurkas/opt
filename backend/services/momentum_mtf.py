"""Multi-timeframe momentum analysis. Consensus across 5m / 15m / 1h."""
from __future__ import annotations

from .indicators import ema, rsi, zscore


def analyze_tf(candles: list[dict]) -> dict:
    """Single-timeframe momentum state."""
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    r = rsi(closes, 14)
    vol_z = zscore(volumes, 20)

    last_close = closes[-1] if closes else 0.0
    prev_close = closes[-2] if len(closes) >= 2 else last_close
    change_pct = (last_close - prev_close) / prev_close * 100 if prev_close else 0.0

    # Direction strength
    if e20 is None or e50 is None or r is None:
        direction = "unknown"
        strength = 0.0
    elif e20 > e50 and r > 50:
        direction = "up"
        # gap between EMAs, normalized
        strength = min(1.0, (e20 - e50) / e50 * 100) if e50 else 0.0
    elif e20 < e50 and r < 50:
        direction = "down"
        strength = min(1.0, (e50 - e20) / e50 * 100) if e50 else 0.0
    else:
        direction = "neutral"
        strength = 0.0

    # Accelerating / decelerating / divergent (momentum classification)
    # Heuristic: if change_pct AND direction aligned & |change| above noise → accelerating.
    if direction == "up" and change_pct > 0.05:
        momentum = "accelerating"
    elif direction == "down" and change_pct < -0.05:
        momentum = "accelerating"
    elif direction in ("up", "down") and abs(change_pct) < 0.05:
        momentum = "decelerating"
    elif direction == "up" and change_pct < -0.1:
        momentum = "divergent"
    elif direction == "down" and change_pct > 0.1:
        momentum = "divergent"
    else:
        momentum = "flat"

    return {
        "direction": direction,
        "strength": round(strength, 3),
        "momentum": momentum,
        "ema20": round(e20, 2) if e20 is not None else None,
        "ema50": round(e50, 2) if e50 is not None else None,
        "rsi": round(r, 1) if r is not None else None,
        "volume_zscore": round(vol_z, 2) if vol_z is not None else None,
        "change_pct": round(change_pct, 3),
        "last_close": round(last_close, 2),
    }


def consensus(tf_5m: dict, tf_15m: dict, tf_1h: dict) -> dict:
    """Aggregate three timeframes into one direction + agreement score."""
    tfs = [tf_5m, tf_15m, tf_1h]
    ups = sum(1 for tf in tfs if tf["direction"] == "up")
    downs = sum(1 for tf in tfs if tf["direction"] == "down")
    total = len(tfs)

    if ups >= 2 and ups > downs:
        direction = "up"
        aligned = ups
    elif downs >= 2 and downs > ups:
        direction = "down"
        aligned = downs
    else:
        direction = "neutral"
        aligned = max(ups, downs)

    # Accelerating only if 1h is accelerating in the consensus direction.
    accelerating = False
    if direction == "up" and tf_1h.get("momentum") == "accelerating":
        accelerating = True
    elif direction == "down" and tf_1h.get("momentum") == "accelerating":
        accelerating = True

    return {
        "direction": direction,
        "agreement": round(aligned / total, 2),
        "tfs_aligned": aligned,
        "tfs_total": total,
        "accelerating": accelerating,
        "tf_5m": tf_5m,
        "tf_15m": tf_15m,
        "tf_1h": tf_1h,
    }

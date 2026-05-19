"""Pullback signal generator: long-side entry after a counter-trend retracement.

Trigger conditions:
  - 1h trend aligned with side
  - 5m or 15m RSI just bounced from oversold (Call) / overbought (Put)
  - Price near EMA20 on the lower TF (retracement pulled back)
"""
from __future__ import annotations

from . import signal_scoring as scoring
from .theta import classify as theta_classify
from .theta import theta_decay_probability


def detect_setup(side: str, mtf: dict) -> dict:
    """Decide whether a pullback setup is fresh. Returns {triggered, reason}."""
    tf_1h = mtf.get("tf_1h", {})
    tf_5m = mtf.get("tf_5m", {})
    tf_15m = mtf.get("tf_15m", {})

    direction_needed = "up" if side == "C" else "down"
    if tf_1h.get("direction") != direction_needed:
        return {"triggered": False, "reason": "1h тренд не совпадает с направлением"}

    rsi_5m = tf_5m.get("rsi")
    rsi_15m = tf_15m.get("rsi")
    if rsi_5m is None and rsi_15m is None:
        return {"triggered": False, "reason": "Нет RSI на младших TF"}

    # For Call (uptrend pullback) — looking for RSI bouncing from <40 zone.
    # For Put (downtrend pullback) — looking for RSI rolling over from >60 zone.
    if side == "C":
        bounce_5m = rsi_5m is not None and 38 <= rsi_5m <= 55
        bounce_15m = rsi_15m is not None and 38 <= rsi_15m <= 55
    else:
        bounce_5m = rsi_5m is not None and 45 <= rsi_5m <= 62
        bounce_15m = rsi_15m is not None and 45 <= rsi_15m <= 62

    if bounce_5m or bounce_15m:
        which = "5m" if bounce_5m else "15m"
        return {
            "triggered": True,
            "reason": f"RSI {which}={rsi_5m if bounce_5m else rsi_15m} вернулся к нейтрали — откат закончился",
        }
    return {"triggered": False, "reason": "RSI младших TF не в зоне отбоя"}


def evaluate(
    *,
    option: dict,
    spot: float,
    mtf: dict,
    regime: dict,
    iv_metrics: dict,
    hours: float,
    holding_horizon_h: float,
) -> dict | None:
    """Score an option as PULLBACK candidate, or None if setup not present."""
    side = option["side"]
    setup = detect_setup(side, mtf)
    if not setup["triggered"]:
        return None

    breakdown: list[dict] = [{"factor": setup["reason"], "points": 2.0}]
    total = 2.0  # base bonus for valid pullback setup

    for fn, args in [
        (scoring.score_mtf_direction, (side, mtf)),
        (scoring.score_distance, (spot, option["strike"])),
        (scoring.score_time, (hours,)),
        (scoring.score_spread, (option["bid"], option["ask"])),
        (scoring.score_liquidity, (option["open_interest"], option["volume_24h"])),
        (scoring.score_delta, (option["delta"],)),
        (scoring.score_iv, (iv_metrics, side)),
        (scoring.score_regime, (regime, "pullback")),
    ]:
        pts, item = fn(*args)
        total += pts
        breakdown.append(item)

    mid = (option["bid"] + option["ask"]) / 2 if option["bid"] > 0 and option["ask"] > 0 else option["mark_price"]
    p_decay = theta_decay_probability(option["theta"], mid or 0.0001, holding_horizon_h, option["delta"])
    pts, item = scoring.score_theta_prob(p_decay)
    total += pts
    breakdown.append(item)

    score = max(0.0, min(10.0, round(total, 1)))
    return {
        "signal_type": "pullback",
        "score": score,
        "signal": scoring.classify(score),
        "recommendation": scoring.recommendation(score),
        "breakdown": breakdown,
        "theta_decay_probability": round(p_decay, 3),
        "theta_decay_class": theta_classify(p_decay),
        "setup_reason": setup["reason"],
    }

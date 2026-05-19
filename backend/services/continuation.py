"""Continuation signal generator: enter in the direction of an established trend."""
from __future__ import annotations

from . import signal_scoring as scoring
from .theta import classify as theta_classify
from .theta import theta_decay_probability


def evaluate(
    *,
    option: dict,
    spot: float,
    mtf: dict,
    regime: dict,
    iv_metrics: dict,
    hours: float,
    holding_horizon_h: float,
) -> dict:
    """Score a single option as a CONTINUATION candidate. Returns scoring dict."""
    side = option["side"]
    breakdown: list[dict] = []
    total = 0.0

    for fn, args in [
        (scoring.score_mtf_direction, (side, mtf)),
        (scoring.score_momentum, (mtf,)),
        (scoring.score_volume, (mtf,)),
        (scoring.score_distance, (spot, option["strike"])),
        (scoring.score_time, (hours,)),
        (scoring.score_spread, (option["bid"], option["ask"])),
        (scoring.score_liquidity, (option["open_interest"], option["volume_24h"])),
        (scoring.score_delta, (option["delta"],)),
        (scoring.score_iv, (iv_metrics, side)),
        (scoring.score_regime, (regime, "continuation")),
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
        "signal_type": "continuation",
        "score": score,
        "signal": scoring.classify(score),
        "recommendation": scoring.recommendation(score),
        "breakdown": breakdown,
        "theta_decay_probability": round(p_decay, 3),
        "theta_decay_class": theta_classify(p_decay),
    }

"""Fade signal generator — ВНИМАНИЕ: 12-месячный бэктест показал что
эта стратегия СТАБИЛЬНО УБЫТОЧНА (avg −3.3% per trade, WR 43%, n=2716).

Сохранена в коде только для архивной/исследовательской цели. Production
banner предупреждает не торговать реальными деньгами.

Generator fires when MTF consensus is OPPOSITE to the trade side, i.e.
buy Call when 2-3/3 says "down", buy Put when 2-3/3 says "up".
"""
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
) -> dict | None:
    """Score an option as a FADE candidate, or None if MTF is aligned with the
    trade direction (that would be continuation, not fade)."""
    side = option["side"]
    fade_direction_needed = "down" if side == "C" else "up"

    direction = mtf.get("direction")
    aligned = mtf.get("tfs_aligned", 0)

    if direction != fade_direction_needed or aligned < 2:
        return None

    breakdown: list[dict] = []
    total = 0.0

    # MTF strength of the trend we're fading — symmetric, neutral baseline
    if aligned == 3:
        total += 1.5
        breakdown.append({"factor": "MTF 3/3 against side", "points": 1.5})
    else:
        total += 1.0
        breakdown.append({"factor": "MTF 2/3 against side", "points": 1.0})

    # Distance / time / spread / liquidity / delta / IV
    for fn, args in [
        (scoring.score_distance, (spot, option["strike"])),
        (scoring.score_time, (hours,)),
        (scoring.score_spread, (option["bid"], option["ask"])),
        (scoring.score_liquidity, (option["open_interest"], option["volume_24h"])),
        (scoring.score_delta, (option["delta"],)),
        (scoring.score_iv, (iv_metrics, side)),
    ]:
        pts, item = fn(*args)
        total += pts
        breakdown.append(item)

    # Theta probability
    mid = (option["bid"] + option["ask"]) / 2 if option["bid"] > 0 and option["ask"] > 0 else option["mark_price"]
    p_decay = theta_decay_probability(option["theta"], mid or 0.0001, holding_horizon_h, option["delta"])
    pts, item = scoring.score_theta_prob(p_decay)
    total += pts
    breakdown.append(item)

    score = max(0.0, min(10.0, round(total, 1)))
    return {
        "signal_type": "fade",
        "score": score,
        "signal": scoring.classify(score),
        "recommendation": scoring.recommendation(score),
        "breakdown": breakdown,
        "theta_decay_probability": round(p_decay, 3),
        "theta_decay_class": theta_classify(p_decay),
        "setup_reason": f"MTF {direction} ({aligned}/3) — фейдим в сторону {('UP' if side=='C' else 'DOWN')}",
        "warning": "Стратегия НЕ валидирована. 12-мес бэктест: avg −3.3%/trade. Не торгуйте реальными деньгами.",
    }

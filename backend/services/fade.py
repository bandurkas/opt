"""Fade (mean-reversion) signal generator.

Backtest finding (60d, ETH 5m walk-forward): trend-following the MTF consensus
loses (option WR 27%, avg P&L -7%). Inverting the direction and using 7d+
expiry turns the system profitable (option WR 49%, avg P&L +2.85%, sweep
shows TP1+20%/TP2+70%/SL-35% as a balanced sweet spot).

This generator fires when MTF consensus is OPPOSITE to the trade side, i.e.
buy Call when 3/3 says "down" (betting on a bounce), buy Put when 3/3 says
"up" (betting on a reversal). Best in TRANSITION regime (ADX 20-25).
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
        return None  # Not a fade setup

    breakdown: list[dict] = []
    total = 0.0

    # MTF strength of the trend we're fading — stronger trend = stronger fade signal
    if aligned == 3:
        total += 2.0
        breakdown.append({"factor": "MTF 3/3 против сделки — fade силы", "points": 2.0})
    else:
        total += 1.0
        breakdown.append({"factor": "MTF 2/3 против сделки — fade умеренный", "points": 1.0})

    # Accelerating momentum to fade = better setup (stretched rubber-band)
    if mtf.get("accelerating"):
        total += 1.0
        breakdown.append({"factor": "Тренд ускоряется (растянут) — пружина дальше отскочит", "points": 1.0})

    # Volume z-score: spike on the move being faded is GOOD (climactic)
    vz = mtf.get("tf_1h", {}).get("volume_zscore") or 0
    if vz >= 2:
        total += 1.0
        breakdown.append({"factor": f"Климактический объём (z={round(vz,2)})", "points": 1.0})

    # Distance / time / spread / liquidity / delta / IV — same as continuation scoring
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

    # Regime: best in transition, ok in trend, bad in range
    r = regime.get("regime", "unknown")
    if r == "transition":
        total += 1.5
        breakdown.append({"factor": f"Transition регим (ADX={regime.get('adx')}) — оптимум для fade", "points": 1.5})
    elif r == "trend":
        total += 0.5
        breakdown.append({"factor": f"Trend регим (ADX={regime.get('adx')}) — fade приемлем", "points": 0.5})
    elif r == "range":
        total -= 1.5
        breakdown.append({"factor": "Range регим — плох для fade", "points": -1.5})

    # Theta
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
    }

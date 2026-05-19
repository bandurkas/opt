"""IV change/rank from option_snapshots history.

Gracefully degrades if no history exists yet (returns nulls).
"""
from __future__ import annotations

import time

from db.repository import iv_history


def iv_metrics(symbol: str, current_iv: float) -> dict:
    """Compute iv_change_1h_pct, iv_change_24h_pct, iv_rank_7d.

    Returns nulls where history is insufficient. Score interprets nulls as neutral.
    """
    out: dict = {
        "current_iv": round(current_iv, 4) if current_iv else None,
        "iv_change_1h_pct": None,
        "iv_change_24h_pct": None,
        "iv_rank_7d": None,
        "history_points_7d": 0,
        "trend_1h": "unknown",  # "rising" / "falling" / "stable"
    }

    if current_iv is None or current_iv <= 0:
        return out

    history_7d = iv_history(symbol, hours=24 * 7)
    out["history_points_7d"] = len(history_7d)

    if not history_7d:
        return out

    # 1h change
    cutoff_1h = int(time.time() * 1000) - 3_600_000
    one_hour_ago = next((iv for ts, iv in history_7d if ts >= cutoff_1h), None)
    if one_hour_ago is None and history_7d:
        # take oldest available within 2h window as a fallback
        cutoff_2h = int(time.time() * 1000) - 2 * 3_600_000
        one_hour_ago = next((iv for ts, iv in history_7d if ts >= cutoff_2h), None)
    if one_hour_ago and one_hour_ago > 0:
        pct = (current_iv - one_hour_ago) / one_hour_ago * 100
        out["iv_change_1h_pct"] = round(pct, 2)
        if pct > 1:
            out["trend_1h"] = "rising"
        elif pct < -1:
            out["trend_1h"] = "falling"
        else:
            out["trend_1h"] = "stable"

    # 24h change
    cutoff_24h = int(time.time() * 1000) - 24 * 3_600_000
    day_ago = next((iv for ts, iv in history_7d if ts >= cutoff_24h), None)
    if day_ago and day_ago > 0:
        out["iv_change_24h_pct"] = round((current_iv - day_ago) / day_ago * 100, 2)

    # IV rank over 7d: where does current sit in [min, max]?
    ivs = [iv for _, iv in history_7d if iv > 0]
    if len(ivs) >= 5:
        lo, hi = min(ivs), max(ivs)
        if hi > lo:
            out["iv_rank_7d"] = round((current_iv - lo) / (hi - lo) * 100, 1)
        else:
            out["iv_rank_7d"] = 50.0

    return out

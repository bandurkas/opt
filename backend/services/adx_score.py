"""ADX Readiness Score (0-10)."""
from __future__ import annotations

from .indicators import adx_full

def compute_adx_score(candles_1h: list[dict]) -> dict:
    res = adx_full(candles_1h, 14)
    if not res:
        return {
            "score": 0.0,
            "adx": None,
            "plus_di": None,
            "minus_di": None,
            "adx_slope_6h": 0.0,
            "di_spread": 0.0,
            "components": {"base": 0.0, "slope_bonus": 0.0, "di_bonus": 0.0}
        }
        
    a = res["adx"]
    p_di = res["plus_di"]
    m_di = res["minus_di"]
    hist = res["adx_history"]
    
    # 1. Base score
    if a <= 12: base = 8.0
    elif a <= 20: base = 8.0 - (a - 12) * (2.0 / 8.0)
    elif a <= 28: base = 6.0 - (a - 20) * (3.0 / 8.0)
    elif a <= 35: base = 3.0 - (a - 28) * (2.0 / 7.0)
    elif a <= 45: base = 1.0 - (a - 35) * (1.0 / 10.0)
    else: base = 0.0
    
    # 2. Slope bonus (6h ago)
    if len(hist) >= 7:
        a_6h = hist[-7]
    else:
        a_6h = hist[0]
        
    delta = a - a_6h
    slope_bonus = max(-1.0, min(1.0, -delta / 3.0))
    
    # 3. DI Spread bonus
    spread = abs(p_di - m_di)
    di_bonus = max(0.0, min(1.0, 1.0 - spread / 25.0))
    
    score = base + slope_bonus + di_bonus
    score = max(0.0, min(10.0, score))
    
    return {
        "score": round(score, 2),
        "adx": round(a, 2),
        "plus_di": round(p_di, 2),
        "minus_di": round(m_di, 2),
        "adx_slope_6h": round(delta, 2),
        "di_spread": round(spread, 2),
        "components": {
            "base": round(base, 2),
            "slope_bonus": round(slope_bonus, 2),
            "di_bonus": round(di_bonus, 2)
        }
    }

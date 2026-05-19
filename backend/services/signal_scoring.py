"""Score factors shared by continuation/pullback generators.

Each helper returns (points: float, breakdown: dict).
"""
from __future__ import annotations


def score_distance(spot: float, strike: float) -> tuple[float, dict]:
    if spot <= 0:
        return 0.0, {"factor": "Расстояние до страйка: нет данных", "points": 0}
    dist_pct = abs(strike - spot) / spot * 100
    if dist_pct <= 1:
        pts = 2.0
    elif dist_pct <= 2:
        pts = 1.0
    elif dist_pct <= 4:
        pts = 0.0
    else:
        pts = -2.0
    return pts, {"factor": f"Расстояние до страйка ±{round(dist_pct, 2)}%", "points": pts}


def score_time(hours: float) -> tuple[float, dict]:
    if hours >= 72:
        pts = 2.0
        label = ">72ч"
    elif hours >= 24:
        pts = 1.0
        label = "24-72ч"
    elif hours >= 12:
        pts = -1.0
        label = "12-24ч"
    else:
        pts = -3.0
        label = "<12ч"
    return pts, {"factor": f"До экспирации {label}", "points": pts}


def score_mtf_direction(side: str, mtf: dict) -> tuple[float, dict]:
    direction_needed = "up" if side == "C" else "down"
    aligned = mtf.get("tfs_aligned", 0)
    direction = mtf.get("direction")
    if direction == direction_needed and aligned >= 3:
        return 2.0, {"factor": "MTF тренд 3/3 в направлении сделки", "points": 2.0}
    if direction == direction_needed and aligned == 2:
        return 1.0, {"factor": "MTF тренд 2/3 в направлении сделки", "points": 1.0}
    if direction == "neutral":
        return 0.0, {"factor": "MTF тренд: смешанный", "points": 0.0}
    return -1.5, {"factor": "MTF тренд против сделки", "points": -1.5}


def score_momentum(mtf: dict) -> tuple[float, dict]:
    momentum_1h = mtf.get("tf_1h", {}).get("momentum", "flat")
    if momentum_1h == "accelerating" and mtf.get("accelerating"):
        return 1.5, {"factor": "Импульс 1h ускоряется", "points": 1.5}
    if momentum_1h == "decelerating":
        return 0.0, {"factor": "Импульс 1h замедляется", "points": 0.0}
    if momentum_1h == "divergent":
        return -1.5, {"factor": "Дивергенция 1h", "points": -1.5}
    return 0.5, {"factor": "Импульс 1h ровный", "points": 0.5}


def score_volume(mtf: dict) -> tuple[float, dict]:
    vz_1h = mtf.get("tf_1h", {}).get("volume_zscore", 0) or 0
    if vz_1h >= 2:
        return 1.0, {"factor": f"Объём 1h всплеск (z={round(vz_1h,2)})", "points": 1.0}
    if vz_1h <= -1:
        return -0.5, {"factor": "Объём 1h ниже среднего", "points": -0.5}
    return 0.0, {"factor": "Объём 1h в норме", "points": 0.0}


def score_spread(bid: float, ask: float) -> tuple[float, dict]:
    if bid <= 0 or ask <= 0:
        return -2.0, {"factor": "Нет двусторонних котировок", "points": -2.0}
    mid = (bid + ask) / 2
    spread_pct = (ask - bid) / mid * 100 if mid > 0 else 100
    if spread_pct > 5:
        return -2.0, {"factor": f"Широкий спред {round(spread_pct,1)}%", "points": -2.0}
    if spread_pct > 3:
        return -0.5, {"factor": f"Средний спред {round(spread_pct,1)}%", "points": -0.5}
    return 0.5, {"factor": f"Узкий спред {round(spread_pct,1)}%", "points": 0.5}


def score_liquidity(oi: float, vol_24h: float) -> tuple[float, dict]:
    if oi >= 50 and vol_24h >= 5:
        return 1.0, {"factor": f"Ликвидность OK (OI={int(oi)}, V24h={int(vol_24h)})", "points": 1.0}
    if oi < 5 and vol_24h < 1:
        return -1.5, {"factor": "Низкая ликвидность", "points": -1.5}
    return 0.0, {"factor": "Ликвидность средняя", "points": 0.0}


def score_delta(delta: float) -> tuple[float, dict]:
    if 0.25 <= abs(delta) <= 0.6:
        return 0.5, {"factor": f"Delta в зоне ({round(delta,2)})", "points": 0.5}
    return 0.0, {"factor": f"Delta вне зоны ({round(delta,2)})", "points": 0.0}


def score_iv(iv_metrics: dict, side: str) -> tuple[float, dict]:
    """Long premium: IV rising helps, IV crashing hurts."""
    change = iv_metrics.get("iv_change_1h_pct")
    if change is None:
        return 0.0, {"factor": "IV history: нет данных", "points": 0.0}
    if change < -5:
        return -2.0, {"factor": f"IV crush 1h ({change}%)", "points": -2.0}
    if change > 3:
        return 1.0, {"factor": f"IV растёт 1h (+{change}%)", "points": 1.0}
    return 0.0, {"factor": f"IV стабильна ({change}%)", "points": 0.0}


def score_theta_prob(p_decay: float) -> tuple[float, dict]:
    if p_decay >= 0.5:
        return -2.0, {"factor": f"Theta риск критический (P={int(p_decay*100)}%)", "points": -2.0}
    if p_decay >= 0.3:
        return -1.0, {"factor": f"Theta риск высокий (P={int(p_decay*100)}%)", "points": -1.0}
    if p_decay >= 0.15:
        return -0.3, {"factor": f"Theta риск средний (P={int(p_decay*100)}%)", "points": -0.3}
    return 0.5, {"factor": f"Theta риск низкий (P={int(p_decay*100)}%)", "points": 0.5}


def score_regime(regime: dict, signal_type: str) -> tuple[float, dict]:
    r = regime.get("regime", "unknown")
    if signal_type == "continuation":
        if r == "trend":
            return 1.0, {"factor": f"Trend регим (ADX={regime.get('adx')})", "points": 1.0}
        if r == "range":
            return -1.5, {"factor": f"Range регим (ADX={regime.get('adx')}) — флэт", "points": -1.5}
        return 0.0, {"factor": f"Transition регим (ADX={regime.get('adx')})", "points": 0.0}
    # pullback
    if r == "trend":
        return 1.5, {"factor": f"Pullback в trend региме (ADX={regime.get('adx')})", "points": 1.5}
    if r == "range":
        return -0.5, {"factor": f"Pullback в range региме", "points": -0.5}
    return 0.0, {"factor": f"Pullback в transition", "points": 0.0}


def classify(score: float) -> str:
    if score >= 9:
        return "Очень сильный"
    if score >= 7:
        return "Хороший"
    if score >= 4:
        return "Средний"
    return "Плохой"


def recommendation(score: float) -> str:
    if score >= 9:
        return "Входить (сильный)"
    if score >= 7:
        return "Входить"
    if score >= 5:
        return "Осторожно"
    return "Не входить"

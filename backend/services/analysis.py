from __future__ import annotations

from datetime import datetime, timezone

from .market_data import MarketSnapshot


# ───────────────────────── helpers ─────────────────────────

def time_to_expiry(expiry_ms: int, now_ms: int) -> dict:
    delta_min = max(0, int((expiry_ms - now_ms) / 60_000))
    hours = delta_min / 60
    if hours > 72:
        risk = "низкий"
    elif hours >= 24:
        risk = "средний"
    elif hours >= 12:
        risk = "высокий"
    else:
        risk = "критический"
    return {
        "hours_to_expiry": round(hours, 1),
        "minutes_to_expiry": delta_min,
        "theta_risk": risk,
        "expiry_iso": datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def distance(spot: float, strike: float) -> dict:
    if spot <= 0:
        return {"distance_usd": 0, "distance_percent": 0}
    return {
        "distance_usd": round(strike - spot, 2),
        "distance_percent": round((strike - spot) / spot * 100, 2),
    }


def _spread_pct(bid: float, ask: float) -> float | None:
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid * 100 if mid > 0 else None


def _mid(bid: float, ask: float) -> float | None:
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2


# ───────────────────────── scoring ─────────────────────────

def _score_option(option: dict, market: MarketSnapshot, hours: float) -> dict:
    """Score 0-10 with explanations. Higher = better entry."""
    breakdown: list[dict] = []
    score = 0.0
    spot = market.spot
    strike = option["strike"]
    side = option["side"]

    # 1. Directional alignment with market
    if side == "C" and market.direction == "bullish":
        score += 2
        breakdown.append({"factor": "Тренд бычий, Call в направлении", "points": +2})
    elif side == "P" and market.direction == "bearish":
        score += 2
        breakdown.append({"factor": "Тренд медвежий, Put в направлении", "points": +2})
    elif market.direction == "neutral":
        score += 0.5
        breakdown.append({"factor": "Нейтральный рынок", "points": +0.5})
    else:
        score -= 1.5
        breakdown.append({"factor": "Контртренд (против рынка)", "points": -1.5})

    # 2. Momentum
    if market.momentum_strong:
        score += 1.5
        breakdown.append({"factor": "Сильный импульс", "points": +1.5})

    if market.volume_spike:
        score += 1
        breakdown.append({"factor": "Всплеск объёма", "points": +1})

    # 3. Strike proximity (favor slightly OTM for cheaper premium, tighter window for short-dated)
    dist_pct = abs(strike - spot) / spot * 100 if spot else 100
    if hours < 24:
        sweet_zone = 1.5
    elif hours < 72:
        sweet_zone = 3.0
    else:
        sweet_zone = 5.0

    if dist_pct <= sweet_zone:
        score += 2
        breakdown.append({"factor": f"Страйк близко к цене (±{round(dist_pct,2)}%)", "points": +2})
    elif dist_pct <= sweet_zone * 2:
        score += 0.5
        breakdown.append({"factor": f"Страйк умеренно далеко (±{round(dist_pct,2)}%)", "points": +0.5})
    else:
        score -= 1
        breakdown.append({"factor": f"Страйк далеко (±{round(dist_pct,2)}%)", "points": -1})

    # 4. Theta / time
    if hours >= 72:
        score += 1.5
        breakdown.append({"factor": "До экспирации >3д", "points": +1.5})
    elif hours >= 24:
        score += 0.5
        breakdown.append({"factor": "До экспирации 1-3д", "points": +0.5})
    elif hours >= 12:
        score -= 1
        breakdown.append({"factor": "До экспирации <24ч (Theta риск)", "points": -1})
    else:
        score -= 2.5
        breakdown.append({"factor": "До экспирации <12ч (критич. Theta)", "points": -2.5})

    # 5. Spread
    spread = _spread_pct(option["bid"], option["ask"])
    if spread is None:
        score -= 2
        breakdown.append({"factor": "Нет двусторонних котировок", "points": -2})
    elif spread > 12:
        score -= 1.5
        breakdown.append({"factor": f"Широкий спред ({round(spread,1)}%)", "points": -1.5})
    elif spread > 6:
        score -= 0.5
        breakdown.append({"factor": f"Средний спред ({round(spread,1)}%)", "points": -0.5})
    else:
        score += 0.5
        breakdown.append({"factor": f"Узкий спред ({round(spread,1)}%)", "points": +0.5})

    # 6. Liquidity
    oi = option["open_interest"]
    vol_24h = option["volume_24h"]
    if oi >= 50 and vol_24h >= 5:
        score += 1
        breakdown.append({"factor": f"Хорошая ликвидность (OI={int(oi)}, V24h={int(vol_24h)})", "points": +1})
    elif oi < 5 and vol_24h < 1:
        score -= 1.5
        breakdown.append({"factor": "Низкая ликвидность", "points": -1.5})

    # 7. Levels — penalize entries pushing against immediate resistance/support
    if side == "C" and strike > market.nearest_resistance > 0 and (strike - market.nearest_resistance) / spot > 0.005:
        score -= 1
        breakdown.append({"factor": f"Страйк выше ближайшего сопротивления {market.nearest_resistance}", "points": -1})
    if side == "P" and strike < market.nearest_support > 0 and (market.nearest_support - strike) / spot > 0.005:
        score -= 1
        breakdown.append({"factor": f"Страйк ниже ближайшей поддержки {market.nearest_support}", "points": -1})

    # 8. Delta sweet spot (longs prefer |delta| 0.25-0.6)
    abs_delta = abs(option["delta"])
    if 0.25 <= abs_delta <= 0.6:
        score += 0.5
        breakdown.append({"factor": f"Delta в зоне ({round(option['delta'],2)})", "points": +0.5})

    score = max(0.0, min(10.0, round(score, 1)))
    if score >= 7:
        signal = "Хороший"
    elif score >= 4:
        signal = "Средний"
    else:
        signal = "Плохой"
    return {"score": score, "signal": signal, "breakdown": breakdown}


# ───────────────────────── entry plan ─────────────────────────

def _entry_plan(option: dict, market: MarketSnapshot, hours: float, risk_budget_usd: float = 100.0) -> dict:
    """Concrete trading parameters: limit price, size, TP/SL."""
    bid = option["bid"]
    ask = option["ask"]
    mid = _mid(bid, ask)
    mark = option["mark_price"]
    # Target limit price = mid (or slightly below mid to wait for fill).
    if mid:
        limit = round(mid * 0.995, 4)
    elif mark > 0:
        limit = round(mark * 0.99, 4)
    else:
        limit = round(ask, 4) if ask > 0 else 0.0

    # Position size: how many contracts to keep total risk near risk_budget_usd
    contracts = 0
    if limit > 0:
        contracts = max(1, int(risk_budget_usd // limit))

    # Target spot move = halfway to nearest level in our direction
    spot = market.spot
    if option["side"] == "C":
        target_spot = round((spot + max(market.nearest_resistance, option["strike"])) / 2, 2)
        stop_spot = round(market.nearest_support, 2) if market.nearest_support else round(spot * 0.99, 2)
    else:
        target_spot = round((spot + min(market.nearest_support, option["strike"])) / 2, 2)
        stop_spot = round(market.nearest_resistance, 2) if market.nearest_resistance else round(spot * 1.01, 2)

    # Premium TP/SL — rough heuristic on premium move:
    # +60% premium → take profit, -40% premium → stop.
    tp_premium = round(limit * 1.6, 4) if limit > 0 else 0.0
    sl_premium = round(limit * 0.6, 4) if limit > 0 else 0.0

    return {
        "limit_price": limit,
        "limit_price_hint": "Лимит около середины bid/ask. Если не закрылось за 2-3 мин — догоняй на 1 тик.",
        "contracts": contracts,
        "max_risk_usd": round(limit * contracts, 2),
        "take_profit_premium": tp_premium,
        "stop_loss_premium": sl_premium,
        "target_spot": target_spot,
        "stop_spot": stop_spot,
        "time_horizon_h": min(round(hours / 2, 1), 24),
    }


# ───────────────────────── public API ─────────────────────────

def scan_top_opportunities(
    options: list[dict],
    market: MarketSnapshot,
    now_ms: int,
    top_n: int = 3,
    min_hours: float = 6.0,
    max_hours: float = 30 * 24.0,
    max_distance_pct: float = 8.0,
) -> list[dict]:
    """Filter the chain and return ranked top opportunities with full plan."""
    spot = market.spot
    if spot <= 0:
        return []

    candidates: list[dict] = []
    for opt in options:
        hours = (opt["expiry_ms"] - now_ms) / 3_600_000
        if hours < min_hours or hours > max_hours:
            continue
        if abs(opt["strike"] - spot) / spot * 100 > max_distance_pct:
            continue
        if opt["bid"] <= 0 and opt["ask"] <= 0 and opt["mark_price"] <= 0:
            continue

        scoring = _score_option(opt, market, hours)
        if scoring["score"] < 4:
            continue
        plan = _entry_plan(opt, market, hours)

        candidates.append(
            {
                "symbol": opt["symbol"],
                "side": "Call" if opt["side"] == "C" else "Put",
                "strike": opt["strike"],
                "expiry": opt["expiry_label"],
                "expiry_iso": time_to_expiry(opt["expiry_ms"], now_ms)["expiry_iso"],
                "underlying_price": opt["underlying_price"] or spot,
                "spot": spot,
                "distance": distance(spot, opt["strike"]),
                "time": time_to_expiry(opt["expiry_ms"], now_ms),
                "quotes": {
                    "bid": opt["bid"],
                    "ask": opt["ask"],
                    "mark": opt["mark_price"],
                    "spread_pct": round(_spread_pct(opt["bid"], opt["ask"]) or 0, 2),
                },
                "greeks": {
                    "delta": round(opt["delta"], 3),
                    "gamma": round(opt["gamma"], 4),
                    "vega": round(opt["vega"], 3),
                    "theta": round(opt["theta"], 3),
                    "iv": round(opt["mark_iv"], 2),
                },
                "liquidity": {
                    "open_interest": int(opt["open_interest"]),
                    "volume_24h": round(opt["volume_24h"], 2),
                },
                "scoring": scoring,
                "entry_plan": plan,
            }
        )

    candidates.sort(key=lambda c: c["scoring"]["score"], reverse=True)
    return candidates[:top_n]

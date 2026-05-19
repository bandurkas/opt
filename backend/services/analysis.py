"""Top-level orchestrator: filter chain, run both signal generators,
merge and rank, build entry plan + exits."""
from __future__ import annotations

from datetime import datetime, timezone

from db.repository import recent_klines

from . import continuation, pullback
from .exits import build_exit_plan
from .indicators import atr
from .iv_analytics import iv_metrics
from .market_data import MarketSnapshot
from .momentum_mtf import analyze_tf, consensus
from .regime import detect_regime


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


def _mid(bid: float, ask: float) -> float | None:
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2


def _spread_pct(bid: float, ask: float) -> float | None:
    mid = _mid(bid, ask)
    if mid is None or mid == 0:
        return None
    return (ask - bid) / mid * 100


# ───────────────────────── multi-TF builder ─────────────────────────

def build_mtf_context(symbol: str = "ETHUSDT") -> dict:
    """Pull klines from DB and compute MTF momentum + regime + ATR."""
    candles_5m = recent_klines(symbol, "5m", limit=200)
    candles_15m = recent_klines(symbol, "15m", limit=200)
    candles_1h = recent_klines(symbol, "1h", limit=200)

    tf_5m = analyze_tf(candles_5m) if candles_5m else {"direction": "unknown", "momentum": "unknown"}
    tf_15m = analyze_tf(candles_15m) if candles_15m else {"direction": "unknown", "momentum": "unknown"}
    tf_1h = analyze_tf(candles_1h) if candles_1h else {"direction": "unknown", "momentum": "unknown"}

    mtf = consensus(tf_5m, tf_15m, tf_1h)
    regime = detect_regime(candles_1h) if candles_1h else {"regime": "unknown", "adx": None}
    atr_15m = atr(candles_15m, 14) if len(candles_15m) >= 16 else None

    return {
        "mtf": mtf,
        "regime": regime,
        "atr_15m": round(atr_15m, 2) if atr_15m else None,
        "data_freshness": {
            "candles_5m": len(candles_5m),
            "candles_15m": len(candles_15m),
            "candles_1h": len(candles_1h),
        },
    }


# ───────────────────────── entry plan ─────────────────────────

def build_entry_plan(
    option: dict,
    market: MarketSnapshot,
    mtf_ctx: dict,
    hours: float,
    risk_budget_usd: float,
) -> dict:
    bid, ask, mark = option["bid"], option["ask"], option["mark_price"]
    mid = _mid(bid, ask)
    if mid:
        limit = round(mid * 0.995, 4)
    elif mark > 0:
        limit = round(mark * 0.99, 4)
    else:
        limit = round(ask, 4) if ask > 0 else 0.0

    contracts = max(1, int(risk_budget_usd // limit)) if limit > 0 else 0
    total_cost = round(limit * contracts, 2)

    side = option["side"]
    strike = option["strike"]
    spot = market.spot

    if side == "C":
        position_summary = (
            f"CALL — право купить ETH по ${int(strike)} до {option['expiry_label']}. "
            f"Зарабатываешь, если ETH ВЫРАСТЕТ выше ${int(strike)}."
        )
    else:
        position_summary = (
            f"PUT — право продать ETH по ${int(strike)} до {option['expiry_label']}. "
            f"Зарабатываешь, если ETH УПАДЁТ ниже ${int(strike)}."
        )

    exits = build_exit_plan(
        side=side,
        spot=spot,
        strike=strike,
        limit_price=limit,
        contracts=contracts,
        regime=mtf_ctx["regime"].get("regime", "unknown"),
        nearest_resistance=market.nearest_resistance,
        nearest_support=market.nearest_support,
        atr_15m=mtf_ctx.get("atr_15m"),
    )

    bybit_steps = [
        "Открой Bybit → раздел Derivatives → Options.",
        "В шапке выбери валюту: ETH.",
        f"Выбери дату экспирации: {option['expiry_label']}.",
        f"Найди строку Strike = {int(strike)}, столбец {'CALL' if side == 'C' else 'PUT'}.",
        "Нажми Buy / Long (зелёная кнопка).",
        "Order Type → Limit (не Market!).",
        f"Price: {limit}",
        f"Quantity: {contracts}",
        "Проверь и нажми Confirm Order.",
        "Жди исполнения 2-3 минуты. Если не исполнилось — подними цену на 0.05-0.10 и переотправь.",
    ]

    return {
        "action": f"Купить {contracts} контракт(ов) по лимиту {limit} USDT",
        "position_summary": position_summary,
        "symbol_to_search": option["symbol"],
        "limit_price": limit,
        "contracts": contracts,
        "total_cost_usd": total_cost,
        "max_risk_usd": total_cost,
        "max_risk_note": "Это всё, что можешь потерять. Опцион может обнулиться.",
        "exits": exits,
        "bybit_steps": bybit_steps,
        "limit_price_hint": "Лимит-цена — это премия (стоимость) одного контракта в USDT. Не путать с ценой ETH!",
    }


# ───────────────────────── public API ─────────────────────────

def scan_top_opportunities(
    options: list[dict],
    market: MarketSnapshot,
    now_ms: int,
    *,
    mtf_ctx: dict,
    top_n: int = 3,
    min_hours: float = 6.0,
    max_hours: float = 30 * 24.0,
    max_distance_pct: float = 8.0,
    min_score: float = 4.0,
    risk_budget_usd: float = 100.0,
    include_pullback: bool = True,
    include_continuation: bool = True,
) -> list[dict]:
    spot = market.spot
    if spot <= 0:
        return []

    mtf = mtf_ctx["mtf"]
    regime = mtf_ctx["regime"]

    candidates: list[dict] = []
    for opt in options:
        hours = (opt["expiry_ms"] - now_ms) / 3_600_000
        if hours < min_hours or hours > max_hours:
            continue
        if abs(opt["strike"] - spot) / spot * 100 > max_distance_pct:
            continue
        if opt["bid"] <= 0 and opt["ask"] <= 0 and opt["mark_price"] <= 0:
            continue

        ivm = iv_metrics(opt["symbol"], opt["mark_iv"])
        holding_horizon = min(24.0, hours / 2)

        signals: list[dict] = []
        if include_continuation:
            signals.append(continuation.evaluate(
                option=opt, spot=spot, mtf=mtf, regime=regime,
                iv_metrics=ivm, hours=hours, holding_horizon_h=holding_horizon,
            ))
        if include_pullback:
            pb = pullback.evaluate(
                option=opt, spot=spot, mtf=mtf, regime=regime,
                iv_metrics=ivm, hours=hours, holding_horizon_h=holding_horizon,
            )
            if pb is not None:
                signals.append(pb)

        for sig in signals:
            if sig["score"] < min_score:
                continue
            plan = build_entry_plan(opt, market, mtf_ctx, hours, risk_budget_usd)

            candidates.append({
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
                    "bid": opt["bid"], "ask": opt["ask"], "mark": opt["mark_price"],
                    "spread_pct": round(_spread_pct(opt["bid"], opt["ask"]) or 0, 2),
                },
                "greeks": {
                    "delta": round(opt["delta"], 3),
                    "gamma": round(opt["gamma"], 4),
                    "vega": round(opt["vega"], 3),
                    "theta": round(opt["theta"], 3),
                    "iv": round(opt["mark_iv"], 4),
                },
                "liquidity": {
                    "open_interest": int(opt["open_interest"]),
                    "volume_24h": round(opt["volume_24h"], 2),
                },
                "iv_metrics": ivm,
                "scoring": sig,
                "entry_plan": plan,
            })

    candidates.sort(key=lambda c: c["scoring"]["score"], reverse=True)
    return candidates[:top_n]

"""TP1 / TP2 / SL / trailing / time-stop rules.

Parameters adjust by regime:
  - trend  → wider bands (let runners run)
  - range  → tighter bands (fade reversion)
  - transition → mid

ATR(15m) used for trailing size when available.
"""
from __future__ import annotations


def _bands_for_regime(regime: str, profile: str | None = None) -> dict:
    # NOTE: 12-month backtest showed all profiles unprofitable. Numbers below
    # are the most-balanced from earlier experimentation, but DO NOT trade real
    # money with this — see UI banner.
    if profile == "fade_long":
        return {"tp1_pct": 0.20, "tp2_pct": 0.70, "sl_pct": 0.35}
    if regime == "trend":
        return {"tp1_pct": 0.35, "tp2_pct": 1.0, "sl_pct": 0.40}
    if regime == "range":
        return {"tp1_pct": 0.25, "tp2_pct": 0.55, "sl_pct": 0.30}
    return {"tp1_pct": 0.30, "tp2_pct": 0.80, "sl_pct": 0.35}


def build_exit_plan(
    *,
    side: str,           # 'C' or 'P'
    spot: float,
    strike: float,
    limit_price: float,  # entry premium
    contracts: int,
    regime: str,
    nearest_resistance: float,
    nearest_support: float,
    atr_15m: float | None,
    bands_profile: str | None = None,
) -> dict:
    """Return a structured exit plan with concrete dollar P&L."""
    if limit_price <= 0 or contracts <= 0:
        return {"valid": False}

    b = _bands_for_regime(regime, profile=bands_profile)

    tp1_premium = round(limit_price * (1 + b["tp1_pct"]), 4)
    tp2_premium = round(limit_price * (1 + b["tp2_pct"]), 4)
    sl_premium = round(limit_price * (1 - b["sl_pct"]), 4)

    # Spot targets — using levels as anchors
    if side == "C":
        tp1_spot = round((spot + max(nearest_resistance, strike)) / 2, 2)
        tp2_spot = round(max(nearest_resistance, strike * 1.005), 2)
        sl_spot = round(nearest_support if nearest_support else spot * 0.99, 2)
    else:
        tp1_spot = round((spot + min(nearest_support, strike)) / 2, 2)
        tp2_spot = round(min(nearest_support, strike * 0.995), 2)
        sl_spot = round(nearest_resistance if nearest_resistance else spot * 1.01, 2)

    # Trailing rule — backend does NOT execute TSL automatically; this is a
    # manual hint for the trader.
    trail_atr = round(atr_15m * 0.5, 2) if atr_15m else None
    trail_rule = (
        f"После TP1 — стоп в безубыток. После TP1+10% премии — трейлинг {trail_atr or '0.5×ATR(15m)'} от макс."
    )

    # Time stop
    time_stop_h = 12 if regime == "trend" else 6

    # Dollar P&L per half-position close at TP1, then full at TP2
    half = max(1, contracts // 2)
    remainder = contracts - half
    profit_tp1_usd = round((tp1_premium - limit_price) * half, 2)
    profit_tp2_usd = round((tp2_premium - limit_price) * remainder, 2)
    total_profit_targets_usd = round(profit_tp1_usd + profit_tp2_usd, 2)
    max_loss_usd = round((limit_price - sl_premium) * contracts, 2)

    return {
        "valid": True,
        "regime_used": regime,
        "tp1": {
            "premium": tp1_premium,
            "spot": tp1_spot,
            "contracts_to_close": half,
            "profit_usd": profit_tp1_usd,
        },
        "tp2": {
            "premium": tp2_premium,
            "spot": tp2_spot,
            "contracts_to_close": remainder,
            "profit_usd": profit_tp2_usd,
        },
        "sl": {
            "premium": sl_premium,
            "spot": sl_spot,
            "loss_usd": max_loss_usd,
        },
        "trail_rule": trail_rule,
        "trail_atr_15m": trail_atr,
        "time_stop_hours": time_stop_h,
        "summary": {
            "best_case_profit_usd": total_profit_targets_usd,
            "worst_case_loss_usd": max_loss_usd,
            "risk_reward": round(total_profit_targets_usd / max_loss_usd, 2) if max_loss_usd > 0 else None,
        },
    }

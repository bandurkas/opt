"""Live paper-trading strategy wrapper around the validated winner.

Uses `gen_sell_premium_iv_high` with the iter4-confirmed config plus the
iter5 overlays (bull-market filter, consecutive-loss CB, dynamic sizing).
"""
from __future__ import annotations

import time

from db import paper_repo

WINNER_GEN_KWARGS = {
    # vol_threshold lowered 0.70 → 0.60 after 21-day replay showed:
    # 27 additional signals (92.6% WR, 0 SL hits, +$428 P&L) without changing
    # drawdown. 0.70 was overly strict — gated out the "moderate-vol range"
    # setups that this strategy was actually designed to catch.
    "vol_threshold": 0.60,
    "regime_filter": ["range", "transition"],
    "side": "C",
    "adx_max": None,
    "mtf_direction_filter": "down",
    "bull_market_ratio_max": 1.05,
    "cooldown_bars": 6,
}

WINNER_EXIT = {
    "tp1_pct": 0.30,   # close half at -30% from entry credit (premium decayed 30%)
    "tp2_pct": 0.50,   # close remainder at -50% decay
    "sl_pct": 0.50,    # stop at +50% growth from entry credit
    "hold_h": 24,      # time stop
}

# Sigma constant used to price fallbacks. Bybit live IV will be used when
# available; this is only for the BS fallback path.
DEFAULT_SIGMA = 0.6
EXPIRY_TARGET_HOURS = 168  # ~7 days

# ───────────── Bybit-realistic sizing / friction model ─────────────
# Starting equity sized so the minimum 0.1-ETH lot fits in budget.
START_EQUITY_USD = 400.0
# Per trade: up to 40% of equity goes into option margin (the rest is buffer
# against TP/SL volatility and the next signal). On $400 → $160 budget.
MARGIN_PCT_PER_TRADE = 0.40
# Bybit min lot for ETH options.
LOT_MIN_ETH = 0.1
# Bybit Cross-Margin IM rate for short ETH options ≈ 10% of strike-notional.
# Effective IM per lot ≈ (0.10·strike + premium)·0.1 ≈ $20-30 at strike $2000.
IM_RATE = 0.10
# Half of round-trip spread. 1.0%·2 = 2% total slippage. Realistic for weekly ETH options.
SPREAD_HALF_PCT = 1.0
# Maximum percentage of total equity that can be locked in margin across all open positions.
MAX_PORTFOLIO_MARGIN_PCT = 0.80
# Bybit taker fee on notional, capped at 12.5% of premium per side.
FEE_RATE = 0.0003
FEE_CAP_PCT_OF_PREMIUM = 0.125


def is_cb_active(state: dict, now_ms: int) -> bool:
    return now_ms < int(state.get("cb_cooldown_until_ms") or 0)


def dyn_size_factor(state: dict) -> float:
    """Halve size when 10-trade WR < 40%. Same as the old current_size_usd."""
    pnls = state.get("recent_pnls_json") or []
    if len(pnls) >= 10:
        wr = sum(1 for p in pnls[-10:] if p > 0) / 10.0
        if wr < 0.40:
            return 0.5
    return 1.0


def realistic_size_lots(free_margin_usd: float, equity_usd: float, strike: float,
                        premium_mid: float, state: dict) -> int:
    """How many 0.1-ETH lots fit in our margin budget at this signal.

    Bybit IM ≈ (IM_RATE·strike + premium_mid)·lot_size_eth (per contract).
    Budget = MARGIN_PCT_PER_TRADE · equity · dyn_factor, capped by free_margin.
    Returns 0 if even one lot doesn't fit — caller should skip the signal.
    """
    if strike <= 0 or premium_mid <= 0 or equity_usd <= 0:
        return 0
    margin_per_lot = (IM_RATE * strike + premium_mid) * LOT_MIN_ETH
    if margin_per_lot <= 0:
        return 0
    trade_budget = equity_usd * MARGIN_PCT_PER_TRADE * dyn_size_factor(state)
    budget = min(trade_budget, free_margin_usd)
    return max(0, int(budget // margin_per_lot))


def margin_per_lot(strike: float, premium_mid: float) -> float:
    return (IM_RATE * strike + premium_mid) * LOT_MIN_ETH


def apply_entry_spread(premium_mid: float) -> float:
    """We SELL at bid = mid·(1 − half-spread)."""
    return premium_mid * (1 - SPREAD_HALF_PCT / 100.0)


def apply_exit_spread(premium_mid: float) -> float:
    """We BUY BACK at ask = mid·(1 + half-spread)."""
    return premium_mid * (1 + SPREAD_HALF_PCT / 100.0)


def fee_per_side(notional_usd: float, premium_total_usd: float) -> float:
    """0.03% × notional, capped at 12.5% of the premium that side handles."""
    cap = abs(premium_total_usd) * FEE_CAP_PCT_OF_PREMIUM
    return min(notional_usd * FEE_RATE, cap)


def evaluate_conditions(k5: list, k15: list, k1h: list) -> dict:
    """Check each entry condition against the LATEST bar without emitting a
    signal. Returns a dict with per-condition booleans + summary.

    IMPORTANT: uses the same 240-bar history window as gen_sell_premium_iv_high
    (strategy_registry._walk_iter), so the live UI conditions match exactly
    what the generator decides. Without this, vol_pctile / regime / mtf could
    differ slightly and confuse the user ('UI says block but bot opened').
    """
    from .indicators import ema, realized_vol
    from .momentum_mtf import analyze_tf, consensus
    from .regime import detect_regime

    out = {
        "ready": False,
        "vol_high": False,
        "regime_ok": False,
        "mtf_down_aligned": False,
        "bull_filter_ok": False,
        "spot": None,
        "vol_pctile": None,
        "regime": None,
        "mtf_direction": None,
        "mtf_aligned_count": None,
        "ema_ratio": None,
    }
    if not k5 or not k15 or not k1h:
        return out
    if len(k5) < 50 or len(k15) < 50 or len(k1h) < 200:
        return out

    out["spot"] = k5[-1]["close"]
    # Match the generator's HIST=240 window so this endpoint can't drift
    HIST = 240
    s5 = k5[-HIST:] if len(k5) > HIST else k5
    s15 = k15[-HIST:] if len(k15) > HIST else k15
    s1h = k1h[-HIST:] if len(k1h) > HIST else k1h

    # 1) Vol percentile (last 168h history, lookback 24h)
    closes_1h = [c["close"] for c in s1h]
    rolling_vols: list[float] = []
    for j in range(20, len(closes_1h)):
        rv = realized_vol(closes_1h[:j + 1], lookback=24)
        if rv is not None:
            rolling_vols.append(rv)
    if len(rolling_vols) >= 30:
        current_vol = rolling_vols[-1]
        sorted_vols = sorted(rolling_vols)
        
        # Match generator's threshold lookup exactly
        threshold_idx = int(len(sorted_vols) * WINNER_GEN_KWARGS["vol_threshold"])
        threshold = sorted_vols[threshold_idx]
        
        # Rank percentile (just for UI display)
        below = sum(1 for v in sorted_vols if v < current_vol)
        pctile = below / len(sorted_vols)
        out["vol_pctile"] = round(pctile, 3)
        
        # Exact logic match with generator (current_vol >= threshold)
        out["vol_high"] = current_vol >= threshold

    # 2) Regime (range/transition)
    reg = detect_regime(s1h)
    out["regime"] = reg.get("regime", "unknown")
    out["regime_ok"] = out["regime"] in WINNER_GEN_KWARGS["regime_filter"]

    # 3) MTF direction
    mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
    out["mtf_direction"] = mtf["direction"]
    out["mtf_aligned_count"] = mtf["tfs_aligned"]
    out["mtf_down_aligned"] = (mtf["direction"] == "down" and mtf["tfs_aligned"] >= 2)

    # 4) Bull-market filter (EMA50_1h / EMA200_1h < 1.05)
    ema50 = ema(closes_1h, 50)
    ema200 = ema(closes_1h, 200)
    if ema50 is not None and ema200 not in (None, 0):
        ratio = ema50 / ema200
        out["ema_ratio"] = round(ratio, 4)
        out["bull_filter_ok"] = ratio <= WINNER_GEN_KWARGS["bull_market_ratio_max"]

    out["ready"] = (out["vol_high"] and out["regime_ok"]
                    and out["mtf_down_aligned"] and out["bull_filter_ok"])
    return out


def record_trade_result(pnl_pct: float) -> dict:
    """Update CB counters + recent_pnls list after a trade closes.
    Returns the new state."""
    state = paper_repo.get_state() or paper_repo.ensure_state(START_EQUITY_USD)
    pnls = list(state.get("recent_pnls_json") or [])
    pnls.append(float(pnl_pct))
    pnls = pnls[-50:]  # keep last 50 for analysis

    consec = int(state.get("consec_losses") or 0)
    cb_until = int(state.get("cb_cooldown_until_ms") or 0)

    if pnl_pct <= 0:
        consec += 1
        if consec >= 3:
            cb_until = int(time.time() * 1000) + 24 * 60 * 60 * 1000
            consec = 0
    else:
        consec = 0

    paper_repo.update_state(
        cb_cooldown_until_ms=cb_until,
        consec_losses=consec,
        recent_pnls=pnls,
    )
    return {
        "consec_losses": consec,
        "cb_cooldown_until_ms": cb_until,
        "recent_pnls": pnls,
    }

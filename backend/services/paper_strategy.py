"""Live paper-trading strategy wrapper for V2 trend-following hybrid.

V2 hybrid (validated 2026-06-02 on 365d):
  ret_7d > +0.5%  → sell Put  (uptrend — Put premium decays)
  ret_7d < -0.5%  → sell Call (downtrend — Call premium decays)
  |ret_7d| < 0.5% → range — try both, MTF picks the side

Circuit breaker: 5 consecutive losses → 48h pause.
"""
from __future__ import annotations

import time

from db import paper_repo
from services.strategy_config import (
    CB_CONSEC_LIMIT,
    CB_PAUSE_HOURS,
    CALL_GEN_KWARGS,
    CALL_EXIT,
    DEFAULT_SIGMA,
    EXPIRY_TARGET_HOURS,
    PUT_GEN_KWARGS,
    PUT_EXIT,
    RET_7D_THRESHOLD,
    SPREAD_HALF_PCT,
)

# ───────────── Bybit-realistic sizing / friction model ─────────────
# Starting equity sized so the minimum 0.1-ETH lot fits in budget.
START_EQUITY_USD = 400.0
# Per trade: up to 15% of equity goes into option margin. On $400 → $60 budget.
MARGIN_PCT_PER_TRADE = 0.15
# Bybit min lot for ETH options.
LOT_MIN_ETH = 0.1
# Bybit Cross-Margin IM rate for short ETH options ≈ 10% of strike-notional.
IM_RATE = 0.10
# Maximum percentage of total equity that can be locked in margin across all open positions.
MAX_PORTFOLIO_MARGIN_PCT = 0.80
# Bybit taker fee on notional, capped at 12.5% of premium per side.
FEE_RATE = 0.0003
FEE_CAP_PCT_OF_PREMIUM = 0.125
# 7d return window in 5m bars (7 * 24 * 12 = 2016)
BARS_7D = 2016


def is_cb_active(state: dict, now_ms: int) -> bool:
    return now_ms < int(state.get("cb_cooldown_until_ms") or 0)


def dyn_size_factor(state: dict) -> float:
    """Halve size when 10-trade WR < 40%."""
    pnls = state.get("recent_pnls_json") or []
    if len(pnls) >= 10:
        wr = sum(1 for p in pnls[-10:] if p > 0) / 10.0
        if wr < 0.40:
            return 0.5
    return 1.0


def realistic_size_lots(free_margin_usd: float, equity_usd: float, strike: float,
                        premium_mid: float, state: dict) -> int:
    """How many 0.1-ETH lots fit in our margin budget at this signal."""
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


def compute_ret_7d(k5: list, idx: int) -> float:
    """Compute 7-day return ending at k5[idx]."""
    if idx < BARS_7D:
        return 0.0
    prev_close = k5[idx - BARS_7D]["close"]
    if prev_close <= 0:
        return 0.0
    return (k5[idx]["close"] - prev_close) / prev_close * 100


def allowed_sides(ret_7d: float) -> list[str]:
    """V2 trend-following: return list of sides allowed at this 7d return.

    ret_7d > +0.5%  → ["P"] (uptrend → only Put)
    ret_7d < -0.5%  → ["C"] (downtrend → only Call)
    |ret_7d| < 0.5% → ["P", "C"] (range — try both, MTF picks)
    """
    if ret_7d > RET_7D_THRESHOLD:
        return ["P"]
    elif ret_7d < -RET_7D_THRESHOLD:
        return ["C"]
    return ["P", "C"]


def determine_side(ret_7d: float) -> str | None:
    """Back-compat single-side picker for UI / conditions endpoint.

    Returns the first allowed side, or None only if both are allowed (range).
    The actual signal logic in paper_loop iterates allowed_sides() and lets
    the per-side gen filter (MTF, regime, vol) decide which one fires.
    """
    sides = allowed_sides(ret_7d)
    if len(sides) == 1:
        return sides[0]
    return None  # range zone — UI should display "both / MTF picks"


def evaluate_conditions(k5: list, k15: list, k1h: list) -> dict:
    """Check each entry condition for V2 trend-following hybrid.

    Returns a dict with per-condition booleans + summary + which side(s) active.
    For UI: when in range zone, both sides allowed and MTF picks; we report
    the MTF-preferred side as `active_side` so the frontend can show one
    coherent answer.
    """
    from .indicators import ema, realized_vol
    from .momentum_mtf import analyze_tf, consensus
    from .regime import detect_regime

    out = {
        "ready": False,
        "active_side": None,    # 'P', 'C', or None
        "dead_zone": False,     # always False under V2 (range allows both)
        "ret_7d": None,
        "vol_high": False,
        "regime_ok": False,
        "mtf_direction_ok": False,
        "spot": None,
        "vol_pctile": None,
        "regime": None,
        "mtf_direction": None,
        "mtf_aligned_count": None,
        "ema_ratio": None,
        "bull_filter_ok": True,
    }
    if not k5 or not k15 or not k1h:
        return out
    if len(k5) < BARS_7D or len(k15) < 50 or len(k1h) < 200:
        return out

    idx = len(k5) - 1
    out["spot"] = k5[idx]["close"]

    # 7d return → which sides are allowed under V2 trend-following
    ret_7d = compute_ret_7d(k5, idx)
    out["ret_7d"] = round(ret_7d, 2)
    sides = allowed_sides(ret_7d)

    # MTF (computed once, reused)
    HIST = 240
    s5 = k5[-HIST:] if len(k5) > HIST else k5
    s15 = k15[-HIST:] if len(k15) > HIST else k15
    s1h = k1h[-HIST:] if len(k1h) > HIST else k1h
    mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
    out["mtf_direction"] = mtf["direction"]
    out["mtf_aligned_count"] = mtf["tfs_aligned"]

    # When both sides allowed (range), pick the MTF-preferred side for display.
    if len(sides) == 1:
        active_side = sides[0]
    else:
        if mtf["direction"] == "up" and mtf["tfs_aligned"] >= 2:
            active_side = "P"
        elif mtf["direction"] == "down" and mtf["tfs_aligned"] >= 2:
            active_side = "C"
        else:
            active_side = None  # range + neutral MTF → won't fire
    out["active_side"] = active_side

    if active_side is None:
        # range zone with no MTF alignment — bot won't trade this bar
        return out

    # Select gen kwargs for the (display) active side
    gen_kw = PUT_GEN_KWARGS if active_side == "P" else CALL_GEN_KWARGS

    # 1) Vol percentile (1h realized vol vs rolling history)
    closes_1h = [c["close"] for c in s1h]
    rolling_vols: list[float] = []
    for j in range(20, len(closes_1h)):
        rv = realized_vol(closes_1h[:j + 1], lookback=24)
        if rv is not None:
            rolling_vols.append(rv)
    if len(rolling_vols) >= 30:
        current_vol = rolling_vols[-1]
        sorted_vols = sorted(rolling_vols)
        threshold_idx = int(len(sorted_vols) * gen_kw["vol_threshold"])
        threshold = sorted_vols[threshold_idx]
        below = sum(1 for v in sorted_vols if v < current_vol)
        pctile = below / len(sorted_vols)
        out["vol_pctile"] = round(pctile, 3)
        out["vol_high"] = current_vol >= threshold

    # 2) Regime
    reg = detect_regime(s1h)
    out["regime"] = reg.get("regime", "unknown")
    out["regime_ok"] = out["regime"] in gen_kw["regime_filter"]

    # 3) MTF direction filter (vs side-specific requirement)
    mtf_filter = gen_kw.get("mtf_direction_filter")
    if mtf_filter == "up":
        out["mtf_direction_ok"] = (mtf["direction"] == "up" and mtf["tfs_aligned"] >= 2)
    elif mtf_filter == "down":
        out["mtf_direction_ok"] = (mtf["direction"] == "down" and mtf["tfs_aligned"] >= 2)
    else:
        out["mtf_direction_ok"] = True

    # 4) Bull-market filter (Put side only)
    if active_side == "P":
        bull_max = gen_kw.get("bull_market_ratio_max")
        ema50 = ema(closes_1h, 50)
        ema200 = ema(closes_1h, 200)
        if ema50 is not None and ema200 not in (None, 0):
            ratio = ema50 / ema200
            out["ema_ratio"] = round(ratio, 4)
            if bull_max is not None:
                out["bull_filter_ok"] = ratio <= bull_max

    # Summary: ready if vol + regime + mtf + bull all pass
    out["ready"] = (out["vol_high"] and out["regime_ok"]
                    and out["mtf_direction_ok"] and out["bull_filter_ok"])
    return out


def record_trade_result(pnl_pct: float) -> dict:
    """Update CB counters + recent_pnls list after a trade closes."""
    state = paper_repo.get_state() or paper_repo.ensure_state(START_EQUITY_USD)
    pnls = list(state.get("recent_pnls_json") or [])
    pnls.append(float(pnl_pct))
    pnls = pnls[-50:]

    consec = int(state.get("consec_losses") or 0)
    cb_until = int(state.get("cb_cooldown_until_ms") or 0)

    if pnl_pct <= 0:
        consec += 1
        if consec >= CB_CONSEC_LIMIT:
            cb_until = int(time.time() * 1000) + CB_PAUSE_HOURS * 60 * 60 * 1000
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

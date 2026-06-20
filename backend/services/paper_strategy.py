"""Live paper-trading strategy wrapper for V2 trend-following hybrid.

V2 hybrid (validated 2026-06-02 on 365d):
  ret_7d > +0.5%  → sell Put  (uptrend — Put premium decays)
  ret_7d < -0.5%  → sell Call (downtrend — Call premium decays)
  |ret_7d| < 0.5% → range — try both, MTF picks the side

Circuit breaker: 5 consecutive losses → 48h pause.
"""
from __future__ import annotations

import os
import time

from db import paper_repo
from services.strategy_config import (
    CB_CONSEC_LIMIT,
    CB_PAUSE_HOURS,
    CALL_GEN_KWARGS,
    CALL_EXIT,
    DEFAULT_SIGMA,
    EXPIRY_TARGET_HOURS,
    get_side_expiry_h,
    PUT_GEN_KWARGS,
    PUT_EXIT,
    RET_7D_THRESHOLD,
    SPREAD_HALF_PCT,
)

# ───────────── Bybit-realistic sizing / friction model ─────────────
# Starting equity sized so the minimum 0.1-ETH lot fits in budget.
# Env-overridable (default = validated $400/0.15). 2026-06-20: paper deposit raised to
# $800 via PAPER_START_EQUITY_USD=800, keeping MO4/MP.15. Rationale (OOS deposit-curve,
# real DVOL): granularity KNEE ~$800 — below it the bot is capital-starved in high-signal
# (volatile) periods (train taken 187→264 at $800); above $800 ROI% is flat (~+39% OOS)
# and $ scales linearly. So $800 = capacity headroom for active periods at no ROI/maxDD
# cost. MO6 was REJECTED (halves ROI), BESTPICK REJECTED (overfit). Reversible via env.
START_EQUITY_USD = float(os.getenv("PAPER_START_EQUITY_USD", "400"))
# Per trade: up to 15% of equity goes into option margin. On $400 → $60 budget.
MARGIN_PCT_PER_TRADE = float(os.getenv("PAPER_MARGIN_PCT_PER_TRADE", "0.15"))
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


def entry_proximity(cond: dict, adx_score: float) -> dict:
    """How close the market is to a tradeable entry, as 0-100 + a zone label.

    Display/observability only — this drives the dashboard "proximity gauge", NOT
    position sizing (ADX-score sizing was rejected by backtest: it hurt compounded
    return and worsened drawdown). The needle is a weighted blend of the same
    factors the entry uses, each normalised to 0-1:
      - adx   : adx_score/10  (HIGH = low/falling ADX = range-like = best decay)
      - mtf   : aligned timeframes / 3
      - vol   : volatility percentile
      - bull  : bull-market filter pass (Put side)
    100 is reserved for `ready` (every gate actually passes), so the gauge never
    claims a full signal the bot wouldn't take.
    """
    def _f(x: float | None) -> float:
        return 0.0 if x is None else max(0.0, min(1.0, float(x)))

    f_adx = _f((adx_score or 0.0) / 10.0)
    f_mtf = _f((cond.get("mtf_aligned_count") or 0) / 3.0)
    f_vol = _f(cond.get("vol_pctile"))
    f_bull = 1.0 if cond.get("bull_filter_ok") else 0.0
    weights = {"adx": 0.40, "mtf": 0.25, "vol": 0.20, "bull": 0.15}
    composite = 100.0 * (weights["adx"] * f_adx + weights["mtf"] * f_mtf
                         + weights["vol"] * f_vol + weights["bull"] * f_bull)
    pct = 100.0 if cond.get("ready") else min(99.0, composite)
    pct = round(pct, 1)
    if pct >= 100.0:
        zone = "entry"        # all gates pass — bot will fire
    elif pct >= 80.0:
        zone = "ready"        # one factor short of entry
    elif pct >= 50.0:
        zone = "preparing"    # factors aligning
    else:
        zone = "waiting"      # far from a signal
    return {
        "proximity_pct": pct,
        "zone": zone,
        "factors": {"adx": round(f_adx, 3), "mtf": round(f_mtf, 3),
                    "vol": round(f_vol, 3), "bull": round(f_bull, 3)},
        "weights": weights,
    }


def _next_cb_state(consec: int, cb_until: int, pnls: list[float],
                   pnl_pct: float, now_ms: int) -> dict:
    """Pure: given the *current* CB counters, compute the next ones after one
    trade closes at `pnl_pct`. No I/O — unit-testable on its own.

    A loss bumps the consecutive-loss counter; hitting CB_CONSEC_LIMIT arms the
    circuit breaker (cooldown window) and resets the counter. Any win resets it.
    `recent_pnls` is a rolling window of the last 50 results.
    """
    pnls = (list(pnls) + [float(pnl_pct)])[-50:]
    if pnl_pct <= 0:
        consec += 1
        if consec >= CB_CONSEC_LIMIT:
            cb_until = now_ms + CB_PAUSE_HOURS * 60 * 60 * 1000
            consec = 0
    else:
        consec = 0
    return {
        "consec_losses": consec,
        "cb_cooldown_until_ms": cb_until,
        "recent_pnls": pnls,
    }


def record_trade_result(pnl_pct: float) -> dict:
    """Update CB counters + recent_pnls list after a trade closes.

    The read-modify-write of consec_losses MUST be atomic: when two positions
    close in one loop iteration, a split read/update would lose one increment and
    the breaker could miss 5 consecutive losses (FUTURE_WORK §5.2). We therefore
    do the whole transition inside a single locked transaction in the repo, with
    `_next_cb_state` as the pure decision step.
    """
    paper_repo.ensure_state(START_EQUITY_USD)
    return paper_repo.record_trade_outcome(
        float(pnl_pct), int(time.time() * 1000), _next_cb_state,
    )

"""Live paper-trading strategy wrapper around the V3 hybrid strategy.

V3 Hybrid (validated 2026-06-01):
  7d-return guided switching between Put/Call sides:
    • |7d_ret| < 2% → sell Put (range regime, premium decay)
    • 7d_ret > +2% → sell Call (uptrend — Put is dangerous)
    • 7d_ret < -2% → sell Put (downtrend — Put profits)
  Circuit breaker: 5 consecutive losses → 48h pause

Previous: Put-only (cd=4, h=96) — available via PAPER_VARIANT=alt.
"""
from __future__ import annotations

import time

from db import paper_repo
from services.strategy_config import (
    CB_CONSEC_LIMIT,
    CB_PAUSE_HOURS,
    CALL_GEN_KWARGS,
    CALL_EXIT,
    CALL_RET_MIN,
    DEFAULT_SIGMA,
    EXPIRY_TARGET_HOURS,
    PUT_GEN_KWARGS,
    PUT_EXIT,
    PUT_RET_MAX,
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


def determine_side(ret_7d: float) -> str | None:
    """Determine which side to trade based on asymmetric 7d return thresholds.

    Returns 'P', 'C', or None (dead zone — don't trade).

    Config B (validated 2026-06-01):
      ret < -2.5% → Put  (strong downtrend)
      ret > +1.0% → Call (uptrend)
      -2.5%..+1.0% → None (dead zone, slow bleed)
    """
    if ret_7d < PUT_RET_MAX:
        return "P"
    elif ret_7d > CALL_RET_MIN:
        return "C"
    return None


def evaluate_conditions(k5: list, k15: list, k1h: list) -> dict:
    """Check each entry condition for the V3 hybrid strategy.

    Returns a dict with per-condition booleans + summary + which side is active.
    """
    from .indicators import ema, realized_vol
    from .momentum_mtf import analyze_tf, consensus
    from .regime import detect_regime

    out = {
        "ready": False,
        "active_side": None,    # 'P', 'C', or None (dead zone)
        "dead_zone": False,
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

    # 7d return → determine active side (or dead zone)
    ret_7d = compute_ret_7d(k5, idx)
    out["ret_7d"] = round(ret_7d, 2)
    active_side = determine_side(ret_7d)
    out["active_side"] = active_side

    if active_side is None:
        out["dead_zone"] = True
        return out  # no trade zone — conditions irrelevant

    # Select gen kwargs for the active side
    gen_kw = PUT_GEN_KWARGS if active_side == "P" else CALL_GEN_KWARGS

    # Use the same HIST=240 window as the generator
    HIST = 240
    s5 = k5[-HIST:] if len(k5) > HIST else k5
    s15 = k15[-HIST:] if len(k15) > HIST else k15
    s1h = k1h[-HIST:] if len(k1h) > HIST else k1h

    # 1) Vol percentile
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

    # 3) MTF direction
    mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
    out["mtf_direction"] = mtf["direction"]
    out["mtf_aligned_count"] = mtf["tfs_aligned"]
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

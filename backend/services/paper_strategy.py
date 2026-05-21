"""Live paper-trading strategy wrapper around the validated winner.

Uses `gen_sell_premium_iv_high` with the iter4-confirmed config plus the
iter5 overlays (bull-market filter, consecutive-loss CB, dynamic sizing).
"""
from __future__ import annotations

import time

from db import paper_repo

WINNER_GEN_KWARGS = {
    "vol_threshold": 0.7,
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
START_EQUITY_USD = 100.0
SIZE_PCT_OF_EQUITY = 0.10   # 10%
SIZE_MIN_USD = 5.0
SIZE_MAX_USD = 50.0


def is_cb_active(state: dict, now_ms: int) -> bool:
    return now_ms < int(state.get("cb_cooldown_until_ms") or 0)


def current_size_usd(state: dict, equity_usd: float) -> float:
    base = equity_usd * SIZE_PCT_OF_EQUITY
    # Dynamic sizing — halve if last-10 WR < 0.40
    pnls = state.get("recent_pnls_json") or []
    if len(pnls) >= 10:
        recent = pnls[-10:]
        wr = sum(1 for p in recent if p > 0) / 10.0
        if wr < 0.40:
            base *= 0.5
    return max(SIZE_MIN_USD, min(SIZE_MAX_USD, base))


def evaluate_conditions(k5: list, k15: list, k1h: list) -> dict:
    """Check each entry condition against the LATEST bar without emitting a
    signal. Returns a dict with per-condition booleans + summary."""
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
    s5, s15, s1h = k5, k15, k1h

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
        # rank percentile
        below = sum(1 for v in sorted_vols if v < current_vol)
        pctile = below / len(sorted_vols)
        out["vol_pctile"] = round(pctile, 3)
        out["vol_high"] = pctile >= WINNER_GEN_KWARGS["vol_threshold"]

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

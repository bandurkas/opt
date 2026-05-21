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

"""Strategy params — single source of truth for live + backtest.

Kept dependency-free so ``local_backtest.py`` can import without psycopg2.
Winner: iter3 put_refine + iter2 refine — bull 1.08, cd12, +17.78% OOS
(see sweep_results/local_opt_iter2.json, final_validation.json).
"""
from __future__ import annotations

import copy
import os

LIVE_GEN_KWARGS = {
    "vol_threshold": 0.50,
    "regime_filter": ["range"],
    "side": "P",
    "adx_max": None,
    "mtf_direction_filter": "up",
    "bull_market_ratio_max": 1.08,
    "cooldown_bars": 12,
}

LIVE_EXIT = {
    "tp1_pct": 0.50,
    "tp2_pct": 0.70,
    "sl_pct": 1.50,
    "hold_h": 72,
}

# More signals (~152 OOS trades/yr in BS sim). PAPER_VARIANT=alt
LIVE_GEN_KWARGS_ALT = {
    **LIVE_GEN_KWARGS,
    "cooldown_bars": 6,
}

# Pre-6be2fbc baseline for A/B validation scripts
BASELINE_CALL_GEN_KWARGS = {
    "vol_threshold": 0.60,
    "regime_filter": ["range", "transition"],
    "side": "C",
    "adx_max": None,
    "mtf_direction_filter": "down",
    "bull_market_ratio_max": 1.05,
    "cooldown_bars": 6,
}

BASELINE_CALL_EXIT = {
    "tp1_pct": 0.30,
    "tp2_pct": 0.50,
    "sl_pct": 0.50,
    "hold_h": 24,
}

DEFAULT_SIGMA = 0.6
EXPIRY_TARGET_HOURS = 168
SPREAD_HALF_PCT = 1.0


def active_gen_kwargs() -> dict:
    """Return live gen kwargs; PAPER_VARIANT=alt selects higher-frequency preset."""
    if os.getenv("PAPER_VARIANT", "").strip().lower() == "alt":
        return copy.deepcopy(LIVE_GEN_KWARGS_ALT)
    return copy.deepcopy(LIVE_GEN_KWARGS)


def active_exit() -> dict:
    return copy.deepcopy(LIVE_EXIT)


def exit_for_backtest(exit_kw: dict) -> dict:
    """Map LIVE_EXIT keys to local_optimizer / backtest exit dict shape."""
    return {
        "tp1": exit_kw["tp1_pct"],
        "tp2": exit_kw["tp2_pct"],
        "sl": exit_kw["sl_pct"],
        "hold_h": exit_kw["hold_h"],
    }

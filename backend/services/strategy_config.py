"""Strategy params — single source of truth for live + backtest.

Kept dependency-free so ``local_backtest.py`` can import without psycopg2.

V3 Hybrid (validated 2026-06-01):
  7d-return guided switching between Put/Call sides:
    • |7d_ret| < 2% → sell Put (range regime, premium decay)
    • 7d_ret > +2% → sell Call (uptrend — Put is dangerous)
    • 7d_ret < -2% → sell Put (downtrend — Put profits)
  Circuit breaker: 5 consecutive losses → 48h pause
  Holdout-90d: n=125, avg +11.97%, WR 69.6%, cl=10
  Sensitivity: 15/15 cells (σ=0.40-0.80, spread=1-4%) positive

Previous: cd=4/h=96 (Put-only) — kept as PAPER_VARIANT=alt.
"""
from __future__ import annotations

import copy
import os

# ── V3 Hybrid: 7d-return switching + per-side exits ──

RET_THRESHOLD = 2.0  # 7d return % threshold for side selection

PUT_GEN_KWARGS = {
    "vol_threshold": 0.50,
    "regime_filter": ["range"],
    "side": "P",
    "adx_max": None,
    "mtf_direction_filter": "up",
    "bull_market_ratio_max": None,
    "cooldown_bars": 4,
}

PUT_EXIT = {
    "tp1_pct": 0.50,
    "tp2_pct": 0.70,
    "sl_pct": 1.50,
    "hold_h": 96,
}

CALL_GEN_KWARGS = {
    "vol_threshold": 0.60,
    "regime_filter": ["range", "transition"],
    "side": "C",
    "adx_max": None,
    "mtf_direction_filter": "down",
    "bull_market_ratio_max": 1.05,
    "cooldown_bars": 6,
}

CALL_EXIT = {
    "tp1_pct": 0.30,
    "tp2_pct": 0.50,
    "sl_pct": 1.00,
    "hold_h": 24,
}

CB_CONSEC_LIMIT = 5       # consecutive losses before cooldown
CB_PAUSE_HOURS = 48       # cooldown duration

# ── Previous Put-only config (for comparison / alt mode) ──
LIVE_GEN_KWARGS = PUT_GEN_KWARGS  # alias for backward compat
LIVE_EXIT = PUT_EXIT

LIVE_GEN_KWARGS_ALT = {
    **PUT_GEN_KWARGS,
    "cooldown_bars": 6,
}

# Pre-6be2fbc baseline (Call-only)
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
    """Return hybrid gen kwargs; PAPER_VARIANT=alt selects Put-only preset."""
    if os.getenv("PAPER_VARIANT", "").strip().lower() == "alt":
        return copy.deepcopy(LIVE_GEN_KWARGS_ALT)
    return copy.deepcopy(PUT_GEN_KWARGS)


def active_exit() -> dict:
    """Return active exit params; for hybrid, caller should use per-side exits."""
    return copy.deepcopy(PUT_EXIT)


def get_side_exits(side: str) -> dict:
    """Return exit params for the given side (P or C)."""
    if side == "C":
        return copy.deepcopy(CALL_EXIT)
    return copy.deepcopy(PUT_EXIT)


def get_side_gen_kwargs(side: str) -> dict:
    """Return gen kwargs for the given side."""
    if side == "C":
        return copy.deepcopy(CALL_GEN_KWARGS)
    return copy.deepcopy(PUT_GEN_KWARGS)


def exit_for_backtest(exit_kw: dict) -> dict:
    """Map LIVE_EXIT keys to local_optimizer / backtest exit dict shape."""
    return {
        "tp1": exit_kw["tp1_pct"],
        "tp2": exit_kw["tp2_pct"],
        "sl": exit_kw["sl_pct"],
        "hold_h": exit_kw["hold_h"],
    }

"""Strategy params — single source of truth for live + backtest.

Kept dependency-free so ``local_backtest.py`` can import without psycopg2.

Winner (proper-holdout protocol, finalize_best 2026-06-01):
  side=P · mtf_up · range · vol≥0.50 · cooldown=6 · NO bull-filter
  holdout-90d: n=148 +13.78%/trade, sharpe=0.40 → ≈+$54/month on $400.

Why no bull-filter: across all 6 cd × bull cells, holdout PnL is identical
within the cooldown group (cd=12 → +15.84%, cd=6 → +13.78%) — the filter
adds no edge on unseen data; dropping it just keeps more signals.

Why cd=6 over cd=12: cd=6 produces ~1.83× more holdout signals (148 vs 81)
at -13% per-trade edge — net ~+58% monthly $ on the same $400 base.
Test-sharpe also higher (0.40 vs 0.35). cd=12 is kept as the conservative
ALT (PAPER_VARIANT=alt) for low-volume preference.
"""
from __future__ import annotations

import copy
import os

# LIVE: max-monthly-$ config selected by user after finalize_best 2026-06-01.
LIVE_GEN_KWARGS = {
    "vol_threshold": 0.50,
    "regime_filter": ["range"],
    "side": "P",
    "adx_max": None,
    "mtf_direction_filter": "up",
    "bull_market_ratio_max": None,
    "cooldown_bars": 6,
}

LIVE_EXIT = {
    "tp1_pct": 0.50,
    "tp2_pct": 0.70,
    "sl_pct": 1.50,
    "hold_h": 72,
}

# Conservative variant: half the signal rate, +15% per-trade edge. PAPER_VARIANT=alt
LIVE_GEN_KWARGS_ALT = {
    **LIVE_GEN_KWARGS,
    "cooldown_bars": 12,
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

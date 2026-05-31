"""Strategy params — single source of truth for live + backtest.

Kept dependency-free so ``local_backtest.py`` can import without psycopg2.

Winner (54-cell parallel sweep on proper-holdout, 2026-06-01):
  side=P · mtf_up · range · vol≥0.50 · cooldown=4 · hold=96h · NO bull-filter
  holdout-90d: n=275 +14.93%/trade, sharpe=0.25 → +$114/month theoretical,
  ~$85-95/month realistic after $400 margin cap.

Key sweep findings (sweep_results/parallel_cd_vol_hold.json):
- hold_h=96 beats hold=72 by ~+44% on $/month across all cd values (more
  theta captured per trade).
- cd=3 has highest theoretical $ ($143/mo) but ~12.7 avg concurrent positions
  vs $400 budget's ~8.4 lot capacity → ~30% signals dropped to margin.
- cd=4 sits at the sweet spot: $114/mo theoretical, ~12 concurrent (some
  margin clipping), still 1.6× current cd=6/h=72 winner.

Phase 4 hybrid test (Put+Call combo) → REJECTED.
Sell-Call MTF-down on 2025-2026 ETH data has NEGATIVE edge (-31.74%/trade,
-$126/mo) due to persistent ETH up-drift. Merging Call signals destroys
the Put edge (-$72/mo merged).

cd=6/h=72 (the previous LIVE) kept as PAPER_VARIANT=alt — lower frequency,
no margin contention, sharpe 0.28, +$54/mo.
"""
from __future__ import annotations

import copy
import os

# LIVE: max-$/month config from 54-cell sweep, 2026-06-01.
LIVE_GEN_KWARGS = {
    "vol_threshold": 0.50,
    "regime_filter": ["range"],
    "side": "P",
    "adx_max": None,
    "mtf_direction_filter": "up",
    "bull_market_ratio_max": None,
    "cooldown_bars": 4,
}

LIVE_EXIT = {
    "tp1_pct": 0.50,
    "tp2_pct": 0.70,
    "sl_pct": 1.50,
    "hold_h": 96,
}

# Conservative variant: pre-sweep LIVE — half the signals, no margin contention.
# PAPER_VARIANT=alt selects this preset.
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

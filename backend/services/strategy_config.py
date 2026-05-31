"""Strategy params — single source of truth for live + backtest.

Kept dependency-free so ``local_backtest.py`` can import without psycopg2.
"""
from __future__ import annotations

WINNER_GEN_KWARGS = {
    # 365d local sweep (2026-05-31): sell ATM Put in MTF-up + high-vol range beats
    # the old sell-Call MTF-down config (+14.8% vs +3.7% avg/trade full-year BS sim).
    # See sweep_results/validation.json and local_opt_iter1.json.
    "vol_threshold": 0.50,
    "regime_filter": ["range"],
    "side": "P",
    "adx_max": None,
    "mtf_direction_filter": "up",
    "bull_market_ratio_max": 1.05,
    "cooldown_bars": 12,
}

WINNER_EXIT = {
    "tp1_pct": 0.50,
    "tp2_pct": 0.70,
    "sl_pct": 1.50,
    "hold_h": 72,
}

# Alternate (more trades, similar edge): cooldown_bars=6 → ~488 signals/yr, +13.3% avg
WINNER_GEN_KWARGS_ALT = {
    **WINNER_GEN_KWARGS,
    "cooldown_bars": 6,
}

# Sigma constant used to price fallbacks. Bybit live IV will be used when
# available; this is only for the BS fallback path.
DEFAULT_SIGMA = 0.6
EXPIRY_TARGET_HOURS = 168  # ~7 days

# Half of round-trip spread. 1.0%·2 = 2% total slippage.
SPREAD_HALF_PCT = 1.0

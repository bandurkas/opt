"""Strategy params — single source of truth for live + backtest.

Kept dependency-free so ``local_backtest.py`` can import without psycopg2.

V2 trend-following hybrid (validated 2026-06-02 on 365d):
  ret_7d > +0.5%  → sell Put  (uptrend — Put premium decays)
  ret_7d < -0.5%  → sell Call (downtrend — Call premium decays)
  |ret_7d| < 0.5% → range — try both, MTF picks the side

  365d stats vs alternatives:
    Pure Put:   n=779 avg +17.81% WR 72% — but 4 losing months incl. -65/-80/-50
    Pure Call:  n=837 avg +3.71%  WR 57% — 6 losing months
    V2 hybrid:  n=936 avg +7.09%  WR 60% — only 2 losing months (Sep -42, Nov -3)
    May 2026 (current down regime): Pure Put -50.6%, V2 hybrid +10.6%

Previous configurations:
  - Config B (2026-06-01 mean-reversion, ret<-2.5→P, ret>+1.0→C): deadlocked
    in May 2026 (ret=-5.1% → wanted Put but MTF=down rejected it). 0 trades/24h.
  - cd=4/h=96 Put-only (54-cell sweep winner): broke in May with -68%/trade.

Circuit breaker: 5 consecutive losses → 48h pause.
"""
from __future__ import annotations

import copy
import os

# ── V2 trend-following hybrid (validated 2026-06-02) ──
# Per-bar logic:
#   ret_7d > +RET_7D_THRESHOLD → only Put allowed
#   ret_7d < -RET_7D_THRESHOLD → only Call allowed
#   |ret_7d| < RET_7D_THRESHOLD → both sides allowed, MTF filter picks

RET_7D_THRESHOLD = 0.5    # V2 hybrid: 0.5% boundary (best from {0.5,1,1.5,2,3} sweep)

# Legacy Config B constants kept for back-compat with research/* scripts.
# NOT used by paper_loop or live trading.
PUT_RET_MAX = -2.5   # legacy Config B (mean-reversion): "Put when ret < -2.5%"
CALL_RET_MIN = 1.0   # legacy Config B (mean-reversion): "Call when ret > +1.0%"

PUT_GEN_KWARGS = {
    "vol_threshold": 0.50,
    "regime_filter": ["range"],
    "side": "P",
    "adx_max": None,
    "mtf_direction_filter": "up",
    "bull_market_ratio_max": None,
    "cooldown_bars": 6,
}

PUT_EXIT = {
    "tp1_pct": 0.50,
    "tp2_pct": 0.70,
    # 2026-06-19 SL sweep (sl_sweep.py + sl_deposit_sweep.py, full year, real DVOL,
    # $400 margin/MAX_OPEN4/compound/CB engine): widening Put stop 1.50→2.00 lifts
    # the account $612→$712 (+78% vs +53%) at ~flat maxDD (24.1%→25.1%), and HOLDOUT
    # per-trade avg +6.08%→+15.20% (OOS, not a compounding mirage). Puts have 168h to
    # recover; the tight 1.50 stop whipsaws out of positions that revert. 2.50 over-widens
    # (slots clogged, throughput 319→258). Calls stay 0.75 — widening HURTS there (24h
    # recycle + margin slots), so this change is PUT-ONLY.
    "sl_pct": 2.00,
    "hold_h": 96,  # 4 days — validated exit (168h drops Put avg +16.7%→+2.6%)
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
    "tp1_pct": 0.40,
    "tp2_pct": 0.80,
    "sl_pct": 0.75,
    "hold_h": 24,  # short-dated (24h) re-tuned exits — holdout +7.37%→+14.79%/trade (2026-06-17)
}

# 2026-06-21 dollar-margin SL for Calls (mirrors BTC straddle's btc_straddle_sl.py:
# margin = IM_RATE*strike + entry_credit; SL trips at CALL_SL_DOLLAR_FRAC*margin of
# buyback loss, instead of a fixed %-of-entry-credit). $-account engine (margin/
# MAX_OPEN4/compound/CB, real DVOL, 70/30 train/holdout, eth_dollar_sl_deposit_sweep.py):
# frac=0.10 strictly dominates the live %-SL=0.75 (FINAL $2933 vs $2726, SAME maxDD
# 20.8%, TRAIN+HOLDOUT improve together, no single-month driver). Put side has NO
# viable dollar-SL operating point (eth_dollar_sl_backtest.py) — stays %-of-premium.
# Env-overridable so the frac can be raised later (0.12-0.15 showed more upside at a
# small maxDD cost) without a code change.
CALL_SL_DOLLAR_FRAC = float(os.getenv("CALL_SL_DOLLAR_FRAC", "0.10"))

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
# Per-side target expiry: Calls go short-dated (24h, re-tuned exits) — holdout +14.79%/trade,
# $400-model 215→332 trades, +34%→+53% (2026-06-17). Puts stay long (no short edge at the edge).
CALL_TARGET_EXPIRY_H = 24
PUT_TARGET_EXPIRY_H = 168
EXPIRY_TARGET_HOURS = PUT_TARGET_EXPIRY_H  # alias for backward compat (Put default)
SPREAD_HALF_PCT = 1.0


def get_side_expiry_h(side: str) -> int:
    """Target option expiry (hours) for the given side: 24h Calls, 168h Puts."""
    return CALL_TARGET_EXPIRY_H if (side or "C").upper() == "C" else PUT_TARGET_EXPIRY_H


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

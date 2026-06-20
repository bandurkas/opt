"""Walk-forward backtest of the directional signal engine.

What's modeled:
  - MTF momentum (5m+15m+1h) + ADX regime, EXACTLY the same code as live
  - Underlying realized return at multiple horizons (no look-ahead)
  - Synthetic option premium via Black-Scholes (constant IV per run)
  - Exit rules from exits.py applied to the synthetic premium path

What's NOT modeled:
  - Real Bybit historical option prices (chain not available without paid feed)
  - Real bid-ask spread / fill quality
  - IV change over time (constant IV assumption)
  - Theta acceleration on the gamma curve as expiry approaches
  - Walls / liquidity in real options book

Treat results as a SANITY CHECK on signal direction & exit logic, NOT a P&L
guarantee. Real edge will come from live calibration.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, median

# Ensure backend/ is on sys.path when running as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import backtest_bs as bs  # noqa: E402
from services.backtest_data import fetch_set  # noqa: E402
from services.exits import build_exit_plan  # noqa: E402
from services.indicators import atr  # noqa: E402
from services.momentum_mtf import analyze_tf, consensus  # noqa: E402
from services.regime import detect_regime  # noqa: E402


# ───────────────────────── walk-forward iterator ─────────────────────────

def _walk(klines_5m, klines_15m, klines_1h, history_window: int = 120):
    """Yield (ts_close_ms, c5_slice, c15_slice, c1h_slice) bar-by-bar on 5m."""
    i15 = 0
    i1h = 0
    for i, c5 in enumerate(klines_5m):
        ts_end = c5["start_ms"] + 5 * 60 * 1000  # close time of this 5m bar
        # advance 15m index while the 15m candle has closed by ts_end
        while i15 < len(klines_15m) and klines_15m[i15]["start_ms"] + 15 * 60 * 1000 <= ts_end:
            i15 += 1
        while i1h < len(klines_1h) and klines_1h[i1h]["start_ms"] + 60 * 60 * 1000 <= ts_end:
            i1h += 1
        s5 = klines_5m[max(0, i + 1 - history_window):i + 1]
        s15 = klines_15m[max(0, i15 - history_window):i15]
        s1h = klines_1h[max(0, i1h - history_window):i1h]
        yield ts_end, c5["close"], s5, s15, s1h


# ───────────────────────── signal detection ─────────────────────────

def _is_signal(side: str, mtf: dict, regime: dict, min_alignment: int) -> bool:
    direction_needed = "up" if side == "C" else "down"
    if mtf["direction"] != direction_needed:
        return False
    if mtf["tfs_aligned"] < min_alignment:
        return False
    # Skip flat regime for trend-continuation entries
    if regime.get("regime") == "range":
        return False
    return True


def _score_signal(mtf: dict, regime: dict) -> float:
    """Simplified composite score used for bucketing in the report (0..10).
    Mirrors the direction/momentum/regime portion of signal_scoring.
    """
    s = 0.0
    if mtf["tfs_aligned"] == 3:
        s += 2.0
    elif mtf["tfs_aligned"] == 2:
        s += 1.0
    if mtf.get("accelerating"):
        s += 1.5
    vz = mtf.get("tf_1h", {}).get("volume_zscore") or 0
    if vz >= 2:
        s += 1.0
    elif vz <= -1:
        s -= 0.5
    if regime.get("regime") == "trend":
        s += 1.0
    return max(0.0, min(10.0, round(s + 4.5, 1)))  # offset so neutral baseline ≈ 4.5


# ───────────────────────── option simulation ─────────────────────────

def _simulate_option_trade(
    *,
    side: str,
    entry_spot: float,
    sigma: float,
    expiry_hours: float,
    bars_5m_forward: list[dict],
    tp1_pct: float,
    tp2_pct: float,
    sl_pct: float,
    horizon_hours: float,
    spread_pct: float = 0.0,
    tsl_trigger_pct: float = 0.0,
    tsl_offset_pct: float = 0.0,
    position: str = "long_premium",  # 'long_premium' or 'short_premium'
    strike_round_to: float = 25.0,   # listed strike spacing; ETH=$25, BTC real spacing is wider
) -> dict:
    """Walk forward bar-by-bar on the underlying path. Compute synthetic premium
    via BS at each step. Apply TP1/TP2/SL exits. Return resolution + P&L.

    ATM strike (rounded to nearest `strike_round_to`). T shrinks by 5m each
    step (theta decay baked in).
    """
    strike = round(entry_spot / strike_round_to) * strike_round_to

    T0 = expiry_hours / (24 * 365)
    bs_mid = bs.price(side, entry_spot, strike, T0, sigma)
    if bs_mid <= 0.01:
        return {"resolution": "no_entry", "pnl_pct": 0.0}

    half_spread = spread_pct / 200.0  # one-way slippage

    if position == "short_premium":
        # Sell at bid → receive credit, want premium to DECAY
        entry_credit = bs_mid * (1 - half_spread)
        # TP fires when we can buy back at ask cheap: ask <= entry_credit * (1 - tp_pct)
        # ask = mid * (1 + hs) → threshold mid: mid <= entry_credit * (1 - tp_pct) / (1 + hs)
        tp1_mid_threshold = entry_credit * (1 - tp1_pct) / (1 + half_spread) if (1 + half_spread) > 0 else 0
        tp2_mid_threshold = entry_credit * (1 - tp2_pct) / (1 + half_spread) if (1 + half_spread) > 0 else 0
        sl_mid_threshold = entry_credit * (1 + sl_pct) / (1 + half_spread)  # mid at which loss triggers
        return _simulate_short_premium(
            side=side, strike=strike, sigma=sigma, expiry_hours=expiry_hours,
            bars_5m_forward=bars_5m_forward, bars_to_use_limit=int(horizon_hours * 12),
            entry_credit=entry_credit, half_spread=half_spread,
            tp1_mid=tp1_mid_threshold, tp2_mid=tp2_mid_threshold, sl_mid=sl_mid_threshold,
            tp1_pct=tp1_pct, tp2_pct=tp2_pct, sl_pct=sl_pct,
        )

    # === LONG PREMIUM path (existing logic) ===
    # Buyer pays ask = mid * (1 + half_spread)
    entry_premium = bs_mid * (1 + half_spread)

    # TP/SL targets are net premiums the buyer can SELL (i.e. receive on bid).
    tp1_premium = entry_premium * (1 + tp1_pct) / (1 - half_spread) if half_spread < 1 else entry_premium * (1 + tp1_pct)
    tp2_premium = entry_premium * (1 + tp2_pct) / (1 - half_spread) if half_spread < 1 else entry_premium * (1 + tp2_pct)
    sl_premium = entry_premium * (1 - sl_pct) / (1 - half_spread) if half_spread < 1 else entry_premium * (1 - sl_pct)

    closed_first_half = False
    pnl_first_half_pct = 0.0
    bars_to_use = min(len(bars_5m_forward), int(horizon_hours * 12))  # 12 bars per hour

    # Trailing-stop: starts at SL%, ratchets UP when premium hits trigger.
    # Effectively replaces SL once activated (one-way floor).
    tsl_active = tsl_trigger_pct > 0
    current_floor_pnl = -sl_pct  # current SL/TSL floor in pnl-fraction terms

    for bi in range(bars_to_use):
        bar = bars_5m_forward[bi]
        elapsed_h = (bi + 1) * 5 / 60
        T = max(0.0, (expiry_hours - elapsed_h) / (24 * 365))
        hi_spot = bar["high"]
        lo_spot = bar["low"]

        if side == "C":
            high_premium = bs.price(side, hi_spot, strike, T, sigma)
            low_premium = bs.price(side, lo_spot, strike, T, sigma)
        else:
            high_premium = bs.price(side, lo_spot, strike, T, sigma)
            low_premium = bs.price(side, hi_spot, strike, T, sigma)

        # Convert to pnl-fractions for TSL logic
        # net pnl_pct(premium) = (premium*(1-hs) - entry_premium) / entry_premium
        hi_pnl_frac = (high_premium * (1 - half_spread) - entry_premium) / entry_premium
        lo_pnl_frac = (low_premium * (1 - half_spread) - entry_premium) / entry_premium

        # Update trailing floor if premium reached/exceeded trigger
        if tsl_active and hi_pnl_frac >= tsl_trigger_pct:
            candidate = hi_pnl_frac - tsl_offset_pct
            if candidate > current_floor_pnl:
                current_floor_pnl = candidate

        # Floor (TSL or original SL) check
        if lo_pnl_frac <= current_floor_pnl:
            exit_pnl = current_floor_pnl
            if closed_first_half:
                total = (pnl_first_half_pct + exit_pnl) / 2
            else:
                total = exit_pnl
            resolution = "tsl" if current_floor_pnl > -sl_pct else "sl"
            return {"resolution": resolution, "pnl_pct": round(total * 100, 2), "bars_held": bi + 1}

        # TP2 (all remaining size)
        if high_premium >= tp2_premium:
            tp2_pnl = (tp2_premium - entry_premium) / entry_premium
            if closed_first_half:
                total = (pnl_first_half_pct + tp2_pnl) / 2
            else:
                total = tp2_pnl
            return {"resolution": "tp2", "pnl_pct": round(total * 100, 2), "bars_held": bi + 1}

        # TP1 (half size)
        if not closed_first_half and high_premium >= tp1_premium:
            closed_first_half = True
            pnl_first_half_pct = (tp1_premium - entry_premium) / entry_premium

    # Time stop — close at last available premium (selling at bid)
    last_bar = bars_5m_forward[bars_to_use - 1] if bars_to_use > 0 else None
    if last_bar is None:
        return {"resolution": "no_data", "pnl_pct": 0.0}
    elapsed_h = bars_to_use * 5 / 60
    T = max(0.0, (expiry_hours - elapsed_h) / (24 * 365))
    final_spot = last_bar["close"]
    final_mid = bs.price(side, final_spot, strike, T, sigma)
    final_received = final_mid * (1 - half_spread)
    final_pnl = (final_received - entry_premium) / entry_premium
    if closed_first_half:
        total = (pnl_first_half_pct + final_pnl) / 2
        resolution = "time_stop_partial" if final_pnl < 0 else "tp1_only"
    else:
        total = final_pnl
        resolution = "time_stop"
    return {"resolution": resolution, "pnl_pct": round(total * 100, 2), "bars_held": bars_to_use}


def _simulate_short_premium(
    *, side: str, strike: float, sigma: float, expiry_hours: float,
    bars_5m_forward: list[dict], bars_to_use_limit: int,
    entry_credit: float, half_spread: float,
    tp1_mid: float, tp2_mid: float, sl_mid: float,
    tp1_pct: float, tp2_pct: float, sl_pct: float,
) -> dict:
    """Short-premium path. Premium decay = profit. We exit when buyback price
    crosses thresholds. Simpler than long path since we don't split positions."""
    bars_to_use = min(len(bars_5m_forward), bars_to_use_limit)
    for bi in range(bars_to_use):
        bar = bars_5m_forward[bi]
        elapsed_h = (bi + 1) * 5 / 60
        T = max(0.0, (expiry_hours - elapsed_h) / (24 * 365))
        hi_spot = bar["high"]
        lo_spot = bar["low"]

        if side == "C":
            premium_high = bs.price(side, hi_spot, strike, T, sigma)  # WORST for short Call
            premium_low = bs.price(side, lo_spot, strike, T, sigma)   # BEST for short Call
        else:
            premium_high = bs.price(side, lo_spot, strike, T, sigma)  # WORST for short Put
            premium_low = bs.price(side, hi_spot, strike, T, sigma)   # BEST for short Put

        # SL: premium went too high
        if premium_high >= sl_mid:
            return {"resolution": "sl", "pnl_pct": round(-sl_pct * 100, 2), "bars_held": bi + 1}
        # TP2: premium decayed a lot
        if premium_low <= tp2_mid:
            return {"resolution": "tp2", "pnl_pct": round(tp2_pct * 100, 2), "bars_held": bi + 1}
        # TP1 not implemented for short (treat as TP2 only for simplicity)

    # Time stop: close at current ask
    last_bar = bars_5m_forward[bars_to_use - 1] if bars_to_use > 0 else None
    if last_bar is None:
        return {"resolution": "no_data", "pnl_pct": 0.0}
    elapsed_h = bars_to_use * 5 / 60
    T = max(0.0, (expiry_hours - elapsed_h) / (24 * 365))
    final_mid = bs.price(side, last_bar["close"], strike, T, sigma)
    buyback_ask = final_mid * (1 + half_spread)
    pnl = (entry_credit - buyback_ask) / entry_credit
    return {"resolution": "time_stop", "pnl_pct": round(pnl * 100, 2), "bars_held": bars_to_use}


# ───────────────────────── main runner ─────────────────────────

def generate_raw_signals(
    klines_5m: list[dict],
    klines_15m: list[dict],
    klines_1h: list[dict],
    min_alignment: int = 2,
    cooldown_bars: int = 24,
    fade: bool = False,
) -> list[dict]:
    """Walk forward once and emit signal metadata only (no option simulation).

    Returns each signal as {idx_5m, ts_ms, close, side, side_label, score,
    regime, adx, mtf_aligned, accelerating}. Caller does the option simulation
    separately so we can sweep TP/SL cheaply over the same signal set.
    """
    signals: list[dict] = []
    last_signal_idx = -10_000
    warmup = 60
    for idx, (ts_end, close, s5, s15, s1h) in enumerate(_walk(klines_5m, klines_15m, klines_1h)):
        if idx < warmup or len(s5) < 50 or len(s15) < 50 or len(s1h) < 50:
            continue
        if idx - last_signal_idx < cooldown_bars:
            continue
        tf_5m = analyze_tf(s5)
        tf_15m = analyze_tf(s15)
        tf_1h = analyze_tf(s1h)
        mtf = consensus(tf_5m, tf_15m, tf_1h)
        regime = detect_regime(s1h)

        for side, side_label in (("C", "Call"), ("P", "Put")):
            if not _is_signal(side, mtf, regime, min_alignment):
                continue
            score = _score_signal(mtf, regime)
            if fade:
                side = "P" if side == "C" else "C"
                side_label = "Put" if side_label == "Call" else "Call"
            signals.append({
                "idx_5m": idx,
                "ts_ms": ts_end,
                "close": close,
                "side": side,
                "side_label": side_label,
                "score": score,
                "regime": regime.get("regime"),
                "adx": regime.get("adx"),
                "mtf_aligned": mtf["tfs_aligned"],
                "accelerating": mtf.get("accelerating"),
            })
            last_signal_idx = idx
            break
    return signals


def simulate_signal_set(
    signals: list[dict],
    klines_5m: list[dict],
    sigma: float,
    expiry_hours: float,
    tp1_pct: float,
    tp2_pct: float,
    sl_pct: float,
    option_horizon_h: float,
    horizons_h: tuple[int, ...] = (1, 4, 12),
    spread_pct: float = 0.0,
    tsl_trigger_pct: float = 0.0,
    tsl_offset_pct: float = 0.0,
    adaptive_side_lookback_bars_5m: int | None = None,  # if set, 7d=2016 etc.
    adaptive_side_threshold_pct: float = 1.5,           # only Put if past return < -X%, etc.
    klines_1h: list[dict] | None = None,                # required if dynamic_sigma=True
    dynamic_sigma: bool = False,                        # σ_t from 168h RV × multiplier
    iv_rv_multiplier: float = 1.05,                     # calibrated 2026-05: live IV / RV_168h ≈ 1.03
    sigma_clamp: tuple[float, float] = (0.20, 1.50),    # safety bounds
    strike_round_to: float = 25.0,                      # listed strike spacing (asset-specific)
) -> list[dict]:
    out = []
    skipped_adaptive = 0
    n_dyn_sigma_used = 0
    n_dyn_sigma_missing = 0
    sigmas_used: list[float] = []

    # Pre-build a (ts_ms_open → idx_1h) map if we'll need σ_t per-signal lookup.
    ts_to_idx_1h: dict[int, int] = {}
    closes_1h: list[float] = []
    if dynamic_sigma and klines_1h:
        ts_to_idx_1h = {int(k["start_ms"]): i for i, k in enumerate(klines_1h)}
        closes_1h = [k["close"] for k in klines_1h]

    for sig in signals:
        idx = sig["idx_5m"]
        future = klines_5m[idx + 1:]

        # Time-varying σ from realized vol of past 168h, calibrated to ATM IV.
        # Replaces the constant σ=0.6 backtest assumption, which inflates premium
        # by ~50% vs current Bybit ETH weekly IV (~0.40).
        sigma_t = sigma
        if dynamic_sigma and closes_1h:
            from services.indicators import realized_vol_at_idx_1h
            sig_ts = int(sig["ts_ms"])
            # Find the 1h bar whose CLOSE timestamp is ≤ sig_ts (we ended the 5m
            # signal candle at sig_ts; use the latest fully-closed 1h bar at or
            # before that moment).
            # 1h bar i covers [start_ms, start_ms+3600s); it's "closed" once we
            # pass start_ms + 3600s. So find latest i s.t. start_ms+3600_000 ≤ sig_ts.
            # Simpler: binary search by ts_ms_open. But we keep it linear cheap:
            i_1h = -1
            # 1h kline start is aligned to the hour; sig_ts is end of a 5m bar.
            # The 1h bar containing sig_ts has start = (sig_ts // 3600_000) * 3600_000.
            # We want the bar BEFORE that to be fully closed.
            hour_start = (sig_ts // 3_600_000) * 3_600_000
            i_1h = ts_to_idx_1h.get(hour_start, -1)
            if i_1h <= 0:
                # Fallback — find by linear scan (rare).
                for j, k in enumerate(klines_1h):
                    if int(k["start_ms"]) > sig_ts:
                        i_1h = j - 1
                        break
            if i_1h >= 168:
                rv168 = realized_vol_at_idx_1h(closes_1h, i_1h, lookback_h=168)
                if rv168 is not None:
                    s = rv168 * iv_rv_multiplier
                    s = max(sigma_clamp[0], min(sigma_clamp[1], s))
                    sigma_t = s
                    sigmas_used.append(s)
                    n_dyn_sigma_used += 1
                else:
                    n_dyn_sigma_missing += 1
            else:
                n_dyn_sigma_missing += 1

        # Adaptive side filter: only trade in direction of recent underlying trend
        if adaptive_side_lookback_bars_5m and idx >= adaptive_side_lookback_bars_5m:
            past_close = klines_5m[idx - adaptive_side_lookback_bars_5m]["close"]
            current_close = sig["close"]
            ret_pct = (current_close - past_close) / past_close * 100
            # Trend down → only allow Put (P). Trend up → only allow Call (C). Flat → both.
            if ret_pct < -adaptive_side_threshold_pct and sig["side"] != "P":
                skipped_adaptive += 1
                continue
            if ret_pct > adaptive_side_threshold_pct and sig["side"] != "C":
                skipped_adaptive += 1
                continue
        urets: dict[str, float] = {}
        for h in horizons_h:
            bars_needed = h * 12
            if len(future) < bars_needed:
                continue
            end_close = future[bars_needed - 1]["close"]
            ret_pct = (end_close - sig["close"]) / sig["close"] * 100
            if sig["side"] == "P":
                ret_pct = -ret_pct
            urets[f"{h}h"] = round(ret_pct, 3)
        option = _simulate_option_trade(
            side=sig["side"], entry_spot=sig["close"], sigma=sigma_t,
            expiry_hours=expiry_hours, bars_5m_forward=future,
            tp1_pct=tp1_pct, tp2_pct=tp2_pct, sl_pct=sl_pct,
            horizon_hours=option_horizon_h, spread_pct=spread_pct,
            tsl_trigger_pct=tsl_trigger_pct, tsl_offset_pct=tsl_offset_pct,
            position=sig.get("position", "long_premium"),
            strike_round_to=strike_round_to,
        )
        out.append({**sig, "underlying_returns": urets, "option": option,
                    "side": sig["side"], "sigma_used": round(sigma_t, 4),
                    "ts_iso": datetime.fromtimestamp(sig["ts_ms"] / 1000, tz=timezone.utc).isoformat()})
    if skipped_adaptive:
        print(f"[simulate] adaptive_side skipped {skipped_adaptive} signals", flush=True)
    if dynamic_sigma:
        if sigmas_used:
            avg_s = sum(sigmas_used) / len(sigmas_used)
            mn, mx = min(sigmas_used), max(sigmas_used)
            print(f"[simulate] dynamic σ used: n={n_dyn_sigma_used} missing={n_dyn_sigma_missing} "
                  f"avg={avg_s:.3f} min={mn:.3f} max={mx:.3f}", flush=True)
        else:
            print(f"[simulate] dynamic σ requested but ALL fell back to constant σ={sigma} "
                  f"(klines_1h missing or too short)", flush=True)
    return out


def run(
    symbol: str = "ETHUSDT",
    days: int = 60,
    horizons_h: tuple[int, ...] = (1, 4, 12),
    sigma: float = 0.60,
    expiry_hours: float = 24.0,
    min_alignment: int = 2,
    cooldown_bars: int = 24,
    tp1_pct: float = 0.30,
    tp2_pct: float = 0.80,
    sl_pct: float = 0.35,
    option_horizon_h: float = 12.0,
    fade: bool = False,
    spread_pct: float = 0.0,
    side_filter: str | None = None,           # 'C', 'P', or None
    regime_filter: str | None = None,         # 'trend' / 'transition' / 'range'
    min_atr_15m: float | None = None,         # only trade when ATR(15m) >= X
    hour_from: int | None = None,             # UTC hour, inclusive
    hour_to: int | None = None,               # UTC hour, exclusive
    bias_24h_trend_pct: float | None = None,  # require trade side aligned with 24h trend ≥ X%
) -> dict:
    print(f"[backtest] symbol={symbol} days={days} sigma={sigma} expiry_h={expiry_hours}", flush=True)
    data = fetch_set(symbol, days=days, intervals=("5", "15", "60"))
    klines_5m = data["5"]
    klines_15m = data["15"]
    klines_1h = data["60"]
    if not klines_5m:
        return {"error": "no klines fetched"}

    # Total iteration count: skip initial warmup (need ~60+ candles for 1h EMA)
    warmup = 60
    total = max(0, len(klines_5m) - warmup)
    print(f"[backtest] walking forward over {total} 5m bars (after {warmup}-bar warmup)...", flush=True)

    signals: list[dict] = []
    last_signal_idx = -10_000
    t_start = time.time()

    for idx, (ts_end, close, s5, s15, s1h) in enumerate(_walk(klines_5m, klines_15m, klines_1h)):
        if idx < warmup:
            continue
        if idx % 2000 == 0 and idx > warmup:
            print(f"[backtest]   {idx}/{len(klines_5m)} bars ({len(signals)} signals so far)...", flush=True)
        if len(s5) < 50 or len(s15) < 50 or len(s1h) < 50:
            continue
        if idx - last_signal_idx < cooldown_bars:
            continue

        tf_5m = analyze_tf(s5)
        tf_15m = analyze_tf(s15)
        tf_1h = analyze_tf(s1h)
        mtf = consensus(tf_5m, tf_15m, tf_1h)
        regime = detect_regime(s1h)

        # Regime filter
        if regime_filter and regime.get("regime") != regime_filter:
            continue

        # ATR(15m) filter
        atr_15m = atr(s15, 14) if len(s15) >= 16 else None
        if min_atr_15m is not None and (atr_15m is None or atr_15m < min_atr_15m):
            continue

        # Hour-of-day filter (UTC)
        if hour_from is not None and hour_to is not None:
            from datetime import datetime as _dt, timezone as _tz
            h = _dt.fromtimestamp(ts_end / 1000, tz=_tz.utc).hour
            if not (hour_from <= h < hour_to):
                continue

        # Try both sides
        for side, side_label in (("C", "Call"), ("P", "Put")):
            if not _is_signal(side, mtf, regime, min_alignment):
                continue

            score = _score_signal(mtf, regime)

            # Fade mode: invert the trade direction (signal says up → buy Put).
            if fade:
                side = "P" if side == "C" else "C"
                side_label = "Put" if side_label == "Call" else "Call"

            if side_filter and side != side_filter:
                continue

            # 24h-trend bias: only allow side aligned with dominant direction.
            # Put pays when down → for downtrend (24h_ret < -X%) only allow P.
            # Call pays when up → for uptrend only allow C. Flat: both.
            if bias_24h_trend_pct is not None and len(s1h) >= 25:
                ret_24h = (s1h[-1]["close"] - s1h[-25]["close"]) / s1h[-25]["close"] * 100
                if ret_24h < -bias_24h_trend_pct and side != "P":
                    continue
                if ret_24h > bias_24h_trend_pct and side != "C":
                    continue

            # Realized underlying return at horizons (no look-ahead — already past idx)
            future = klines_5m[idx + 1:]
            returns_h: dict[str, float] = {}
            for h in horizons_h:
                bars_needed = h * 12  # 12 × 5m bars per hour
                if len(future) < bars_needed:
                    continue
                end_close = future[bars_needed - 1]["close"]
                ret_pct = (end_close - close) / close * 100
                if side == "P":
                    ret_pct = -ret_pct  # for Put, gain = downside move
                returns_h[f"{h}h"] = round(ret_pct, 3)

            # Synthetic option simulation
            option = _simulate_option_trade(
                side=side,
                entry_spot=close,
                sigma=sigma,
                expiry_hours=expiry_hours,
                bars_5m_forward=future,
                tp1_pct=tp1_pct, tp2_pct=tp2_pct, sl_pct=sl_pct,
                horizon_hours=option_horizon_h,
                spread_pct=spread_pct,
            )

            signals.append({
                "ts_ms": ts_end,
                "ts_iso": datetime.fromtimestamp(ts_end / 1000, tz=timezone.utc).isoformat(),
                "side": side_label,
                "score": score,
                "regime": regime.get("regime"),
                "adx": regime.get("adx"),
                "mtf_aligned": mtf["tfs_aligned"],
                "accelerating": mtf.get("accelerating"),
                "underlying_returns": returns_h,
                "option": option,
            })
            last_signal_idx = idx
            break  # don't generate both sides on same bar

    elapsed = round(time.time() - t_start, 1)
    print(f"[backtest] done in {elapsed}s, {len(signals)} signals", flush=True)

    return {
        "params": {
            "symbol": symbol, "days": days, "sigma": sigma,
            "expiry_hours": expiry_hours, "min_alignment": min_alignment,
            "cooldown_bars": cooldown_bars,
            "tp1_pct": tp1_pct, "tp2_pct": tp2_pct, "sl_pct": sl_pct,
            "option_horizon_h": option_horizon_h, "horizons_h": list(horizons_h),
        },
        "period": {
            "from_ms": klines_5m[0]["start_ms"] if klines_5m else None,
            "to_ms": klines_5m[-1]["start_ms"] if klines_5m else None,
            "from_iso": datetime.fromtimestamp(klines_5m[0]["start_ms"] / 1000, tz=timezone.utc).isoformat() if klines_5m else None,
            "to_iso": datetime.fromtimestamp(klines_5m[-1]["start_ms"] / 1000, tz=timezone.utc).isoformat() if klines_5m else None,
        },
        "total_bars": len(klines_5m),
        "signals_count": len(signals),
        "elapsed_s": elapsed,
        "summary": _summarize(signals, horizons_h),
        "signals": signals,
    }


def _summarize(signals: list[dict], horizons_h: tuple[int, ...]) -> dict:
    if not signals:
        return {"empty": True}

    def _bucket_stats(items: list[dict]) -> dict:
        if not items:
            return {"count": 0}
        s: dict = {"count": len(items)}
        # Underlying win rates by horizon
        for h in horizons_h:
            key = f"{h}h"
            rets = [it["underlying_returns"].get(key) for it in items if key in it["underlying_returns"]]
            rets = [r for r in rets if r is not None]
            if rets:
                s[f"underlying_{key}_win_rate"] = round(sum(1 for r in rets if r > 0) / len(rets), 3)
                s[f"underlying_{key}_avg_pct"] = round(mean(rets), 3)
                s[f"underlying_{key}_median_pct"] = round(median(rets), 3)
        # Option resolution mix
        res_counter: dict[str, int] = defaultdict(int)
        opt_pnls: list[float] = []
        for it in items:
            opt = it.get("option", {})
            res_counter[opt.get("resolution", "?")] += 1
            if "pnl_pct" in opt:
                opt_pnls.append(opt["pnl_pct"])
        s["option_resolution_pct"] = {k: round(v / len(items) * 100, 1) for k, v in res_counter.items()}
        if opt_pnls:
            s["option_avg_pnl_pct"] = round(mean(opt_pnls), 2)
            s["option_median_pnl_pct"] = round(median(opt_pnls), 2)
            s["option_win_rate"] = round(sum(1 for p in opt_pnls if p > 0) / len(opt_pnls), 3)
        return s

    # Overall + by score bucket + by side + by regime
    summary: dict = {"overall": _bucket_stats(signals)}

    buckets = [("score_4-6", lambda x: 4 <= x["score"] < 7),
               ("score_7-8", lambda x: 7 <= x["score"] < 9),
               ("score_9-10", lambda x: x["score"] >= 9)]
    summary["by_score"] = {name: _bucket_stats([s for s in signals if pred(s)]) for name, pred in buckets}
    summary["by_side"] = {
        "Call": _bucket_stats([s for s in signals if s["side"] == "Call"]),
        "Put":  _bucket_stats([s for s in signals if s["side"] == "Put"]),
    }
    summary["by_regime"] = {
        r: _bucket_stats([s for s in signals if s.get("regime") == r])
        for r in ("trend", "transition", "range", "unknown")
    }
    summary["by_mtf_alignment"] = {
        str(a): _bucket_stats([s for s in signals if s.get("mtf_aligned") == a])
        for a in (2, 3)
    }
    return summary


def print_report(result: dict) -> None:
    """Console-friendly summary."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return
    print("=" * 80)
    print("BACKTEST REPORT")
    print("=" * 80)
    p = result["params"]
    print(f"Symbol: {p['symbol']} | Period: {result['period']['from_iso']} → {result['period']['to_iso']}")
    print(f"Days: {p['days']} | sigma={p['sigma']} | expiry={p['expiry_hours']}h | bars={result['total_bars']}")
    print(f"Signals: {result['signals_count']} | Walk time: {result['elapsed_s']}s")
    print()

    s = result["summary"]
    if s.get("empty"):
        print("No signals generated.")
        return

    def _row(name: str, stats: dict, horizons):
        if not stats or stats.get("count", 0) == 0:
            return
        parts = [f"{name:<20}", f"n={stats['count']:>5}"]
        for h in horizons:
            wr = stats.get(f"underlying_{h}h_win_rate")
            avg = stats.get(f"underlying_{h}h_avg_pct")
            if wr is not None:
                parts.append(f"{h}h WR {wr*100:>5.1f}% avg{avg:+6.2f}%")
        opt_wr = stats.get("option_win_rate")
        opt_avg = stats.get("option_avg_pnl_pct")
        if opt_wr is not None:
            parts.append(f"OPT WR {opt_wr*100:>5.1f}% avg{opt_avg:+7.2f}%")
        print(" | ".join(parts))

    horizons = p["horizons_h"]
    print(f"-- Overall --")
    _row("ALL", s["overall"], horizons)
    print(f"-- By score --")
    for k, v in s["by_score"].items():
        _row(k, v, horizons)
    print(f"-- By side --")
    for k, v in s["by_side"].items():
        _row(k, v, horizons)
    print(f"-- By regime --")
    for k, v in s["by_regime"].items():
        _row(k, v, horizons)
    print(f"-- By MTF alignment --")
    for k, v in s["by_mtf_alignment"].items():
        _row(f"aligned={k}/3", v, horizons)
    print()
    print("Option resolution mix (overall):")
    for k, v in s["overall"].get("option_resolution_pct", {}).items():
        print(f"  {k:<20} {v}%")


def sweep_params(
    symbol: str = "ETHUSDT",
    days: int = 60,
    sigma: float = 0.60,
    expiry_hours: float = 168.0,
    fade: bool = True,
    option_horizon_h: float = 12.0,
    min_alignment: int = 2,
    cooldown_bars: int = 24,
    tp1_grid: tuple[float, ...] = (0.15, 0.20, 0.25, 0.30),
    tp2_grid: tuple[float, ...] = (0.40, 0.55, 0.70, 0.90),
    sl_grid: tuple[float, ...] = (0.15, 0.20, 0.25, 0.30, 0.35),
) -> dict:
    """Parameter sweep on TP1/TP2/SL. Walks the kline data once, then re-simulates."""
    from itertools import product

    print(f"[sweep] fetching klines for {symbol} {days}d...", flush=True)
    data = fetch_set(symbol, days=days, intervals=("5", "15", "60"))
    klines_5m, klines_15m, klines_1h = data["5"], data["15"], data["60"]
    if not klines_5m:
        return {"error": "no klines"}

    print(f"[sweep] generating raw signals (fade={fade})...", flush=True)
    signals = generate_raw_signals(klines_5m, klines_15m, klines_1h, min_alignment, cooldown_bars, fade)
    print(f"[sweep] {len(signals)} raw signals. running {len(tp1_grid)*len(tp2_grid)*len(sl_grid)} combos...", flush=True)

    results: list[dict] = []
    for tp1, tp2, sl in product(tp1_grid, tp2_grid, sl_grid):
        if tp2 <= tp1:
            continue
        sims = simulate_signal_set(signals, klines_5m, sigma, expiry_hours, tp1, tp2, sl, option_horizon_h)
        pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s["option"]]
        if not pnls:
            continue
        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        results.append({
            "tp1": tp1, "tp2": tp2, "sl": sl,
            "n": len(pnls),
            "wr": round(wr, 3),
            "avg_pnl": round(mean(pnls), 2),
            "median_pnl": round(median(pnls), 2),
            "total_pnl": round(sum(pnls), 1),
        })

    results.sort(key=lambda r: r["avg_pnl"], reverse=True)
    return {
        "fade": fade,
        "sigma": sigma,
        "expiry_hours": expiry_hours,
        "option_horizon_h": option_horizon_h,
        "signals_count": len(signals),
        "results": results,
    }


def print_sweep_report(sweep: dict) -> None:
    if "error" in sweep:
        print(f"ERROR: {sweep['error']}")
        return
    print("=" * 80)
    print(f"TP/SL SWEEP (fade={sweep['fade']}, sigma={sweep['sigma']}, expiry={sweep['expiry_hours']}h, signals={sweep['signals_count']})")
    print("=" * 80)
    print(f"{'TP1':>6} {'TP2':>6} {'SL':>6} {'WR':>7} {'avg %':>8} {'med %':>8} {'total %':>9}")
    for r in sweep["results"][:25]:
        print(f"{r['tp1']:>6.2f} {r['tp2']:>6.2f} {r['sl']:>6.2f} {r['wr']*100:>6.1f}% {r['avg_pnl']:>8.2f} {r['median_pnl']:>8.2f} {r['total_pnl']:>9.1f}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--sigma", type=float, default=0.60, help="Constant annualized IV")
    parser.add_argument("--expiry-hours", type=float, default=24.0)
    parser.add_argument("--min-alignment", type=int, default=2)
    parser.add_argument("--cooldown-bars", type=int, default=24)
    parser.add_argument("--out", default="/tmp/backtest_result.json")
    parser.add_argument("--fade", action="store_true", help="Invert direction (mean-reversion test)")
    parser.add_argument("--sweep", action="store_true", help="Run TP/SL parameter sweep instead of single backtest")
    parser.add_argument("--option-horizon-h", type=float, default=12.0)
    parser.add_argument("--spread-pct", type=float, default=0.0, help="Round-trip spread % (realistic friction)")
    parser.add_argument("--side", choices=["C", "P"], default=None, help="Only trade this side")
    parser.add_argument("--tp1", type=float, default=0.30)
    parser.add_argument("--tp2", type=float, default=0.80)
    parser.add_argument("--sl", type=float, default=0.35)
    parser.add_argument("--regime", choices=["trend", "transition", "range"], default=None)
    parser.add_argument("--min-atr-15m", type=float, default=None)
    parser.add_argument("--hour-from", type=int, default=None)
    parser.add_argument("--hour-to", type=int, default=None)
    parser.add_argument("--bias-24h-trend-pct", type=float, default=None,
                        help="Trade only side aligned with 24h trend >= X%")
    args = parser.parse_args()

    if args.sweep:
        sw = sweep_params(
            symbol=args.symbol, days=args.days, sigma=args.sigma,
            expiry_hours=args.expiry_hours, fade=args.fade,
            option_horizon_h=args.option_horizon_h,
            min_alignment=args.min_alignment, cooldown_bars=args.cooldown_bars,
        )
        with open(args.out, "w") as f:
            json.dump(sw, f, indent=2)
        print_sweep_report(sw)
        sys.exit(0)

    result = run(
        symbol=args.symbol,
        days=args.days,
        sigma=args.sigma,
        expiry_hours=args.expiry_hours,
        min_alignment=args.min_alignment,
        cooldown_bars=args.cooldown_bars,
        fade=args.fade,
        spread_pct=args.spread_pct,
        side_filter=args.side,
        regime_filter=args.regime,
        min_atr_15m=args.min_atr_15m,
        hour_from=args.hour_from,
        hour_to=args.hour_to,
        bias_24h_trend_pct=args.bias_24h_trend_pct,
        tp1_pct=args.tp1, tp2_pct=args.tp2, sl_pct=args.sl,
    )
    with open(args.out, "w") as f:
        # signals[] can be huge — write trimmed copy
        trimmed = {**result, "signals": result["signals"][:200]}
        json.dump(trimmed, f, indent=2)
    print(f"\nFull result trimmed-saved to {args.out} (first 200 signals)")
    print()
    print_report(result)

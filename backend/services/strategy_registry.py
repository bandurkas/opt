"""Strategy registry — each strategy is a signal generator function with config.

A signal is a dict: {idx_5m, ts_ms, close, side, signal_type, score, position, regime?}
  - position = 'long_premium' (buy option) or 'short_premium' (sell option)
  - side = 'C' or 'P'

The simulation engine treats long/short differently:
  - long_premium: profit if premium goes UP (existing simulation)
  - short_premium: profit if premium goes DOWN (we receive credit at entry, pay debit at exit)
"""
from __future__ import annotations

from typing import Callable, Iterator

from .indicators import adx, atr, bollinger, donchian, ema, realized_vol, rsi, zscore
from .momentum_mtf import analyze_tf, consensus
from .regime import detect_regime


SignalIter = Iterator[dict]


def _emit(idx, ts, close, side, sig_type, score, position="long_premium", **extra) -> dict:
    return {
        "idx_5m": idx, "ts_ms": ts, "close": close, "side": side,
        "signal_type": sig_type, "score": float(score),
        "position": position, **extra,
    }


def _walk_iter(k5, k15, k1h, history_window: int = 240):
    """Same iteration shape as backtest._walk but accepts variable history window."""
    i15 = 0
    i1h = 0
    for i, c5 in enumerate(k5):
        ts_end = c5["start_ms"] + 5 * 60 * 1000
        while i15 < len(k15) and k15[i15]["start_ms"] + 15 * 60 * 1000 <= ts_end:
            i15 += 1
        while i1h < len(k1h) and k1h[i1h]["start_ms"] + 60 * 60 * 1000 <= ts_end:
            i1h += 1
        s5 = k5[max(0, i + 1 - history_window):i + 1]
        s15 = k15[max(0, i15 - history_window):i15]
        s1h = k1h[max(0, i1h - history_window):i1h]
        yield i, ts_end, c5["close"], s5, s15, s1h


# ───────────────────────── STRATEGY 1: MTF Fade ─────────────────────────

def gen_mtf_fade(k5, k15, k1h, *, min_alignment: int = 2, cooldown_bars: int = 12,
                 warmup: int = 60) -> list[dict]:
    """Buy Call when MTF says DOWN (fade dip), buy Put when MTF says UP (fade rally)."""
    out: list[dict] = []
    last_idx = -10_000
    for i, ts, close, s5, s15, s1h in _walk_iter(k5, k15, k1h):
        if i < warmup or len(s5) < 50 or len(s15) < 50 or len(s1h) < 50:
            continue
        if i - last_idx < cooldown_bars:
            continue
        mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
        if mtf["direction"] == "up" and mtf["tfs_aligned"] >= min_alignment:
            out.append(_emit(i, ts, close, "P", "mtf_fade", 5.0))
            last_idx = i
        elif mtf["direction"] == "down" and mtf["tfs_aligned"] >= min_alignment:
            out.append(_emit(i, ts, close, "C", "mtf_fade", 5.0))
            last_idx = i
    return out


# ───────────────────────── STRATEGY 2: MTF Continuation ─────────────────────────

def gen_mtf_continuation(k5, k15, k1h, *, min_alignment: int = 2, cooldown_bars: int = 12,
                         warmup: int = 60) -> list[dict]:
    """Trade WITH the MTF direction."""
    out: list[dict] = []
    last_idx = -10_000
    for i, ts, close, s5, s15, s1h in _walk_iter(k5, k15, k1h):
        if i < warmup or len(s5) < 50 or len(s15) < 50 or len(s1h) < 50:
            continue
        if i - last_idx < cooldown_bars:
            continue
        mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
        if mtf["direction"] == "up" and mtf["tfs_aligned"] >= min_alignment:
            out.append(_emit(i, ts, close, "C", "mtf_continuation", 5.0))
            last_idx = i
        elif mtf["direction"] == "down" and mtf["tfs_aligned"] >= min_alignment:
            out.append(_emit(i, ts, close, "P", "mtf_continuation", 5.0))
            last_idx = i
    return out


# ───────────────────────── STRATEGY 3: RSI Mean-Reversion ─────────────────────────

def gen_rsi_extremes(k5, k15, k1h, *, rsi_low: float = 25, rsi_high: float = 75,
                     tf: str = "1h", cooldown_bars: int = 24) -> list[dict]:
    """Buy Call when RSI < rsi_low (oversold). Buy Put when RSI > rsi_high (overbought).
    TF selects which timeframe to use for RSI."""
    out: list[dict] = []
    last_idx = -10_000
    for i, ts, close, s5, s15, s1h in _walk_iter(k5, k15, k1h):
        if i - last_idx < cooldown_bars:
            continue
        ref = {"5m": s5, "15m": s15, "1h": s1h}[tf]
        if len(ref) < 20:
            continue
        closes = [c["close"] for c in ref]
        r = rsi(closes, 14)
        if r is None:
            continue
        if r < rsi_low:
            out.append(_emit(i, ts, close, "C", f"rsi_oversold_{tf}", 6.0, rsi=r))
            last_idx = i
        elif r > rsi_high:
            out.append(_emit(i, ts, close, "P", f"rsi_overbought_{tf}", 6.0, rsi=r))
            last_idx = i
    return out


# ───────────────────────── STRATEGY 4: Bollinger Band Reversion ─────────────────────────

def gen_bb_reversion(k5, k15, k1h, *, tf: str = "1h", bb_period: int = 20, bb_k: float = 2.0,
                     cooldown_bars: int = 24) -> list[dict]:
    """Price touches/breaks lower BB → buy Call (reversal up).
    Price touches/breaks upper BB → buy Put (reversal down)."""
    out: list[dict] = []
    last_idx = -10_000
    for i, ts, close, s5, s15, s1h in _walk_iter(k5, k15, k1h):
        if i - last_idx < cooldown_bars:
            continue
        ref = {"5m": s5, "15m": s15, "1h": s1h}[tf]
        if len(ref) < bb_period:
            continue
        closes = [c["close"] for c in ref]
        lower, mid, upper = bollinger(closes, bb_period, bb_k)
        if lower is None or upper is None:
            continue
        if close <= lower:
            out.append(_emit(i, ts, close, "C", f"bb_lower_{tf}", 6.0))
            last_idx = i
        elif close >= upper:
            out.append(_emit(i, ts, close, "P", f"bb_upper_{tf}", 6.0))
            last_idx = i
    return out


# ───────────────────────── STRATEGY 5: Donchian Breakout ─────────────────────────

def gen_donchian_breakout(k5, k15, k1h, *, tf: str = "1h", period: int = 20,
                          cooldown_bars: int = 24) -> list[dict]:
    """New 20-bar high → buy Call (momentum). New 20-bar low → buy Put."""
    out: list[dict] = []
    last_idx = -10_000
    for i, ts, close, s5, s15, s1h in _walk_iter(k5, k15, k1h):
        if i - last_idx < cooldown_bars:
            continue
        ref = {"5m": s5, "15m": s15, "1h": s1h}[tf]
        if len(ref) < period + 1:
            continue
        lo, hi = donchian(ref[:-1], period)  # exclude current bar
        if lo is None or hi is None:
            continue
        if close > hi:
            out.append(_emit(i, ts, close, "C", f"donchian_break_up_{tf}", 6.0))
            last_idx = i
        elif close < lo:
            out.append(_emit(i, ts, close, "P", f"donchian_break_dn_{tf}", 6.0))
            last_idx = i
    return out


# ───────────────────────── STRATEGY 6: Sell Premium (short straddle proxy) ─────────────────────────

def gen_sell_premium_iv_high(k5, k15, k1h, *, vol_lookback_h: int = 168, vol_threshold: float = 0.85,
                              regime_filter: list[str] | None = ("range", "transition"),
                              side: str = "P",
                              adx_max: float | None = None,
                              mtf_direction_filter: str | None = None,
                              bull_market_ratio_max: float | None = None,
                              cooldown_bars: int = 24) -> list[dict]:
    """When realized vol is in TOP (1-vol_threshold) of recent week AND we're in
    non-trend regime, sell ATM option(s). Returns short_premium signals.

    `side`:
      'P' — sell ATM Put only (delta-positive; bullish stance)
      'C' — sell ATM Call only (delta-negative; bearish stance)
      'both' — strangle: emit BOTH P and C at same idx (delta-neutral, double theta)

    `adx_max`: optional hard cap on ADX to enforce a true range regime (e.g. 15).
    `mtf_direction_filter`: 'up' | 'down' | None. When 'up', only emit when MTF
        consensus is bullish (≥2 of 3 TFs aligned up) — pair with side='P' to sell
        puts in uptrend. When 'down', only emit when MTF is bearish — pair with
        side='C' to sell calls in downtrend. None = no directional filter.
    """
    out: list[dict] = []
    last_idx = -10_000
    for i, ts, close, s5, s15, s1h in _walk_iter(k5, k15, k1h):
        if i - last_idx < cooldown_bars:
            continue
        if len(s1h) < vol_lookback_h + 20:
            continue

        closes_1h = [c["close"] for c in s1h]
        rolling_vols = []
        for j in range(20, len(closes_1h)):
            rv = realized_vol(closes_1h[:j + 1], lookback=24)
            if rv is not None:
                rolling_vols.append(rv)
        if len(rolling_vols) < 30:
            continue

        current_vol = rolling_vols[-1]
        sorted_vols = sorted(rolling_vols)
        threshold = sorted_vols[int(len(sorted_vols) * vol_threshold)]
        if current_vol < threshold:
            continue

        reg = detect_regime(s1h)
        if regime_filter and reg.get("regime", "unknown") not in regime_filter:
            continue
        if adx_max is not None and (reg.get("adx") or 999) > adx_max:
            continue

        if mtf_direction_filter is not None:
            if len(s5) < 50 or len(s15) < 50 or len(s1h) < 50:
                continue
            mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
            if mtf["direction"] != mtf_direction_filter or mtf["tfs_aligned"] < 2:
                continue

        # Bull-market kill switch: when EMA50_1h / EMA200_1h > threshold, skip
        # (Designed to disable C-side selling in strong uptrends where calls get crushed.)
        if bull_market_ratio_max is not None:
            closes_1h = [c["close"] for c in s1h]
            if len(closes_1h) < 200:
                continue
            ema50 = ema(closes_1h, 50)
            ema200 = ema(closes_1h, 200)
            if ema50 is None or ema200 is None or ema200 == 0:
                continue
            ratio = ema50 / ema200
            if ratio > bull_market_ratio_max:
                continue

        sides = ["P", "C"] if side == "both" else [side]
        sig_type = "sell_premium_strangle" if side == "both" else "sell_premium_high_vol"
        for s in sides:
            out.append(_emit(i, ts, close, s, sig_type, 6.5, position="short_premium"))
        last_idx = i
    return out


# ───────────────────────── STRATEGY 7: Volume Spike + Direction ─────────────────────────

def gen_volume_spike_continuation(k5, k15, k1h, *, tf: str = "15m", z_threshold: float = 2.5,
                                   cooldown_bars: int = 24) -> list[dict]:
    """Volume z-score >= threshold + price up/down in same bar → trade in direction."""
    out: list[dict] = []
    last_idx = -10_000
    for i, ts, close, s5, s15, s1h in _walk_iter(k5, k15, k1h):
        if i - last_idx < cooldown_bars:
            continue
        ref = {"5m": s5, "15m": s15, "1h": s1h}[tf]
        if len(ref) < 25:
            continue
        volumes = [c["volume"] for c in ref]
        vz = zscore(volumes, 20)
        if vz is None or vz < z_threshold:
            continue
        last_bar = ref[-1]
        change = last_bar["close"] - last_bar["open"]
        if change > 0:
            out.append(_emit(i, ts, close, "C", f"vol_spike_up_{tf}", 6.0, vol_z=vz))
            last_idx = i
        elif change < 0:
            out.append(_emit(i, ts, close, "P", f"vol_spike_dn_{tf}", 6.0, vol_z=vz))
            last_idx = i
    return out


# ───────────────────────── STRATEGY 8: EMA Cross ─────────────────────────

def gen_ema_cross(k5, k15, k1h, *, tf: str = "1h", fast: int = 20, slow: int = 50,
                  cooldown_bars: int = 24) -> list[dict]:
    """EMA fast crosses ABOVE slow → Call. Below → Put."""
    out: list[dict] = []
    last_idx = -10_000
    prev_above: bool | None = None
    for i, ts, close, s5, s15, s1h in _walk_iter(k5, k15, k1h):
        ref = {"5m": s5, "15m": s15, "1h": s1h}[tf]
        if len(ref) < slow + 5:
            continue
        closes = [c["close"] for c in ref]
        e_fast = ema(closes, fast)
        e_slow = ema(closes, slow)
        if e_fast is None or e_slow is None:
            continue
        now_above = e_fast > e_slow
        if prev_above is None:
            prev_above = now_above
            continue
        crossed = prev_above != now_above
        prev_above = now_above
        if not crossed:
            continue
        if i - last_idx < cooldown_bars:
            continue
        if now_above:
            out.append(_emit(i, ts, close, "C", f"ema_cross_up_{tf}", 6.0))
        else:
            out.append(_emit(i, ts, close, "P", f"ema_cross_dn_{tf}", 6.0))
        last_idx = i
    return out


# ───────────────────────── REGISTRY ─────────────────────────

REGISTRY: dict[str, Callable] = {
    "mtf_fade": gen_mtf_fade,
    "mtf_continuation": gen_mtf_continuation,
    "rsi_extremes": gen_rsi_extremes,
    "bb_reversion": gen_bb_reversion,
    "donchian_breakout": gen_donchian_breakout,
    "sell_premium_high_vol": gen_sell_premium_iv_high,
    "volume_spike_continuation": gen_volume_spike_continuation,
    "ema_cross": gen_ema_cross,
}

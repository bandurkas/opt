"""How long since the strategy last produced a signal.

Lightweight diagnostic so the UI can answer "is paper quiet because of a bug,
or because the market just has no setup right now?" — by showing the age of
the most recent generator-fired signal (whether or not paper actually opened it).

Reads the same 600-bar window paper_loop uses, runs gen_sell_premium_iv_high
with the configured kwargs, and reports the timestamp / age of the newest
signal. Cached for 30s (paper-loop only ticks once per minute anyway).
"""
from __future__ import annotations

import time

from db.repository import recent_klines
from services.strategy_config import active_gen_kwargs
from services.strategy_registry import gen_sell_premium_iv_high

_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_S = 30
_WINDOW_5M = 600


def compute_freshness(symbol: str = "ETHUSDT") -> dict:
    now = time.time()
    cached = _CACHE.get(symbol)
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1]

    k5 = recent_klines(symbol, "5m", _WINDOW_5M)
    k15 = recent_klines(symbol, "15m", _WINDOW_5M // 3 + 20)
    k1h = recent_klines(symbol, "1h", _WINDOW_5M // 12 + 20)

    if not k5 or len(k5) < 60:
        out = {
            "last_signal_ts_ms": None,
            "last_signal_age_h": None,
            "bars_since_last_signal_5m": None,
            "signals_24h": 0,
            "window_5m_bars": len(k5) if k5 else 0,
        }
        _CACHE[symbol] = (now, out)
        return out

    sigs = gen_sell_premium_iv_high(k5, k15, k1h, **active_gen_kwargs())
    now_ms = int(now * 1000)
    day_ago = now_ms - 24 * 3600 * 1000

    if sigs:
        last = sigs[-1]
        last_ts = int(last["ts_ms"])
        last_idx = int(last.get("idx_5m", -1))
        bars_since = (len(k5) - 1 - last_idx) if last_idx >= 0 else None
        age_h = round((now_ms - last_ts) / 3_600_000, 2)
    else:
        last_ts = None
        bars_since = None
        age_h = None

    out = {
        "last_signal_ts_ms": last_ts,
        "last_signal_age_h": age_h,
        "bars_since_last_signal_5m": bars_since,
        "signals_24h": sum(1 for s in sigs if s.get("ts_ms", 0) >= day_ago),
        "window_5m_bars": len(k5),
    }
    _CACHE[symbol] = (now, out)
    return out

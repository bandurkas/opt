"""Compute the trades paper_loop SHOULD have opened (but missed due to bug).

Replays the validated sell-premium generator on recent DB klines and simulates
each signal through the same exit/sizing/CB rules used by the live paper
service. Returns per-trade outcomes + equity curve so the frontend can show
'this is what the bug cost us'.

Synthetic pricing: BS with sigma=0.6 and 2% round-trip spread (same friction
as the validation backtest). Real Bybit historical option premiums are not
available, so this is a model approximation — should be within ±20% of real.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from db.repository import recent_klines
from services.backtest import simulate_signal_set
from services.paper_strategy import (
    DEFAULT_SIGMA,
    EXPIRY_TARGET_HOURS,
    SIZE_MAX_USD,
    SIZE_MIN_USD,
    SIZE_PCT_OF_EQUITY,
    START_EQUITY_USD,
    WINNER_EXIT,
    WINNER_GEN_KWARGS,
)
from services.strategy_registry import gen_sell_premium_iv_high


SPREAD_PCT = 2.0
STRIKE_GRID = 25.0

# Module-level cache. compute is ~2 minutes due to O(N^2) realized_vol;
# we cache so the HTTP endpoint returns instantly after the first warm-up.
_CACHE: dict[int, tuple[float, dict]] = {}
_CACHE_TTL_S = 600  # 10 min


def _size_usd(equity: float, recent_pnls: list[float]) -> float:
    """Replicate paper_strategy.current_size_usd exactly."""
    base = equity * SIZE_PCT_OF_EQUITY
    if len(recent_pnls) >= 10:
        wr = sum(1 for p in recent_pnls[-10:] if p > 0) / 10.0
        if wr < 0.40:
            base *= 0.5
    return max(SIZE_MIN_USD, min(SIZE_MAX_USD, base))


def compute_missed_signals(lookback_days: int = 14, force_refresh: bool = False) -> dict:
    """Generate signals from DB klines + simulate each as if paper had opened it.

    Returns a fully-formed report dict with per-trade list + summary stats +
    equity curve points (one per closed trade). Cached for 10 min per lookback_days.
    """
    if not force_refresh:
        cached = _CACHE.get(lookback_days)
        if cached:
            ts_age, payload = cached
            if time.time() - ts_age < _CACHE_TTL_S:
                return {**payload, "cached": True, "cache_age_s": int(time.time() - ts_age)}

    bars_needed_5m = max(5000, lookback_days * 24 * 12 + 600)
    k5 = recent_klines("ETHUSDT", "5m", bars_needed_5m)
    k15 = recent_klines("ETHUSDT", "15m", bars_needed_5m // 3 + 100)
    k1h = recent_klines("ETHUSDT", "1h", bars_needed_5m // 12 + 220)

    if not k5 or len(k5) < 600:
        return {"error": "not enough klines", "n_5m_bars": len(k5) if k5 else 0}

    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - lookback_days * 86_400_000

    raw_signals = gen_sell_premium_iv_high(k5, k15, k1h, **WINNER_GEN_KWARGS)
    raw_signals = [s for s in raw_signals if s["ts_ms"] >= cutoff_ms]
    raw_signals.sort(key=lambda s: s["ts_ms"])

    if not raw_signals:
        return {
            "lookback_days": lookback_days,
            "generated_at_ms": now_ms,
            "n_signals": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "total_pnl_usd": 0.0,
            "total_pnl_pct": 0.0,
            "start_equity_usd": float(START_EQUITY_USD),
            "final_equity_usd": float(START_EQUITY_USD),
            "avg_pnl_pct_per_trade": 0.0,
            "n_skipped_by_cb": 0,
            "resolution_counts": {},
            "trades": [],
            "equity_curve": [{"ts_ms": now_ms, "equity_usd": float(START_EQUITY_USD), "label": "start"}],
            "pricing_note": (
                "BS pricing with sigma=0.6 and 2% spread (same as backtest). Real "
                "Bybit historical option prices not available; expect approx ±20% accuracy."
            ),
        }

    # Simulate every signal with the live exit rules
    sims = simulate_signal_set(
        raw_signals, k5,
        sigma=DEFAULT_SIGMA, expiry_hours=EXPIRY_TARGET_HOURS,
        tp1_pct=WINNER_EXIT["tp1_pct"],
        tp2_pct=WINNER_EXIT["tp2_pct"],
        sl_pct=WINNER_EXIT["sl_pct"],
        option_horizon_h=WINNER_EXIT["hold_h"],
        spread_pct=SPREAD_PCT,
    )

    # Replay paper's state machine (CB + dynsize)
    equity = float(START_EQUITY_USD)
    consec_losses = 0
    cb_until_ms = 0
    recent_pnls: list[float] = []
    trades: list[dict] = []
    equity_curve = [{"ts_ms": int(raw_signals[0]["ts_ms"]) - 60_000,
                     "equity_usd": equity, "label": "start"}]
    skipped_cb = 0

    for sim in sims:
        ts = int(sim["ts_ms"])
        opt = sim["option"]
        if "pnl_pct" not in opt:
            continue

        if ts < cb_until_ms:
            skipped_cb += 1
            continue

        size_usd = _size_usd(equity, recent_pnls)
        pnl_pct = float(opt["pnl_pct"])
        pnl_usd = round(size_usd * pnl_pct / 100, 2)

        equity_before = equity
        equity = round(equity + pnl_usd, 2)

        recent_pnls.append(pnl_pct)
        if len(recent_pnls) > 50:
            recent_pnls = recent_pnls[-50:]

        if pnl_pct <= 0:
            consec_losses += 1
            if consec_losses >= 3:
                cb_until_ms = ts + 24 * 3_600_000
                consec_losses = 0
        else:
            consec_losses = 0

        spot = float(sim["close"])
        strike = round(spot / STRIKE_GRID) * STRIKE_GRID

        trades.append({
            "ts_ms": ts,
            "ts_iso": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
            "side": sim["side"],
            "strike": strike,
            "spot_at_entry": round(spot, 2),
            "size_usd": round(size_usd, 2),
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": pnl_usd,
            "exit_reason": opt.get("resolution", "unknown"),
            "bars_held": opt.get("bars_held"),
            "equity_before": round(equity_before, 2),
            "equity_after": equity,
        })

        equity_curve.append({"ts_ms": ts, "equity_usd": equity,
                             "label": "win" if pnl_pct > 0 else "loss"})

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl_usd"] > 0)
    losses = n - wins
    total_pnl_usd = round(equity - START_EQUITY_USD, 2)
    total_pnl_pct = round((equity / START_EQUITY_USD - 1) * 100, 2)
    avg_pnl_pct = round(sum(t["pnl_pct"] for t in trades) / n, 2) if n else 0.0

    res_counts: dict[str, int] = {}
    for t in trades:
        res_counts[t["exit_reason"]] = res_counts.get(t["exit_reason"], 0) + 1

    payload = {
        "lookback_days": lookback_days,
        "generated_at_ms": now_ms,
        "n_signals": n,
        "n_skipped_by_cb": skipped_cb,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / n, 3) if n else None,
        "total_pnl_usd": total_pnl_usd,
        "total_pnl_pct": total_pnl_pct,
        "avg_pnl_pct_per_trade": avg_pnl_pct,
        "start_equity_usd": float(START_EQUITY_USD),
        "final_equity_usd": equity,
        "resolution_counts": res_counts,
        "trades": trades,
        "equity_curve": equity_curve,
        "pricing_note": (
            "BS pricing with sigma=0.6 and 2% spread (same as backtest). Real "
            "Bybit historical option prices not available; expect approx +/-20% accuracy."
        ),
        "cached": False,
        "cache_age_s": 0,
    }
    _CACHE[lookback_days] = (time.time(), payload)
    return payload

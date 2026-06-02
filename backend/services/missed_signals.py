"""Compute the trades paper_loop SHOULD have opened (but missed due to bug).

Replays the validated sell-premium generator on recent DB klines and simulates
each signal through the same EXIT / SIZING / FRICTION rules that the live
paper_loop now uses (Bybit-realistic: 0.1-ETH lots, ~10% IM, 5% round-trip
spread, 0.03% taker fees capped at 12.5% premium).

Pricing remains BS-based since real Bybit historical option chains aren't
available; expect ±20% accuracy vs real fills, but P&L numbers should now
be on the right ORDER OF MAGNITUDE for a $400 starting account.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from db.repository import recent_klines
from services.backtest import simulate_signal_set
from services.hybrid_backtest_v2 import generate_hybrid_v2
from services.paper_strategy import (
    DEFAULT_SIGMA,
    EXPIRY_TARGET_HOURS,
    LOT_MIN_ETH,
    SPREAD_HALF_PCT,
    START_EQUITY_USD,
    fee_per_side,
    margin_per_lot,
    realistic_size_lots,
)
from services.strategy_config import (
    CALL_EXIT,
    CALL_GEN_KWARGS,
    PUT_EXIT,
    PUT_GEN_KWARGS,
    RET_7D_THRESHOLD,
)

STRIKE_GRID = 25.0
SPREAD_PCT_TOTAL = SPREAD_HALF_PCT * 2.0  # round-trip, fed to simulate_signal_set
_T_YEARS = EXPIRY_TARGET_HOURS / (365.0 * 24.0)

_CACHE: dict[int, tuple[float, dict]] = {}
_CACHE_TTL_S = 600


def _bs_call(spot: float, K: float, T_years: float = _T_YEARS,
             sigma: float = DEFAULT_SIGMA) -> float:
    """Black-Scholes call price, no dividend, r=0."""
    if T_years <= 0 or sigma <= 0:
        return max(0.0, spot - K)
    sqrtT = math.sqrt(T_years)
    d1 = (math.log(spot / K) + 0.5 * sigma * sigma * T_years) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    cdf = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2)))
    return spot * cdf(d1) - K * cdf(d2)


def compute_missed_signals(lookback_days: int = 14, force_refresh: bool = False) -> dict:
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

    # V2 hybrid signals: side picked per-bar by ret_7d + MTF filter.
    raw_signals = generate_hybrid_v2(
        k5, k15, k1h,
        put_gen=PUT_GEN_KWARGS, call_gen=CALL_GEN_KWARGS,
        ret_7d_threshold=RET_7D_THRESHOLD,
    )
    raw_signals = [s for s in raw_signals if s["ts_ms"] >= cutoff_ms]
    raw_signals.sort(key=lambda s: s["ts_ms"])

    if not raw_signals:
        return _empty_report(lookback_days, now_ms)

    # Simulate Put and Call signals separately (each side has its own TP/SL/hold).
    put_sigs = [s for s in raw_signals if s["side"] == "P"]
    call_sigs = [s for s in raw_signals if s["side"] == "C"]
    sims = []
    if put_sigs:
        sims.extend(simulate_signal_set(
            put_sigs, k5,
            sigma=DEFAULT_SIGMA, expiry_hours=EXPIRY_TARGET_HOURS,
            tp1_pct=PUT_EXIT["tp1_pct"], tp2_pct=PUT_EXIT["tp2_pct"],
            sl_pct=PUT_EXIT["sl_pct"], option_horizon_h=PUT_EXIT["hold_h"],
            spread_pct=SPREAD_PCT_TOTAL,
        ))
    if call_sigs:
        sims.extend(simulate_signal_set(
            call_sigs, k5,
            sigma=DEFAULT_SIGMA, expiry_hours=EXPIRY_TARGET_HOURS,
            tp1_pct=CALL_EXIT["tp1_pct"], tp2_pct=CALL_EXIT["tp2_pct"],
            sl_pct=CALL_EXIT["sl_pct"], option_horizon_h=CALL_EXIT["hold_h"],
            spread_pct=SPREAD_PCT_TOTAL,
        ))
    sims.sort(key=lambda s: s["ts_ms"])

    # Replay paper's full state machine: CB + dynsize + margin budget + 1-position discipline
    equity = float(START_EQUITY_USD)
    consec_losses = 0
    cb_until_ms = 0
    from services.paper_strategy import MAX_PORTFOLIO_MARGIN_PCT
    active_positions = []  # list of {"exit_ms": int, "margin_locked": float}
    recent_pnls: list[float] = []
    trades: list[dict] = []
    equity_curve = [{"ts_ms": int(raw_signals[0]["ts_ms"]) - 60_000,
                     "equity_usd": equity, "label": "start"}]
    skipped_cb = 0
    skipped_margin = 0
    skipped_busy = 0

    for sim in sims:
        ts = int(sim["ts_ms"])
        opt = sim["option"]
        if "pnl_pct" not in opt:
            continue

        if ts < cb_until_ms:
            skipped_cb += 1
            continue
        # Purge closed positions
        active_positions = [p for p in active_positions if p["exit_ms"] > ts]

        spot = float(sim["close"])
        strike = round(spot / STRIKE_GRID) * STRIKE_GRID
        premium_mid = _bs_call(spot, strike)
        if premium_mid <= 0:
            continue

        locked_margin = sum(p["margin_locked"] for p in active_positions)
        free_margin = (equity * MAX_PORTFOLIO_MARGIN_PCT) - locked_margin
        
        n_lots = realistic_size_lots(
            free_margin, equity, strike, premium_mid,
            {"recent_pnls_json": recent_pnls},
        )
        if n_lots < 1:
            if locked_margin > 0:
                skipped_busy += 1
            else:
                skipped_margin += 1
            continue

        contracts = n_lots * LOT_MIN_ETH
        notional = strike * contracts
        margin_locked = margin_per_lot(strike, premium_mid) * n_lots

        # Spread-haircut premium (matches what paper_loop's open does)
        premium_received_per_contract = premium_mid * (1 - SPREAD_HALF_PCT / 100.0)
        premium_received_total = premium_received_per_contract * contracts

        # P&L: simulate's pnl_pct is post-spread % on premium. Convert to USD,
        # then subtract entry+exit fees.
        model_pnl_pct = float(opt["pnl_pct"])
        gross_pnl_usd = premium_received_total * model_pnl_pct / 100.0

        entry_fee = fee_per_side(notional, premium_received_total)
        # exit notional similar (strike·contracts), premium handled ≈ exit_debit·contracts
        # approximate exit premium ≈ premium_received_total·(1 − model_pnl_pct/100)
        approx_exit_premium = premium_received_total * (1 - model_pnl_pct / 100.0)
        exit_fee = fee_per_side(notional, approx_exit_premium)
        fees_total = entry_fee + exit_fee

        pnl_usd = round(gross_pnl_usd - fees_total, 2)
        # % return on net premium received (matches paper_loop's pnl_pct semantics)
        pnl_pct_net = (pnl_usd / premium_received_total * 100.0) if premium_received_total > 0 else 0

        recent_pnls.append(pnl_pct_net)
        if len(recent_pnls) > 50:
            recent_pnls = recent_pnls[-50:]

        if pnl_pct_net <= 0:
            consec_losses += 1
            if consec_losses >= 5:  # match paper_strategy CB threshold
                cb_until_ms = ts + 12 * 3_600_000  # 12h cooldown
                consec_losses = 0
        else:
            consec_losses = 0

        bars_held = int(opt.get("bars_held") or 0)
        side_hold_h = PUT_EXIT["hold_h"] if sim["side"] == "P" else CALL_EXIT["hold_h"]
        held_ms = min(bars_held * 5 * 60 * 1000, side_hold_h * 3_600_000)
        active_positions.append({"exit_ms": ts + held_ms, "margin_locked": margin_locked})

        equity_before = equity
        equity = round(equity + pnl_usd, 2)

        trades.append({
            "ts_ms": ts,
            "ts_iso": datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(),
            "side": sim["side"],
            "strike": strike,
            "spot_at_entry": round(spot, 2),
            # `size_usd` field now carries MARGIN locked (frontend label = "Маржа")
            "size_usd": round(margin_locked, 2),
            "n_lots": n_lots,
            "contracts_eth": round(contracts, 2),
            "premium_recv_usd": round(premium_received_total, 2),
            "fees_usd": round(fees_total, 3),
            "pnl_pct": round(pnl_pct_net, 2),
            "pnl_usd": pnl_usd,
            "exit_reason": opt.get("resolution", "unknown"),
            "bars_held": opt.get("bars_held"),
            "equity_before": round(equity_before, 2),
            "equity_after": equity,
        })

        equity_curve.append({"ts_ms": ts, "equity_usd": equity,
                             "label": "win" if pnl_usd > 0 else "loss"})

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
        "n_skipped_by_margin": skipped_margin,
        "n_skipped_by_busy": skipped_busy,
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
            f"Bybit-realistic model: 0.1-ETH lots, IM≈10%·strike+premium, "
            f"{SPREAD_PCT_TOTAL:.0f}% round-trip spread, 0.03% taker fee (cap 12.5% premium). "
            f"Start ${START_EQUITY_USD:.0f}. BS pricing — real fills ±20%."
        ),
        "cached": False,
        "cache_age_s": 0,
    }
    _CACHE[lookback_days] = (time.time(), payload)
    return payload


def _empty_report(lookback_days: int, now_ms: int) -> dict:
    return {
        "lookback_days": lookback_days,
        "generated_at_ms": now_ms,
        "n_signals": 0,
        "n_skipped_by_cb": 0,
        "n_skipped_by_margin": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "total_pnl_usd": 0.0,
        "total_pnl_pct": 0.0,
        "start_equity_usd": float(START_EQUITY_USD),
        "final_equity_usd": float(START_EQUITY_USD),
        "avg_pnl_pct_per_trade": 0.0,
        "resolution_counts": {},
        "trades": [],
        "equity_curve": [{"ts_ms": now_ms, "equity_usd": float(START_EQUITY_USD), "label": "start"}],
        "pricing_note": "no signals in window",
    }

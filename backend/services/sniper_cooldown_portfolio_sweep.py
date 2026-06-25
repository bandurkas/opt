"""Cooldown_bars portfolio-level sweep (2026-06-25) — real $-account engine.

finding_sniper1_entry_gap_cooldown.md (2026-06-24) found cooldown-suppressed
windows are NOT lower quality standalone (holdout avg/WR comparable, even
slightly better than fired ones) — but flagged a critical unresolved
caveat: those windows cluster right after a just-fired signal, riding the
SAME continuing move, so opening them too is concentration/correlation
risk, not diversification. That backtest treated every signal as an
independent trade, which they are NOT, and explicitly said "do not shorten
cooldown_bars from this result alone — needs a position-level (portfolio,
not per-signal) simulation first." This is that simulation.

Reuses deposit_sim.py's exact $-account engine (margin-based sizing,
MAX_OPEN_POSITIONS=4, 80% portfolio-margin cap, fees, dyn-size, circuit
breaker, dynamic-sigma option pricing) — NOT a naive "10% of equity per
pnl_pct%" sizing (that was calibrated for a different signal set's pnl_pct
SCALE and produced nonsense -99% maxDD for every cooldown candidate
including the live one when first tried here).

Method:
  1. Reuse sniper_persistence_backtest.py's cached per-minute readiness
     reconstruction (real evaluate_conditions, current CALL-anchor + the
     live tol1/FLICKER_TOLERANCE debounce already baked in).
  2. For each cooldown_bars candidate, replay calendar-ordered windows with
     a PERSISTENT last-fired-ts tracker per side — mirrors the live
     ts_ms-based cooldown fix exactly (see
     project_sniper1_mtf_anchor_and_cooldown_fix).
  3. Price + simulate each fired event with dyn_sim_set (dynamic-sigma,
     same TP1/TP2/SL/time-stop + real-DVOL pricing used everywhere else).
  4. Replay through the real $-account engine (margin/MAX_OPEN4/compound/
     fees/CB) — this is what actually exposes concentration risk that a
     per-trade-average view cannot.

Run: cd backend && PYTHONPATH=. .venv311/bin/python3 services/sniper_cooldown_portfolio_sweep.py
"""
from __future__ import annotations

import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs
from services.local_optimizer import find_data_dir, load_local
from services.multi_coin_signals import dyn_sim_set
from services.sniper_persistence_backtest import rule_tol1

FIVE_MIN_MS = 300_000
TRAIN_FRAC = 0.70
COOLDOWN_CANDIDATES_BARS = [3, 4, 5, 6, 8, 10, 12]  # 15/20/25/30(live)/40/50/60 min

START = 800.0  # matches live PAPER_START_EQUITY_USD=800 (deposit_sim.py default 400 is stale)
MARGIN_PCT = 0.15
IM_RATE = 0.10
LOT = 0.1
MAX_OPEN = 4
PORT_MARGIN_CAP = 0.80
FEE_RATE = 0.0003
FEE_CAP = 0.125
HALF_SPREAD = 0.01
EXPIRY_H = 168.0
CB_LOSSES = 5
CB_COOLDOWN_MS = 48 * 3600 * 1000


def events_for_cooldown(records: list[dict], cooldown_bars: int) -> list[dict]:
    """Same persistent-tracker logic as paper_loop's fixed check_new_signal
    (ts_ms-based, not idx_5m-based) — independent per-side cooldown,
    calendar-ordered, matches the live mechanism exactly."""
    out = []
    last_ts = {"P": -10**18, "C": -10**18}
    cooldown_ms = cooldown_bars * FIVE_MIN_MS
    for r in records:
        side = r["side"]
        if side is None or not rule_tol1(r):
            continue
        if r["ts_ms"] - last_ts[side] < cooldown_ms:
            continue
        last_ts[side] = r["ts_ms"]
        out.append({"idx_5m": r["idx_5m"], "ts_ms": r["ts_ms"], "close": r["close"],
                    "side": side, "position": "short_premium"})
    return out


def build_dollar_trades(events: list[dict], k5: list, k1h: list) -> list[dict]:
    """Mirrors deposit_sim.run()'s trade-building exactly (dynamic-sigma
    option pricing, strike rounding, credit net of half-spread)."""
    sims = dyn_sim_set(events, k5, k1h)
    trades = []
    for s in sims:
        o = s["option"]
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        spot = s["close"]
        strike = round(spot / 25) * 25
        T0 = EXPIRY_H / (24 * 365)
        mid = bs.price(s["side"], spot, strike, T0, s["sigma_used"])
        if mid <= 0.01:
            continue
        credit = mid * (1 - HALF_SPREAD)
        bars = o.get("bars_held", int(EXPIRY_H * 12))
        trades.append({
            "ts": int(s["ts_ms"]), "exit_ts": int(s["ts_ms"]) + bars * FIVE_MIN_MS,
            "side": s["side"], "strike": strike, "mid": mid, "credit": credit,
            "pnl_pct": o["pnl_pct"] / 100.0,
        })
    trades.sort(key=lambda t: t["ts"])
    return trades


def fee(notional, premium_total):
    return min(notional * FEE_RATE, abs(premium_total) * FEE_CAP)


def replay_dollar_account(trades: list[dict], compounding: bool = True) -> dict:
    """Deposit_sim.run()'s exact account-replay loop, factored out so it can
    run on an arbitrary (cooldown-varying) trade list instead of only the
    live generate(variant='v3') stream."""
    if not trades:
        return {"n_taken": 0, "final": START, "return_pct": 0.0, "max_dd": 0.0,
                "blocked_cap": 0, "blocked_margin": 0, "blocked_cb": 0}

    equity = START
    peak = equity
    max_dd = 0.0
    open_pos: list[dict] = []
    recent_pnls: list[float] = []
    consec = 0
    cb_until = 0
    n_taken = n_blocked_cap = n_blocked_margin = n_blocked_cb = 0
    monthly: dict[str, float] = {}
    by_side: dict[str, list[float]] = {"P": [], "C": []}

    def realize_due(now_ts):
        nonlocal equity, peak, max_dd, consec, cb_until
        still = []
        for p in sorted(open_pos, key=lambda x: x["exit_ts"]):
            if p["exit_ts"] <= now_ts:
                equity += p["pnl_dollars"]
                recent_pnls.append(p["pnl_pct"])
                by_side[p["side"]].append(p["pnl_dollars"])
                if p["pnl_pct"] > 0:
                    consec = 0
                else:
                    consec += 1
                    if consec >= CB_LOSSES:
                        cb_until = p["exit_ts"] + CB_COOLDOWN_MS
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
                mk = datetime.fromtimestamp(p["exit_ts"] / 1000, tz=timezone.utc).strftime("%Y-%m")
                monthly[mk] = monthly.get(mk, 0.0) + p["pnl_dollars"]
            else:
                still.append(p)
        open_pos[:] = still

    for t in trades:
        realize_due(t["ts"])
        if t["ts"] < cb_until:
            n_blocked_cb += 1
            continue
        if len(open_pos) >= MAX_OPEN:
            n_blocked_cap += 1
            continue
        size_base = equity if compounding else START
        used_margin = sum(p["margin"] for p in open_pos)
        free = max(0.0, size_base * PORT_MARGIN_CAP - used_margin)
        dyn = 0.5 if (len(recent_pnls) >= 10 and
                      sum(1 for x in recent_pnls[-10:] if x > 0) / 10 < 0.40) else 1.0
        budget = min(size_base * MARGIN_PCT * dyn, free)
        m_per_lot = (IM_RATE * t["strike"] + t["mid"]) * LOT
        n_lots = int(budget // m_per_lot) if m_per_lot > 0 else 0
        if n_lots < 1:
            n_blocked_margin += 1
            continue
        qty = n_lots * LOT
        margin = m_per_lot * n_lots
        credit_total = t["credit"] * qty
        notional = t["strike"] * qty
        gross = credit_total * t["pnl_pct"]
        fees = 2 * fee(notional, credit_total)
        pnl_dollars = gross - fees
        open_pos.append({"exit_ts": t["exit_ts"], "margin": margin, "side": t["side"],
                         "pnl_dollars": pnl_dollars, "pnl_pct": t["pnl_pct"]})
        n_taken += 1

    if open_pos:
        realize_due(max(p["exit_ts"] for p in open_pos) + 1)

    losing_months = sum(1 for v in monthly.values() if v < 0)
    return {
        "n_taken": n_taken, "final": round(equity, 2),
        "return_pct": round((equity - START) / START * 100, 1),
        "max_dd": round(max_dd * 100, 1),
        "blocked_cap": n_blocked_cap, "blocked_margin": n_blocked_margin,
        "blocked_cb": n_blocked_cb,
        "losing_months": losing_months, "total_months": len(monthly),
        "worst_month": round(min(monthly.values()), 1) if monthly else 0.0,
        "by_side_n": {sd: len(v) for sd, v in by_side.items()},
        "by_side_pnl": {sd: round(sum(v), 1) for sd, v in by_side.items()},
    }


def split_trades(trades: list[dict]) -> tuple[list[dict], list[dict]]:
    if not trades:
        return [], []
    cutoff = trades[0]["ts"] + TRAIN_FRAC * (trades[-1]["ts"] - trades[0]["ts"])
    return ([t for t in trades if t["ts"] < cutoff],
            [t for t in trades if t["ts"] >= cutoff])


def _row(label: str, m: dict) -> str:
    return (f"  {label:<10} n_taken={m['n_taken']:>4} final=${m['final']:>9,.2f} "
            f"return={m['return_pct']:>+8.1f}% maxDD={m['max_dd']:>5.1f}% "
            f"losM={m['losing_months']}/{m['total_months']} worstM=${m['worst_month']:>+7.1f} "
            f"P/C n={m['by_side_n'].get('P',0)}/{m['by_side_n'].get('C',0)} "
            f"$={m['by_side_pnl'].get('P',0):+.0f}/{m['by_side_pnl'].get('C',0):+.0f}")


def quarter_splits(trades: list[dict], n_quarters: int = 4) -> list[list[dict]]:
    """Contiguous, non-overlapping time segments — each evaluated with a FRESH
    START equity, so the comparison isolates whether the edge holds up
    period-by-period rather than being driven by one lucky compounding run."""
    if not trades:
        return []
    t0, t1 = trades[0]["ts"], trades[-1]["ts"]
    span = (t1 - t0) / n_quarters
    out = []
    for q in range(n_quarters):
        lo, hi = t0 + q * span, t0 + (q + 1) * span
        out.append([t for t in trades if lo <= t["ts"] < hi or (q == n_quarters - 1 and t["ts"] == hi)])
    return out


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_local(data_dir)
    cache_path = data_dir / "sniper_persistence_records_cache.pkl"
    records = pickle.loads(cache_path.read_bytes())
    print(f"klines: 5m={len(k5):,}  cached windows: {len(records):,}  START=${START:.0f}\n", flush=True)

    print("=" * 130)
    print("Cooldown_bars portfolio sweep — real $-account engine "
          "(margin/MAX_OPEN4/compound/fees/CB, dynamic-sigma pricing)")
    print("=" * 130)
    all_trades = {}
    for cb in COOLDOWN_CANDIDATES_BARS:
        events = events_for_cooldown(records, cb)
        trades = build_dollar_trades(events, k5, k1h)
        all_trades[cb] = trades
        train, hold = split_trades(trades)
        marker = " <- LIVE" if cb == 6 else ""
        print(f"\ncooldown_bars={cb} ({cb*5}min){marker}  raw_events={len(events)} priced_trades={len(trades)}")
        print(_row("FULL", replay_dollar_account(trades)))
        print(_row("TRAIN", replay_dollar_account(train)))
        print(_row("HOLDOUT", replay_dollar_account(hold)))

    # ── Walk-forward robustness: 4 contiguous quarters, fresh equity each,
    # for the candidates actually in contention (live + the promising ones). ──
    print("\n" + "=" * 130)
    print("Walk-forward (4 contiguous quarters, FRESH $START each — isolates period-by-period edge)")
    print("=" * 130)
    for cb in (6, 8, 10, 12):
        marker = " <- LIVE" if cb == 6 else ""
        print(f"\ncooldown_bars={cb} ({cb*5}min){marker}")
        quarters = quarter_splits(all_trades[cb], n_quarters=4)
        for qi, qtrades in enumerate(quarters):
            m = replay_dollar_account(qtrades)
            print(_row(f"Q{qi+1}", m))

    print(f"\nelapsed {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

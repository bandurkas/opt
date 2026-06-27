"""Concentration-risk theory sweep (2026-06-27) — real $-account engine,
extended with intrabar mark-to-market so we can test rules that react to
an OPEN position's current health, not just its final outcome.

Context: 2026-06-26 Sniper1 cluster — 3x re-entry into Call@1525, then 3x
into Call@1550, all opened ~30min apart, all SL. The live circuit breaker
(CB_CONSEC_LIMIT=1/CB_PAUSE_HOURS=8) only fires when a trade CLOSES — by
the time the first loss closed (hours later), several more had already
opened. A blanket "max 1 position per strike" rule was tested and
REJECTED (sniper_strike_cap_sweep.py, 2026-06-27): it cuts train return
+45.7%->+11.2%, worse than baseline on full history too — it blocks
profitable same-strike stacking during trend continuation (Q2) along with
the bad cluster.

This sweep tests narrower theories, all reusing the exact $-account engine
(margin/MAX_OPEN4/compound/fees/CB, dynamic-sigma pricing) at the LIVE
cooldown_bars=6 setting:

  A) max_per_strike=2 (looser than the rejected =1)
  B) max_per_side: cap concurrent open positions on the SAME side
     (independent of strike) at N in {1,2,3}
  C) side_stack_cooldown_min: extra cooldown specifically between
     same-side entries (on top of the live per-side signal cooldown),
     in minutes: {30,60,90,120,180}
  D) mark_dd_gate: NEW — block a new same-side entry if any currently-open
     position on that side already shows unrealized loss beyond
     `mark_dd_frac` of its entry margin, using a real intrabar BS reprice
     (current spot from the 5m kline grid, entry sigma, time-decayed T).
     This is the closest analogue to "the strategy can see the position is
     already going wrong" — it reacts to OPEN risk, not realized closes.
     mark_dd_frac in {0.25, 0.50, 0.75, 1.00}
  E) combinations of B x D, C x D

Grid is run in parallel across all CPU cores (multiprocessing.Pool).
Selection discipline (AGENTS.md #1): rank candidates by TRAIN metrics
first; only look at HOLDOUT/walk-forward to confirm robustness of
already-promising-on-train candidates, never to cherry-pick.

Run: cd backend && PYTHONPATH=. .venv311/bin/python3 services/sniper_concentration_theories_sweep.py
"""
from __future__ import annotations

import itertools
import multiprocessing as mp
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
LIVE_COOLDOWN_BARS = 6

START = 800.0
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
YEAR_MS = 365 * 24 * 3600 * 1000


# ─────────────────────── data prep (shared, computed once) ───────────────────────

def events_for_cooldown(records: list[dict], cooldown_bars: int) -> list[dict]:
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
    """Like the baseline build, but also keeps sigma_used + T0 so we can
    intrabar-reprice the position later (mark_dd_gate theory)."""
    sims = dyn_sim_set(events, k5, k1h)
    trades = []
    for s in sims:
        o = s["option"]
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        spot = s["close"]
        strike = round(spot / 25) * 25
        T0 = EXPIRY_H / (24 * 365)
        sigma = s["sigma_used"]
        mid = bs.price(s["side"], spot, strike, T0, sigma)
        if mid <= 0.01:
            continue
        credit = mid * (1 - HALF_SPREAD)
        bars = o.get("bars_held", int(EXPIRY_H * 12))
        trades.append({
            "ts": int(s["ts_ms"]), "exit_ts": int(s["ts_ms"]) + bars * FIVE_MIN_MS,
            "side": s["side"], "strike": strike, "mid": mid, "credit": credit,
            "sigma": sigma, "T0": T0,
            "pnl_pct": o["pnl_pct"] / 100.0,
        })
    trades.sort(key=lambda t: t["ts"])
    return trades


class SpotLookup:
    """O(1) spot lookup on the evenly-spaced 5m grid (falls back to nearest
    if a timestamp doesn't land exactly on a bar)."""
    def __init__(self, k5: list):
        self.t0 = k5[0]["start_ms"]
        self.closes = [c["close"] for c in k5]
        self.n = len(self.closes)

    def at(self, ts_ms: int) -> float:
        idx = int((ts_ms - self.t0) // FIVE_MIN_MS)
        idx = max(0, min(self.n - 1, idx))
        return self.closes[idx]


def fee(notional, premium_total):
    return min(notional * FEE_RATE, abs(premium_total) * FEE_CAP)


def mark_now(trade: dict, now_ts: int, spot_lookup: SpotLookup) -> float:
    """BS reprice of a short option position at now_ts using the entry sigma
    and time-decayed T (floor at a small epsilon to avoid blow-up near
    expiry) — the same methodology used everywhere else in this codebase
    for unrealized MtM."""
    elapsed_years = max(0.0, (now_ts - trade["ts"]) / YEAR_MS)
    T_now = max(trade["T0"] - elapsed_years, 1.0 / (365 * 24))  # floor 1h
    spot_now = spot_lookup.at(now_ts)
    return bs.price(trade["side"], spot_now, trade["strike"], T_now, trade["sigma"])


# ─────────────────────── parametrized $-account engine ───────────────────────

def replay(trades: list[dict], spot_lookup: SpotLookup,
           max_per_strike: int | None = None,
           max_per_side: int | None = None,
           side_stack_cooldown_ms: int = 0,
           mark_dd_frac: float | None = None,
           min_strike_dist: float | None = None,
           stack_size_decay: float | None = None) -> dict:
    """Generalized replay: baseline behaviour when all the optional knobs
    are left at their no-op defaults (None / 0). Each knob independently
    blocks a candidate trade at entry time if it would violate the rule
    against currently-open positions."""
    if not trades:
        return _empty_result()

    equity = START
    peak = equity
    max_dd = 0.0
    open_pos: list[dict] = []
    recent_pnls: list[float] = []
    consec = 0
    cb_until = 0
    last_side_entry_ts = {"P": -10**18, "C": -10**18}
    n_taken = n_blocked_cap = n_blocked_margin = n_blocked_cb = 0
    n_blocked_strike = n_blocked_side = n_blocked_stack_cd = n_blocked_markdd = n_blocked_dist = 0
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

        if max_per_strike is not None:
            n_same_strike = sum(1 for p in open_pos if p["side"] == t["side"] and p["strike"] == t["strike"])
            if n_same_strike >= max_per_strike:
                n_blocked_strike += 1
                continue

        if max_per_side is not None:
            n_same_side = sum(1 for p in open_pos if p["side"] == t["side"])
            if n_same_side >= max_per_side:
                n_blocked_side += 1
                continue

        if side_stack_cooldown_ms and (t["ts"] - last_side_entry_ts[t["side"]]) < side_stack_cooldown_ms:
            n_blocked_stack_cd += 1
            continue

        if mark_dd_frac is not None:
            blocked = False
            for p in open_pos:
                if p["side"] != t["side"]:
                    continue
                ref = p["trade_ref"]
                cur_mark = mark_now(ref, t["ts"], spot_lookup)
                unrealized_loss = cur_mark - ref["credit"]  # >0 = losing (short premium)
                # Denominator is the entry CREDIT (premium), not margin — margin is
                # dominated by IM_RATE*strike (~$150+) and dwarfs any realistic premium
                # swing, which silently made this gate a no-op in the first pass.
                if unrealized_loss > mark_dd_frac * ref["credit"]:
                    blocked = True
                    break
            if blocked:
                n_blocked_markdd += 1
                continue

        if min_strike_dist is not None:
            same_side_open = [p for p in open_pos if p["side"] == t["side"]]
            if same_side_open and min(abs(p["strike"] - t["strike"]) for p in same_side_open) < min_strike_dist:
                n_blocked_dist += 1
                continue

        size_base = equity
        used_margin = sum(p["margin"] for p in open_pos)
        free = max(0.0, size_base * PORT_MARGIN_CAP - used_margin)
        dyn = 0.5 if (len(recent_pnls) >= 10 and
                      sum(1 for x in recent_pnls[-10:] if x > 0) / 10 < 0.40) else 1.0
        # stack_size_decay: shrink the size budget geometrically by the number
        # of already-open SAME-SIDE positions, instead of blocking outright —
        # keeps some participation in a continuing trend while capping the
        # dollar exposure a reversal cluster can rack up.
        if stack_size_decay is not None:
            n_same_side_open = sum(1 for p in open_pos if p["side"] == t["side"])
            dyn *= stack_size_decay ** n_same_side_open
        budget = min(size_base * MARGIN_PCT * dyn, free)
        m_per_lot = (IM_RATE * t["strike"] + t["mid"]) * LOT
        n_lots = int(budget // m_per_lot) if m_per_lot > 0 else 0
        if n_lots < 1:
            n_blocked_margin += 1
            continue
        if len(open_pos) >= MAX_OPEN:
            n_blocked_cap += 1
            continue

        qty = n_lots * LOT
        margin = m_per_lot * n_lots
        credit_total = t["credit"] * qty
        notional = t["strike"] * qty
        gross = credit_total * t["pnl_pct"]
        fees = 2 * fee(notional, credit_total)
        pnl_dollars = gross - fees
        open_pos.append({"exit_ts": t["exit_ts"], "margin": margin, "side": t["side"],
                         "strike": t["strike"], "qty": qty, "pnl_dollars": pnl_dollars,
                         "pnl_pct": t["pnl_pct"], "trade_ref": t})
        last_side_entry_ts[t["side"]] = t["ts"]
        n_taken += 1

    if open_pos:
        realize_due(max(p["exit_ts"] for p in open_pos) + 1)

    losing_months = sum(1 for v in monthly.values() if v < 0)
    return {
        "n_taken": n_taken, "final": round(equity, 2),
        "return_pct": round((equity - START) / START * 100, 1),
        "max_dd": round(max_dd * 100, 1),
        "blocked": {"cap": n_blocked_cap, "margin": n_blocked_margin, "cb": n_blocked_cb,
                    "strike": n_blocked_strike, "side": n_blocked_side,
                    "stack_cd": n_blocked_stack_cd, "markdd": n_blocked_markdd,
                    "dist": n_blocked_dist},
        "losing_months": losing_months, "total_months": len(monthly),
        "worst_month": round(min(monthly.values()), 1) if monthly else 0.0,
        "by_side_n": {sd: len(v) for sd, v in by_side.items()},
        "by_side_pnl": {sd: round(sum(v), 1) for sd, v in by_side.items()},
    }


def _empty_result():
    return {"n_taken": 0, "final": START, "return_pct": 0.0, "max_dd": 0.0,
            "blocked": {}, "losing_months": 0, "total_months": 0, "worst_month": 0.0,
            "by_side_n": {}, "by_side_pnl": {}}


def split_trades(trades: list[dict]) -> tuple[list[dict], list[dict]]:
    if not trades:
        return [], []
    cutoff = trades[0]["ts"] + TRAIN_FRAC * (trades[-1]["ts"] - trades[0]["ts"])
    return ([t for t in trades if t["ts"] < cutoff],
            [t for t in trades if t["ts"] >= cutoff])


def quarter_splits(trades: list[dict], n_quarters: int = 4) -> list[list[dict]]:
    if not trades:
        return []
    t0, t1 = trades[0]["ts"], trades[-1]["ts"]
    span = (t1 - t0) / n_quarters
    out = []
    for q in range(n_quarters):
        lo, hi = t0 + q * span, t0 + (q + 1) * span
        out.append([t for t in trades if lo <= t["ts"] < hi or (q == n_quarters - 1 and t["ts"] == hi)])
    return out


def calmar(m: dict) -> float:
    """return_pct / max_dd, the usual robustness-adjusted ranking metric —
    avoids picking a candidate that wins on raw return by taking on far more
    drawdown."""
    if m["max_dd"] <= 0.1:
        return m["return_pct"] / 0.1
    return m["return_pct"] / m["max_dd"]


# ─────────────────────── grid + parallel runner ───────────────────────

# Module-level globals populated once in main() / worker init, so each
# worker process doesn't need to re-pickle the trades/spot_lookup per task.
_G = {}


def _init_worker(trades, spot_lookup):
    _G["trades"] = trades
    _G["spot_lookup"] = spot_lookup


def _run_one(label_and_kwargs):
    label, kwargs = label_and_kwargs
    trades = _G["trades"]
    spot_lookup = _G["spot_lookup"]
    train, hold = split_trades(trades)
    full_m = replay(trades, spot_lookup, **kwargs)
    train_m = replay(train, spot_lookup, **kwargs)
    hold_m = replay(hold, spot_lookup, **kwargs)
    quarters = [replay(q, spot_lookup, **kwargs) for q in quarter_splits(trades, 4)]
    return label, kwargs, full_m, train_m, hold_m, quarters


def build_grid() -> list[tuple[str, dict]]:
    grid: list[tuple[str, dict]] = [("baseline", {})]

    for n in (1, 2):
        grid.append((f"strike_cap={n}", {"max_per_strike": n}))

    for n in (1, 2, 3):
        grid.append((f"side_cap={n}", {"max_per_side": n}))

    for mins in (60, 90, 120, 180):
        grid.append((f"side_stack_cd={mins}m", {"side_stack_cooldown_ms": mins * 60_000}))

    # frac = fraction of entry CREDIT already lost (unrealized) on an OPEN
    # same-side position before refusing a new same-side entry. Calibration:
    # live Call SL trips ~0.75x credit (dollar-margin variant aside), Put SL
    # trips at 2.00x credit — so 0.3-1.0 spans "noticeably hurting" up to
    # "near/at the Call SL level", without being redundant with the SL itself.
    for frac in (0.30, 0.50, 0.75, 1.00):
        grid.append((f"mark_dd={frac:.2f}", {"mark_dd_frac": frac}))

    # combinations: side_cap x mark_dd, side_stack_cd x mark_dd
    for n in (2, 3):
        for frac in (0.30, 0.50, 0.75):
            grid.append((f"side_cap={n}+mark_dd={frac:.2f}",
                        {"max_per_side": n, "mark_dd_frac": frac}))
    for mins in (60, 120):
        for frac in (0.30, 0.50, 0.75):
            grid.append((f"side_stack_cd={mins}m+mark_dd={frac:.2f}",
                        {"side_stack_cooldown_ms": mins * 60_000, "mark_dd_frac": frac}))

    # Strike-distance gate: refuse a new same-side entry if it's within
    # `dist` $ of any already-open same-side strike. dist=0 ~= strike_cap=1
    # (since strikes round to a $25 grid); wider distances test whether
    # "stack only on a meaningfully different strike" preserves more edge
    # than an exact-strike-match block.
    for dist in (25, 50, 75, 100):
        grid.append((f"min_strike_dist={dist}", {"min_strike_dist": dist}))

    # Size-decay: don't block stacking, just shrink the size of each
    # additional same-side entry geometrically (decay^n_open_same_side).
    # Keeps some trend participation while capping how much dollar exposure
    # a reversal cluster (like 2026-06-26) can rack up.
    for decay in (0.7, 0.5, 0.3):
        grid.append((f"stack_decay={decay:.1f}", {"stack_size_decay": decay}))

    for decay in (0.7, 0.5):
        for dist in (25, 50):
            grid.append((f"stack_decay={decay:.1f}+min_strike_dist={dist}",
                        {"stack_size_decay": decay, "min_strike_dist": dist}))

    return grid


def _row(label: str, m: dict) -> str:
    b = m["blocked"]
    return (f"  {label:<32} n={m['n_taken']:>4} final=${m['final']:>9,.2f} "
            f"ret={m['return_pct']:>+7.1f}% maxDD={m['max_dd']:>5.1f}% calmar={calmar(m):>+7.1f} "
            f"losM={m['losing_months']}/{m['total_months']} worstM=${m['worst_month']:>+7.1f} "
            f"blk(strike/side/cd/markdd/dist)={b.get('strike',0)}/{b.get('side',0)}/{b.get('stack_cd',0)}/{b.get('markdd',0)}/{b.get('dist',0)}")


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_local(data_dir)
    cache_path = data_dir / "sniper_persistence_records_cache.pkl"
    records = pickle.loads(cache_path.read_bytes())
    print(f"klines: 5m={len(k5):,}  cached windows: {len(records):,}  START=${START:.0f}", flush=True)

    events = events_for_cooldown(records, LIVE_COOLDOWN_BARS)
    trades = build_dollar_trades(events, k5, k1h)
    spot_lookup = SpotLookup(k5)
    print(f"cooldown_bars={LIVE_COOLDOWN_BARS} raw_events={len(events)} priced_trades={len(trades)}", flush=True)

    grid = build_grid()
    print(f"grid size: {len(grid)} candidates, running on {mp.cpu_count()} cores\n", flush=True)

    ctx = mp.get_context("fork")
    with ctx.Pool(processes=mp.cpu_count(), initializer=_init_worker, initargs=(trades, spot_lookup)) as pool:
        results = pool.map(_run_one, grid)

    # Rank by TRAIN calmar first (selection discipline) — print full table sorted by train calmar.
    results.sort(key=lambda r: calmar(r[3]), reverse=True)

    print("=" * 150)
    print("Ranked by TRAIN calmar (return%/maxDD%) — selection happens here, holdout is confirmation only")
    print("=" * 150)
    for label, kwargs, full_m, train_m, hold_m, quarters in results:
        print(f"\n{label}  {kwargs}")
        print(_row("FULL", full_m))
        print(_row("TRAIN", train_m))
        print(_row("HOLDOUT", hold_m))

    print("\n" + "=" * 150)
    print("Walk-forward (top 8 by train calmar) — 4 contiguous quarters, fresh $START each")
    print("=" * 150)
    for label, kwargs, full_m, train_m, hold_m, quarters in results[:8]:
        print(f"\n{label}")
        for qi, qm in enumerate(quarters):
            print(_row(f"Q{qi+1}", qm))

    print(f"\nelapsed {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

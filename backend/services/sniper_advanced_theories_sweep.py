"""Advanced concentration-risk theories sweep (2026-06-27, round 2).

Round 1 (sniper_concentration_theories_sweep.py) rejected every entry-gating
idea (strike/side caps, extra cooldowns, entry-time mark-dd gates, strike
distance gates, fixed-ratio size decay): all of them cut TRAIN return because
they block the same stacking mechanism that also rides legitimate trends.

This round tests 4 ideas that do NOT gate entries:

  1. cluster_stop_frac — NEW. Track combined unrealized PnL across all open
     SAME-SIDE positions, checked every 5-min tick (not just at trade
     arrivals). If combined loss exceeds `cluster_stop_frac` x combined
     entry credit, force-close the whole same-side cluster immediately via
     BS reprice (mark_now), instead of waiting for each leg's own SL.
  2. momentum_veto — suppress a signal at generation time if spot has moved
     more than `momentum_veto_pct` against that side's thesis over the
     preceding `momentum_veto_bars` 5-min bars, regardless of what the
     regime/MTF classifiers say. Targets the suspected root cause: signals
     kept firing into a strengthening adverse move for hours on 2026-06-26.
  3. adaptive_decay — replaces the rejected fixed-ratio stack_size_decay.
     Only shrinks size for a new same-side entry if price has moved
     adversely (against that side) by more than `adverse_pct` since the
     last same-side entry; size *= decay_ratio when triggered, else
     untouched. Direction-conditioned, not blanket.
  4. stacked_time_stop_h — the 2nd+ concurrently-open same-side position
     gets hold_h capped at this value (e.g. 8-12h instead of 24h for
     Calls), so it self-resolves faster. Forces an early BS-reprice exit
     instead of waiting the full natural hold.

Engineering note: round 1's replay() only evaluated state at trade-arrival
timestamps. cluster_stop and stacked_time_stop need evaluation at *regular
ticks* to catch a mid-hold breach, not just at the next trade's arrival. This
module walks a unified 5-min tick timeline (merged with trade arrivals)
instead.

Reuses round 1's validated $-account primitives (events_for_cooldown,
build_dollar_trades, SpotLookup, mark_now, fee, split_trades, quarter_splits,
calmar) — only the replay loop and the momentum-veto event filter are new.

Run: cd backend && PYTHONPATH=. .venv311/bin/python3 services/sniper_advanced_theories_sweep.py
"""
from __future__ import annotations

import multiprocessing as mp
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir, load_local
from services.sniper_concentration_theories_sweep import (
    CB_COOLDOWN_MS, CB_LOSSES, FEE_CAP, FEE_RATE, FIVE_MIN_MS, IM_RATE, LOT,
    LIVE_COOLDOWN_BARS, MARGIN_PCT, MAX_OPEN, PORT_MARGIN_CAP, START,
    SpotLookup, build_dollar_trades, calmar, events_for_cooldown, fee,
    mark_now, quarter_splits, split_trades,
)


# ─────────────────────── momentum veto (idea 2) ───────────────────────

def events_with_momentum_veto(events: list[dict], k5: list,
                              pct_threshold: float, bars: int) -> list[dict]:
    """Drop a signal if spot has already moved >pct_threshold against that
    side's thesis over the preceding `bars` 5m bars. Call thesis = spot
    falling (short premium benefits from spot staying below strike), so an
    UP move of pct_threshold vetoes a Call; Put thesis is the mirror."""
    n = len(k5)
    out = []
    for r in events:
        idx = r["idx_5m"]
        if idx is None or idx < bars or idx >= n:
            out.append(r)
            continue
        now_px = k5[idx]["close"]
        prior_px = k5[idx - bars]["close"]
        move_pct = (now_px - prior_px) / prior_px * 100.0
        if r["side"] == "C" and move_pct > pct_threshold:
            continue  # spot rose hard — adverse for a short Call
        if r["side"] == "P" and -move_pct > pct_threshold:
            continue  # spot fell hard — adverse for a short Put
        out.append(r)
    return out


# ─────────────────────── unified tick-driven $-account engine ───────────────────────

def build_tick_grid(t0: int, t1: int) -> list[int]:
    n = int((t1 - t0) // FIVE_MIN_MS) + 2
    return [t0 + i * FIVE_MIN_MS for i in range(n)]


def replay_v2(trades: list[dict], spot_lookup: SpotLookup,
             cluster_stop_frac: float | dict[str, float] | None = None,
             cluster_stop_worst_leg_only: bool = False,
             stacked_time_stop_h: float | None = None,
             adaptive_decay: tuple[float, float] | None = None,
             log_cluster_events: list | None = None) -> dict:
    """Tick-driven replay (5-min steps) of the validated $-account engine,
    extended with cluster_stop / stacked_time_stop / adaptive_decay. With
    all three knobs at their no-op defaults this reproduces round 1's
    baseline numbers exactly (same accept logic, same fee/margin/CB math)."""
    if not trades:
        return _empty_result()

    t0, t1 = trades[0]["ts"], max(t["exit_ts"] for t in trades)
    ticks = build_tick_grid(t0, t1)
    trades_by_ts: dict[int, list[dict]] = {}
    for t in trades:
        trades_by_ts.setdefault(t["ts"], []).append(t)

    equity = START
    peak = equity
    max_dd = 0.0
    open_pos: list[dict] = []
    recent_pnls: list[float] = []
    consec = 0
    cb_until = 0
    last_side_entry_ts = {"P": -10**18, "C": -10**18}
    last_side_entry_px = {"P": None, "C": None}
    n_taken = n_blocked_cap = n_blocked_margin = n_blocked_cb = 0
    n_cluster_stops = n_stacked_early_exits = 0
    monthly: dict[str, float] = {}
    by_side: dict[str, list[float]] = {"P": [], "C": []}

    def book_close(p: dict, now_ts: int, pnl_dollars: float, pnl_pct: float):
        nonlocal equity, peak, max_dd, consec, cb_until
        equity += pnl_dollars
        recent_pnls.append(pnl_pct)
        by_side[p["side"]].append(pnl_dollars)
        if pnl_pct > 0:
            consec = 0
        else:
            consec += 1
            if consec >= CB_LOSSES:
                cb_until = now_ts + CB_COOLDOWN_MS
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        mk = datetime.fromtimestamp(now_ts / 1000, tz=timezone.utc).strftime("%Y-%m")
        monthly[mk] = monthly.get(mk, 0.0) + pnl_dollars

    def natural_pnl_dollars(p: dict) -> float:
        return p["pnl_dollars"]

    def early_pnl_dollars(p: dict, now_ts: int) -> tuple[float, float]:
        ref = p["trade_ref"]
        cur_mark = mark_now(ref, now_ts, spot_lookup)
        pnl_per_unit = ref["credit"] - cur_mark
        gross = pnl_per_unit * p["qty"]
        notional = ref["strike"] * p["qty"]
        credit_total = ref["credit"] * p["qty"]
        fees = 2 * fee(notional, credit_total)
        pnl_dollars = gross - fees
        pnl_pct = pnl_per_unit / ref["credit"] if ref["credit"] else 0.0
        return pnl_dollars, pnl_pct

    for now_ts in ticks:
        # 1) realize natural exits due by now
        still = []
        for p in sorted(open_pos, key=lambda x: x["exit_ts"]):
            if p["exit_ts"] <= now_ts:
                if p.get("early_exit"):
                    pnl_d, pnl_p = early_pnl_dollars(p, p["exit_ts"])
                else:
                    pnl_d, pnl_p = natural_pnl_dollars(p), p["pnl_pct"]
                book_close(p, p["exit_ts"], pnl_d, pnl_p)
            else:
                still.append(p)
        open_pos[:] = still

        # 2) cluster-stop: combined unrealized loss per side, forced close
        #    (whole cluster, or just the worst leg if cluster_stop_worst_leg_only)
        if cluster_stop_frac is not None and open_pos:
            for side in ("P", "C"):
                frac = (cluster_stop_frac.get(side) if isinstance(cluster_stop_frac, dict)
                        else cluster_stop_frac)
                if frac is None:
                    continue
                same = [p for p in open_pos if p["side"] == side]
                if len(same) < 2:
                    continue
                combined_credit = sum(p["trade_ref"]["credit"] * p["qty"] for p in same)
                if combined_credit <= 0:
                    continue
                combined_unrealized = 0.0
                marks = {}
                for p in same:
                    cur_mark = mark_now(p["trade_ref"], now_ts, spot_lookup)
                    marks[p["id"]] = cur_mark
                    combined_unrealized += (cur_mark - p["trade_ref"]["credit"]) * p["qty"]
                if combined_unrealized > frac * combined_credit:
                    n_cluster_stops += 1
                    if cluster_stop_worst_leg_only:
                        worst = max(same, key=lambda p: marks[p["id"]] - p["trade_ref"]["credit"])
                        targets = [worst]
                    else:
                        targets = same
                    if log_cluster_events is not None:
                        log_cluster_events.append({
                            "ts": now_ts, "side": side, "n_legs": len(same),
                            "n_closed": len(targets), "combined_unrealized": combined_unrealized,
                            "combined_credit": combined_credit,
                        })
                    for p in targets:
                        ref = p["trade_ref"]
                        cur_mark = marks[p["id"]]
                        pnl_per_unit = ref["credit"] - cur_mark
                        gross = pnl_per_unit * p["qty"]
                        notional = ref["strike"] * p["qty"]
                        credit_total = ref["credit"] * p["qty"]
                        fees = 2 * fee(notional, credit_total)
                        pnl_d = gross - fees
                        pnl_p = pnl_per_unit / ref["credit"] if ref["credit"] else 0.0
                        book_close(p, now_ts, pnl_d, pnl_p)
                    target_ids = {id(p) for p in targets}
                    open_pos[:] = [p for p in open_pos if id(p) not in target_ids]

        # 3) accept any trades arriving exactly at this tick
        for t in trades_by_ts.get(now_ts, []):
            if now_ts < cb_until:
                n_blocked_cb += 1
                continue

            size_base = equity
            used_margin = sum(p["margin"] for p in open_pos)
            free = max(0.0, size_base * PORT_MARGIN_CAP - used_margin)
            dyn = 0.5 if (len(recent_pnls) >= 10 and
                          sum(1 for x in recent_pnls[-10:] if x > 0) / 10 < 0.40) else 1.0

            n_same_side_open = sum(1 for p in open_pos if p["side"] == t["side"])

            if adaptive_decay is not None and n_same_side_open >= 1:
                decay_ratio, adverse_pct = adaptive_decay
                last_px = last_side_entry_px[t["side"]]
                if last_px:
                    move_pct = (t["close_spot"] - last_px) / last_px * 100.0
                    adverse = (t["side"] == "C" and move_pct > adverse_pct) or \
                              (t["side"] == "P" and -move_pct > adverse_pct)
                    if adverse:
                        dyn *= decay_ratio

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

            exit_ts = t["exit_ts"]
            early_exit = False
            if stacked_time_stop_h is not None and n_same_side_open >= 1:
                capped_exit = t["ts"] + int(stacked_time_stop_h * 3_600_000)
                if capped_exit < exit_ts:
                    exit_ts = capped_exit
                    early_exit = True
                    n_stacked_early_exits += 1

            open_pos.append({"id": id(t), "exit_ts": exit_ts, "margin": margin,
                             "side": t["side"], "strike": t["strike"], "qty": qty,
                             "pnl_dollars": pnl_dollars, "pnl_pct": t["pnl_pct"],
                             "trade_ref": t, "early_exit": early_exit})
            last_side_entry_ts[t["side"]] = t["ts"]
            last_side_entry_px[t["side"]] = t["close_spot"]
            n_taken += 1

    losing_months = sum(1 for v in monthly.values() if v < 0)
    return {
        "n_taken": n_taken, "final": round(equity, 2),
        "return_pct": round((equity - START) / START * 100, 1),
        "max_dd": round(max_dd * 100, 1),
        "blocked": {"cap": n_blocked_cap, "margin": n_blocked_margin, "cb": n_blocked_cb,
                    "cluster_stops": n_cluster_stops, "stacked_early": n_stacked_early_exits},
        "losing_months": losing_months, "total_months": len(monthly),
        "worst_month": round(min(monthly.values()), 1) if monthly else 0.0,
        "by_side_n": {sd: len(v) for sd, v in by_side.items()},
        "by_side_pnl": {sd: round(sum(v), 1) for sd, v in by_side.items()},
    }


def _empty_result():
    return {"n_taken": 0, "final": START, "return_pct": 0.0, "max_dd": 0.0,
            "blocked": {}, "losing_months": 0, "total_months": 0, "worst_month": 0.0,
            "by_side_n": {}, "by_side_pnl": {}}


def _row(label: str, m: dict) -> str:
    b = m["blocked"]
    return (f"  {label:<10} n={m['n_taken']:>4} final=${m['final']:>9,.2f} "
            f"ret={m['return_pct']:>+7.1f}% maxDD={m['max_dd']:>5.1f}% calmar={calmar(m):>+7.1f} "
            f"losM={m['losing_months']}/{m['total_months']} worstM=${m['worst_month']:>+7.1f} "
            f"cstop={b.get('cluster_stops',0)} stackedEarly={b.get('stacked_early',0)}")


# ─────────────────────── grid + parallel runner ───────────────────────

_G = {}


def _init_worker(k5, k1h, records):
    _G["k5"] = k5
    _G["k1h"] = k1h
    _G["records"] = records


def _run_one(label_and_kwargs):
    label, mv_kwargs, replay_kwargs = label_and_kwargs
    k5 = _G["k5"]
    k1h = _G["k1h"]
    records = _G["records"]

    events = events_for_cooldown(records, LIVE_COOLDOWN_BARS)
    if mv_kwargs:
        events = events_with_momentum_veto(events, k5, **mv_kwargs)
    trades = build_dollar_trades(events, k5, k1h)
    spot_lookup = SpotLookup(k5)
    # build_dollar_trades doesn't carry the entry spot through (only the
    # rounded strike) — attach it via the same grid lookup adaptive_decay
    # needs to measure adverse price moves between stacked entries.
    for t in trades:
        t["close_spot"] = spot_lookup.at(t["ts"])
    trades.sort(key=lambda t: t["ts"])

    train, hold = split_trades(trades)
    full_m = replay_v2(trades, spot_lookup, **replay_kwargs)
    train_m = replay_v2(train, spot_lookup, **replay_kwargs)
    hold_m = replay_v2(hold, spot_lookup, **replay_kwargs)
    quarters = [replay_v2(q, spot_lookup, **replay_kwargs) for q in quarter_splits(trades, 4)]
    return label, mv_kwargs, replay_kwargs, full_m, train_m, hold_m, quarters


def build_grid() -> list[tuple[str, dict, dict]]:
    grid: list[tuple[str, dict, dict]] = [("baseline", {}, {})]

    # ── idea 1a: cluster stop, whole-cluster close, FINE grid around the
    # round-2 winner (0.75) — round 2 only tested {0.3,0.5,0.75,1.0} and
    # found non-monotonic behavior, so the true optimum could be anywhere
    # in 0.40-1.10. ──
    for frac in [round(0.40 + 0.05 * i, 2) for i in range(15)]:  # 0.40..1.10
        grid.append((f"cstop={frac:.2f}", {}, {"cluster_stop_frac": frac}))

    # ── idea 1b: cluster stop, CLOSE-WORST-LEG-ONLY variant — never tested
    # in round 2. Less destructive per trigger (only 1 leg realized, not the
    # whole cluster), so the useful threshold range may sit lower. ──
    for frac in [round(0.20 + 0.10 * i, 2) for i in range(9)]:  # 0.20..1.00
        grid.append((f"cstop_worst={frac:.2f}", {},
                    {"cluster_stop_frac": frac, "cluster_stop_worst_leg_only": True}))

    # ── idea 1c: cluster stop, SIDE-SPECIFIC — Calls already have a tight
    # dollar-margin SL (~0.75x credit) and a 24h hold; Puts have a much
    # looser 2.0x-credit SL and a 96h hold, so they sit underwater longer
    # before their own SL trips. The whole-cluster benefit may be driven by
    # one side only — test applying the threshold to just one side. ──
    for frac in (0.60, 0.70, 0.75, 0.80, 0.90):
        grid.append((f"cstop_Conly={frac:.2f}", {}, {"cluster_stop_frac": {"C": frac, "P": None}}))
        grid.append((f"cstop_Ponly={frac:.2f}", {}, {"cluster_stop_frac": {"P": frac, "C": None}}))

    # ── idea 2: momentum veto, WIDE grid (round 2 only covered 1.0-2.5% /
    # 60-120min) — extend both the move-size threshold and the lookback
    # window in both directions. ──
    for pct in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        for bars in (12, 24, 36):  # 60min, 120min, 180min
            grid.append((f"mveto={pct:.1f}%/{bars*5}m",
                        {"pct_threshold": pct, "bars": bars}, {}))
    for bars in (6, 48):  # 30min, 240min — extremes not covered above
        for pct in (1.5, 2.0):
            grid.append((f"mveto={pct:.1f}%/{bars*5}m",
                        {"pct_threshold": pct, "bars": bars}, {}))

    # ── idea 3: adaptive decay, WIDE grid (round 2 only covered decay
    # 0.3-0.5 / adverse 1.0-2.0%) — extend decay more aggressive (0.1-0.2)
    # and the adverse-move trigger more sensitive (0.5%). ──
    for decay in (0.1, 0.2, 0.3, 0.5, 0.7):
        for adverse in (0.5, 0.75, 1.0, 1.5, 2.0):
            grid.append((f"adecay={decay:.1f}@{adverse:.2f}%", {},
                        {"adaptive_decay": (decay, adverse)}))

    # ── idea 4: stacked time-stop — round 2 found 6/8/12/16h all hurt
    # (monotonically, by freeing MAX_OPEN slots faster -> more low-quality
    # throughput). Extend toward baseline (18/20h) to confirm the hurt
    # shrinks monotonically rather than there being a sweet spot we missed. ──
    for h in (4, 6, 8, 12, 16, 18, 20, 22):
        grid.append((f"stt={h}h", {}, {"stacked_time_stop_h": float(h)}))

    # ── stage B: combinations of the most promising region from each
    # surviving idea (cluster_stop 0.70-0.85, momentum_veto ~1.5-2.0%/60-120m,
    # adaptive_decay ~0.3-0.5@1.0%) — pairwise and triple. ──
    cstop_top = (0.70, 0.75, 0.80, 0.85)
    mveto_top = ((1.5, 12), (2.0, 12), (2.0, 24))
    adecay_top = ((0.5, 1.0), (0.3, 1.0), (0.5, 0.75))

    for frac in cstop_top:
        for pct, bars in mveto_top:
            grid.append((f"cstop={frac:.2f}+mveto={pct:.1f}%/{bars*5}m",
                        {"pct_threshold": pct, "bars": bars}, {"cluster_stop_frac": frac}))
    for frac in cstop_top:
        for decay, adverse in adecay_top:
            grid.append((f"cstop={frac:.2f}+adecay={decay:.1f}@{adverse:.2f}%", {},
                        {"cluster_stop_frac": frac, "adaptive_decay": (decay, adverse)}))
    for pct, bars in mveto_top:
        for decay, adverse in adecay_top:
            grid.append((f"mveto={pct:.1f}%/{bars*5}m+adecay={decay:.1f}@{adverse:.2f}%",
                        {"pct_threshold": pct, "bars": bars},
                        {"adaptive_decay": (decay, adverse)}))
    for frac in (0.75, 0.80):
        for pct, bars in mveto_top:
            for decay, adverse in adecay_top:
                grid.append((f"cstop={frac:.2f}+mveto={pct:.1f}%/{bars*5}m+adecay={decay:.1f}@{adverse:.2f}%",
                            {"pct_threshold": pct, "bars": bars},
                            {"cluster_stop_frac": frac, "adaptive_decay": (decay, adverse)}))

    # ── stage C: cstop_worst=0.40 was the standout of the whole grid — the
    # ONLY candidate with a positive HOLDOUT return, better maxDD in every
    # quarter — but it was missing from stage B's combos entirely (those only
    # crossed the whole-cluster cstop variant). Fine-grid around 0.40 plus
    # combos with the other surviving ideas. ──
    for frac in (0.32, 0.35, 0.38, 0.40, 0.42, 0.45, 0.48):
        grid.append((f"cworst={frac:.2f}", {},
                    {"cluster_stop_frac": frac, "cluster_stop_worst_leg_only": True}))
    for frac in (0.35, 0.40, 0.45):
        for pct, bars in mveto_top:
            grid.append((f"cworst={frac:.2f}+mveto={pct:.1f}%/{bars*5}m",
                        {"pct_threshold": pct, "bars": bars},
                        {"cluster_stop_frac": frac, "cluster_stop_worst_leg_only": True}))
    for frac in (0.35, 0.40, 0.45):
        for decay, adverse in adecay_top:
            grid.append((f"cworst={frac:.2f}+adecay={decay:.1f}@{adverse:.2f}%", {},
                        {"cluster_stop_frac": frac, "cluster_stop_worst_leg_only": True,
                         "adaptive_decay": (decay, adverse)}))
    for frac in (0.40,):
        for pct, bars in mveto_top:
            for decay, adverse in adecay_top:
                grid.append((f"cworst={frac:.2f}+mveto={pct:.1f}%/{bars*5}m+adecay={decay:.1f}@{adverse:.2f}%",
                            {"pct_threshold": pct, "bars": bars},
                            {"cluster_stop_frac": frac, "cluster_stop_worst_leg_only": True,
                             "adaptive_decay": (decay, adverse)}))
    # worst-leg restricted to Put-only (the side driving the whole-cluster
    # benefit too) at the promising 0.40 region
    for frac in (0.35, 0.40, 0.45):
        grid.append((f"cworst_Ponly={frac:.2f}", {},
                    {"cluster_stop_frac": {"P": frac, "C": None}, "cluster_stop_worst_leg_only": True}))

    return grid


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_local(data_dir)
    records = pickle.loads((data_dir / "sniper_persistence_records_cache.pkl").read_bytes())
    print(f"klines: 5m={len(k5):,}  cached windows: {len(records):,}  START=${START:.0f}", flush=True)

    grid = build_grid()
    print(f"grid size: {len(grid)} candidates, running on {mp.cpu_count()} cores\n", flush=True)

    ctx = mp.get_context("fork")
    with ctx.Pool(processes=mp.cpu_count(), initializer=_init_worker,
                  initargs=(k5, k1h, records)) as pool:
        results = pool.map(_run_one, grid)

    results.sort(key=lambda r: calmar(r[4]), reverse=True)

    print("=" * 150)
    print("Ranked by TRAIN calmar (return%/maxDD%) — selection happens here, holdout is confirmation only")
    print("=" * 150)
    for label, mv_kwargs, replay_kwargs, full_m, train_m, hold_m, quarters in results:
        print(f"\n{label}  mv={mv_kwargs} replay={replay_kwargs}")
        print(_row("FULL", full_m))
        print(_row("TRAIN", train_m))
        print(_row("HOLDOUT", hold_m))

    print("\n" + "=" * 150)
    print("Walk-forward (top 10 by train calmar) — 4 contiguous quarters, fresh $START each")
    print("=" * 150)
    for label, mv_kwargs, replay_kwargs, full_m, train_m, hold_m, quarters in results[:10]:
        print(f"\n{label}")
        for qi, qm in enumerate(quarters):
            print(_row(f"Q{qi+1}", qm))

    print(f"\nelapsed {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

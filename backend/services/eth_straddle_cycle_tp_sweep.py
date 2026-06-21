"""ETH-specific CYCLE_H x TP2 sweep for the mechanical dollar-margin-stop
straddle (see ETH_STRADDLE_RESEARCH_HANDOFF.md). The BTC handoff locked
CYCLE_H=24/TP2=0.80 as a starting point copied from BTC's own sweep — this
re-sweeps both directly on ETH instead of assuming they transfer.

SL_DOLLAR_FRAC is held fixed at 0.3 (ETH's own leg-level Sharpe-optimum,
already found in btc_straddle_dollar_stop.py eth) to keep the grid 2D instead
of 3D; re-confirm SL_DOLLAR_FRAC at the winning (cycle_h, tp2) afterward if it
moved far from 24h/0.80.

Selection discipline: pick by TRAIN-only Sharpe, report HOLDOUT for that pick
(never peek at holdout during selection) — same as btc_straddle_sweep.py.

Run: cd backend && python3 services/eth_straddle_cycle_tp_sweep.py [coin] [days_back]
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.btc_straddle_sweep import build_periodic_signals
from services.btc_straddle_dollar_stop import (
    simulate_leg_dollar_stop, trailing_sigma, nearest_1h_idx,
)

COIN = sys.argv[1] if len(sys.argv) > 1 else "eth"
DAYS_BACK = float(sys.argv[2]) if len(sys.argv) > 2 else 1095.0
SL_DOLLAR_FRAC = 0.3
CYCLE_HOURS = (12.0, 24.0, 48.0, 72.0, 168.0)
TP2_VALUES = (0.50, 0.65, 0.80, 0.90)
TRAIN_FRAC = 0.70


def agg(vals):
    n = len(vals)
    if n == 0:
        return "n 0"
    avg = sum(vals) / n
    sd = st.stdev(vals) if n > 1 else 0
    sh = avg / sd if sd > 0 else 0
    worst = min(vals)
    return f"n{n:>4} avg{avg:>+6.2f}% Sh{sh:>+5.2f}  worst{worst:>+7.1f}%"


def run_combo(k5, k1h, cycle_h, tp2):
    sigs = build_periodic_signals(k5, cycle_h)
    cycles_by_idx = {}
    for s in sigs:
        cycles_by_idx.setdefault(s["_cycle"], {})[s["side"]] = s["idx_5m"]

    rows = []
    for cycle_idx, legs in sorted(cycles_by_idx.items()):
        if "C" not in legs or "P" not in legs:
            continue
        idx_1h = nearest_1h_idx(k1h, k5[cycle_idx]["start_ms"])
        if idx_1h is None:
            continue
        sigma = trailing_sigma(k1h, idx_1h)
        if sigma is None:
            continue
        legres = {}
        for side in ("C", "P"):
            r = simulate_leg_dollar_stop(side, legs[side], k5, sigma, cycle_h, tp2, SL_DOLLAR_FRAC)
            if r is None:
                legres = None
                break
            legres[side] = r
        if not legres:
            continue
        tot_pnl = legres["C"]["pnl_dollars"] + legres["P"]["pnl_dollars"]
        tot_margin = legres["C"]["margin"] + legres["P"]["margin"]
        pct = tot_pnl / tot_margin * 100 if tot_margin else 0.0
        rows.append((k5[cycle_idx]["start_ms"], pct))
    return rows


def main():
    k5, _, k1h = load_coin(COIN, find_data_dir(None))
    if DAYS_BACK:
        cutoff = k5[-1]["start_ms"] - int(DAYS_BACK * 86_400_000)
        k5 = [c for c in k5 if c["start_ms"] >= cutoff]
        k1h = [c for c in k1h if c["start_ms"] >= cutoff]

    print(f"{COIN.upper()} CYCLE_H x TP2 sweep (sl_dollar_frac={SL_DOLLAR_FRAC} fixed)\n")
    print(f"{'cycle_h':>8} {'tp2':>6}   {'OVERALL (% of margin)':<40}")

    results = {}  # (cycle_h, tp2) -> rows
    for cycle_h in CYCLE_HOURS:
        for tp2 in TP2_VALUES:
            rows = run_combo(k5, k1h, cycle_h, tp2)
            if not rows:
                continue
            pcts = [p for _, p in rows]
            print(f"{cycle_h:>8.0f} {tp2:>6.2f}   {agg(pcts)}")
            results[(cycle_h, tp2)] = rows

    if not results:
        print("no valid configs")
        return

    # TRAIN-only selection (never peek at holdout)
    ts_all = sorted(t for rows in results.values() for t, _ in rows)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])

    best = None
    for (cycle_h, tp2), rows in results.items():
        tr = [p for t, p in rows if t < split_ts]
        sd = st.stdev(tr) if len(tr) > 1 else 0
        sh = (sum(tr) / len(tr)) / sd if tr and sd > 0 else -999
        if best is None or sh > best[1]:
            best = ((cycle_h, tp2), sh, rows)

    (cycle_h, tp2), train_sh, rows = best
    tr = [p for t, p in rows if t < split_ts]
    ho = [p for t, p in rows if t >= split_ts]
    print(f"\n=== BEST cycle_h={cycle_h:.0f} tp2={tp2:.2f} (by TRAIN Sharpe={train_sh:+.2f}) ===")
    print(f"TRAIN   {agg(tr)}")
    print(f"HOLDOUT {agg(ho)}")


if __name__ == "__main__":
    main()

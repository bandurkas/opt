#!/usr/bin/env python3
"""SL-level sweep on a full year, priced on REAL DVOL, OOS-honest.

Task: the live CALL stop is sl_pct=0.75 (exit when the short option's mark loses
75% of the credit = live `entry_credit * 1.75`). User asks: is a SLIGHTLY WIDER
stop statistically better — fewer whipsaw stop-outs when the signal is ultimately
right (sideways/down) — or does it just deepen the tail losses?

Method: sweep sl_pct over a grid for each side, holding the rest of the LIVE config
fixed (CALL_GEN_KWARGS/CALL_EXIT, PUT_GEN_KWARGS/PUT_EXIT). Premium path priced on
REAL Deribit DVOL (monkeypatch realized_vol_at_idx_1h, identical to realiv_rerun /
realiv_mixed — no edits to the validated engine). Chronological 65/35 train/holdout.
Report per sl_pct: n, avg%, total%, SL-rate, win-rate, and maxDD on the cumulative
%-PnL curve. Pick by HOLDOUT (avoid the train-only mirage).

Wider sl_pct = looser stop: fewer "sl" exits, but a non-stopped loser resolves at
time-stop and can lose MORE than the old floor. The engine captures both, so the
net is honest. We also sweep the CALL transition-only regime (the realiv_improve /
realiv_mixed OOS win) so SL and regime are co-checked, not optimized in isolation.
"""
import argparse
import json
import os
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.backtest_data import load_local_set                 # noqa: E402
from services.backtest import simulate_signal_set                 # noqa: E402
from services.option_futures_complement import gen_parallel        # noqa: E402
from services.strategy_config import (CALL_GEN_KWARGS, CALL_EXIT,   # noqa: E402
                                      PUT_GEN_KWARGS, PUT_EXIT)
import services.indicators as ind                                  # noqa: E402
from multiprocessing import cpu_count                              # noqa: E402

DATA = Path(os.path.expanduser("~/Desktop/options/data"))
_orig_rv = ind.realized_vol_at_idx_1h
DVOL_IV = []


def _patched_rv(closes, i, lookback_h=168):
    if 0 <= i < len(DVOL_IV) and DVOL_IV[i] is not None:
        return DVOL_IV[i]
    return _orig_rv(closes, i, lookback_h)


def maxdd(pnls):
    """maxDD (most negative) on the cumulative %-PnL curve, in % points."""
    peak = 0.0; c = 0.0; dd = 0.0
    for p in pnls:
        c += p; peak = max(peak, c); dd = min(dd, c - peak)
    return dd


def summ(label, rows):
    if not rows:
        print(f"      {label:9} n=0"); return
    p = [r["pnl"] for r in rows]
    sl = 100 * sum(r["sl"] for r in rows) / len(rows)
    print(f"      {label:9} n={len(rows):4} | avg {st.fmean(p):+6.2f}% | total {sum(p):+8.1f}% | "
          f"SL {sl:4.1f}% | win {100*sum(x>0 for x in p)/len(rows):4.1f}% | maxDD {maxdd(p):+7.1f}%")


def sweep_side(name, signals, k5, k1h, exit_kw, expiry_h, sl_grid, split):
    print(f"\n========== {name}  (expiry {expiry_h}h, tp {exit_kw['tp1_pct']}/{exit_kw['tp2_pct']}, "
          f"live sl_pct={exit_kw['sl_pct']}) ==========")
    for sl in sl_grid:
        sims = simulate_signal_set(signals, k5, sigma=0.6, expiry_hours=float(expiry_h),
                                   tp1_pct=exit_kw["tp1_pct"], tp2_pct=exit_kw["tp2_pct"],
                                   sl_pct=sl, option_horizon_h=exit_kw["hold_h"],
                                   spread_pct=2.0, klines_1h=k1h, dynamic_sigma=True,
                                   iv_rv_multiplier=1.0, sigma_clamp=(0.05, 3.0))
        rows = []
        for s in sims:
            opt = s.get("option", {})
            if "pnl_pct" not in opt:
                continue
            rows.append({"idx": s["idx_5m"], "pnl": opt["pnl_pct"],
                         "sl": 1 if opt.get("resolution") == "sl" else 0})
        rows.sort(key=lambda r: r["idx"])
        tr = [r for r in rows if r["idx"] < split]
        ho = [r for r in rows if r["idx"] >= split]
        star = "  <== LIVE" if abs(sl - exit_kw["sl_pct"]) < 1e-9 else ""
        print(f"  --- sl_pct = {sl:.3f}{star} ---")
        summ("TRAIN", tr)
        summ("HOLDOUT", ho)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=["call", "put", "both"], default="both")
    ap.add_argument("--call-transition-only", action="store_true",
                    help="use CALL regime_filter=['transition'] (realiv_improve OOS win)")
    ap.add_argument("--train-frac", type=float, default=0.65)
    args = ap.parse_args()

    ncore = cpu_count()
    print(f"[1] klines + parallel gen ({ncore} cores)...")
    d = load_local_set(DATA); k5, k15, k1h = d["5"], d["15"], d["60"]
    k1h = sorted(k1h, key=lambda c: c["start_ms"])

    dvol = json.loads((DATA / "eth_dvol_1h.json").read_text())
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    global DVOL_IV
    DVOL_IV = [iv_at.get(c["start_ms"] // 3_600_000) for c in k1h]
    cov = 100 * sum(x is not None for x in DVOL_IV) / len(DVOL_IV)
    ind.realized_vol_at_idx_1h = _patched_rv   # price on real IV
    split = int(len(k5) * args.train_frac)
    print(f"    DVOL coverage {cov:.0f}% | {len(k5)} 5m bars | split idx {split} ({args.train_frac:.0%})")

    call_sl_grid = [0.50, 0.625, 0.75, 0.875, 1.00, 1.25, 1.50, 2.00]
    put_sl_grid = [0.75, 1.00, 1.25, 1.50, 1.75, 2.00, 2.50]

    if args.side in ("call", "both"):
        cg = dict(CALL_GEN_KWARGS)
        if args.call_transition_only:
            cg["regime_filter"] = ["transition"]
        calls = gen_parallel(k5, k15, k1h, cg, ncore)
        label = f"CALL  regime={cg['regime_filter']}"
        print(f"\n[2] {len(calls)} call signals  ({label})")
        sweep_side(label, calls, k5, k1h, CALL_EXIT, 24, call_sl_grid, split)

    if args.side in ("put", "both"):
        puts = gen_parallel(k5, k15, k1h, PUT_GEN_KWARGS, ncore)
        label = f"PUT   regime={PUT_GEN_KWARGS['regime_filter']}"
        print(f"\n[2] {len(puts)} put signals  ({label})")
        sweep_side(label, puts, k5, k1h, PUT_EXIT, 168, put_sl_grid, split)

    print("\nPick the sl_pct with best HOLDOUT avg/total & least-bad maxDD. Wider is only "
          "better if HOLDOUT total rises (fewer whipsaws) WITHOUT maxDD blowing out (deeper tails).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""OOS test of the SL-predictor gate, two monetizations, pick the best.

From sl_predictor: SL is predictable at entry by VRP (IV-RV) and IV level (causal,
theory-consistent). Here we LEARN a gate threshold on TRAIN only, apply to HOLDOUT,
and compare against the unfiltered validated short-CALL strategy:

  A (SKIP)  : don't take trades the gate flags as likely-SL  -> lifts options directly
  B (HEDGE) : take all, add a long perp ONLY on flagged trades -> capture the breakout

Gates tested (threshold from train):
  vrp<=0        : IV not above RV (no fitting)
  vrp<=trainQ1  : bottom-quartile variance premium (cutoff learned on train)
  vrp<=0|range  : thin VRP OR range regime (range was weak in-sample)

Honest: chronological split, BS credit, perp costs (4 fills) + funding, maxDD.
Pick by HOLDOUT total $ and maxDD.
"""
import argparse
import os
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.backtest_data import load_local_set                       # noqa: E402
from services.backtest import simulate_signal_set                       # noqa: E402
from services import backtest_bs as bs                                  # noqa: E402
from services.option_futures_complement import (gen_parallel, WINNER_GEN,  # noqa: E402
                                                WINNER_EXIT, fund_lookup, perp_pnl)
from services.sl_predictor import dvol_rv_context                       # noqa: E402
from multiprocessing import cpu_count                                   # noqa: E402

DATA = Path(os.path.expanduser("~/Desktop/options/data"))
T0 = 168.0 / 8760.0


def maxdd(curve):
    peak = curve[0] if curve else 0.0; dd = 0.0
    for v in curve:
        peak = max(peak, v); dd = min(dd, v - peak)
    return dd


def stats(rows, pnl_key):
    if not rows:
        return (0.0, 0.0, 0.0, 0)
    c, curve = 0.0, []
    for r in rows:
        c += pnl_key(r); curve.append(c)
    sl = 100 * sum(r["sl"] for r in rows) / len(rows)
    return (sum(pnl_key(r) for r in rows), maxdd(curve), sl, len(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=float, default=1.0, help="perp ETH per option ETH for HEDGE")
    ap.add_argument("--contracts", type=float, default=0.3)
    ap.add_argument("--train-frac", type=float, default=0.65)
    args = ap.parse_args()

    ncore = cpu_count()
    print(f"[1] klines + parallel gen ({ncore} cores)...")
    d = load_local_set(DATA); k5, k15, k1h = d["5"], d["15"], d["60"]
    signals = gen_parallel(k5, k15, k1h, WINNER_GEN, ncore)
    sims = simulate_signal_set(signals, k5, sigma=0.6, expiry_hours=168.0,
                               tp1_pct=WINNER_EXIT["tp1"], tp2_pct=WINNER_EXIT["tp2"],
                               sl_pct=WINNER_EXIT["sl"], option_horizon_h=WINNER_EXIT["hold_h"],
                               spread_pct=2.0, tsl_trigger_pct=0.0, tsl_offset_pct=0.0)
    iv_at, rv_at = dvol_rv_context()
    fund_f = fund_lookup()
    ts5 = [c["start_ms"] + 5 * 60 * 1000 for c in k5]
    px5 = [float(c["close"]) for c in k5]
    idx = {t: i for i, t in enumerate(ts5)}

    rows = []
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt or opt.get("bars_held") is None or s["ts_ms"] not in idx:
            continue
        ke = idx[s["ts_ms"]]; kx = min(ke + int(opt["bars_held"]), len(px5) - 1)
        if kx <= ke:
            continue
        hb = s["ts_ms"] // 3_600_000
        iv, rv = iv_at.get(hb), rv_at.get(hb)
        if iv is None or rv is None:
            continue
        credit = bs.price("C", s["close"], round(s["close"] / 25) * 25, T0, 0.6) * args.contracts
        rows.append({
            "ke": ke, "kx": kx, "opt": (opt["pnl_pct"] / 100.0) * credit,
            "sl": 1 if opt.get("resolution") == "sl" else 0,
            "vrp": iv - rv, "iv": iv, "regime": s.get("regime"),
            "perp": perp_pnl(px5, ke, kx, args.contracts, args.h, fund_f, ts5, trig=None),
        })

    rows.sort(key=lambda r: r["ke"])
    n = len(rows); sp = int(n * args.train_frac)
    train, hold = rows[:sp], rows[sp:]
    print(f"[2] {n} trades | train {len(train)} / holdout {len(hold)} | h(perp)={args.h}\n")

    tr_vrp = sorted(r["vrp"] for r in train)
    q1 = tr_vrp[len(tr_vrp) // 4]
    gates = {
        "vrp<=0":        lambda r: r["vrp"] <= 0,
        f"vrp<=Q1({q1:.3f})": lambda r: r["vrp"] <= q1,
        "vrp<=0|range":  lambda r: r["vrp"] <= 0 or r["regime"] == "range",
    }

    def report(name, rows_):
        base = stats(rows_, lambda r: r["opt"])
        print(f"  [{name}] BASELINE all:  total ${base[0]:+8.2f} | maxDD ${base[1]:8.2f} | SL {base[2]:4.1f}% | n={base[3]}")
        for gname, g in gates.items():
            kept = [r for r in rows_ if not g(r)]            # A: skip flagged
            flagged = [r for r in rows_ if g(r)]
            a = stats(kept, lambda r: r["opt"])
            b = stats(rows_, lambda r: r["opt"] + (r["perp"] if g(r) else 0.0))  # B: hedge flagged
            print(f"     gate {gname:16} flagged {len(flagged):4} "
                  f"| A-skip ${a[0]:+8.2f} maxDD ${a[1]:8.2f} SL {a[2]:4.1f}% n={a[3]:4} "
                  f"| B-hedge ${b[0]:+8.2f} maxDD ${b[1]:8.2f}")

    report("TRAIN", train)
    print()
    report("HOLDOUT", hold)
    print("\nPick by HOLDOUT: higher total $ and smaller |maxDD| than BASELINE, with SL-rate down (A).")


if __name__ == "__main__":
    main()

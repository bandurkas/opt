#!/usr/bin/env python3
"""Round 2: try to ENLARGE the validated edge, priced on REAL DVOL, OOS-honest.

Round 1 (realiv_rerun): under real IV the short-CALL edge is ~+4.5%/trade, stable
train~holdout. Here we test whether dropping weak sub-populations raises avg PnL &
cuts SL WITHOUT collapsing total, validated on holdout (thresholds learned on train):
  - skip regime=range (was ~-0.4% avg, higher SL)
  - skip thin VRP=IV-RV (train bottom quartile; 39% SL in-sample)
  - both
Real IV injected via monkeypatch (same as realiv_rerun). Reports train & holdout.
"""
import json
import math
import os
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.backtest_data import load_local_set                 # noqa: E402
from services.backtest import simulate_signal_set                 # noqa: E402
from services.option_futures_complement import (gen_parallel, WINNER_GEN,  # noqa: E402
                                                WINNER_EXIT)
from services.sl_predictor import dvol_rv_context                 # noqa: E402
import services.indicators as ind                                 # noqa: E402
from multiprocessing import cpu_count                             # noqa: E402

DATA = Path(os.path.expanduser("~/Desktop/options/data"))
_orig_rv = ind.realized_vol_at_idx_1h
DVOL_IV = []


def _patched_rv(closes, i, lookback_h=168):
    if 0 <= i < len(DVOL_IV) and DVOL_IV[i] is not None:
        return DVOL_IV[i]
    return _orig_rv(closes, i, lookback_h)


def summ(label, rows):
    if not rows:
        print(f"    {label:22} n=0"); return
    pnls = [r["pnl"] for r in rows]
    sl = 100 * sum(r["sl"] for r in rows) / len(rows)
    print(f"    {label:22} n={len(rows):4} | avg {st.fmean(pnls):+6.2f}% | total {sum(pnls):+8.1f}% | "
          f"SL {sl:4.1f}% | win {100*sum(p>0 for p in pnls)/len(rows):4.1f}%")


def main():
    ncore = cpu_count()
    print(f"[1] klines + parallel gen ({ncore} cores)...")
    d = load_local_set(DATA); k5, k15, k1h = d["5"], d["15"], d["60"]
    k1h = sorted(k1h, key=lambda c: c["start_ms"])
    signals = gen_parallel(k5, k15, k1h, WINNER_GEN, ncore)

    dvol = json.loads((DATA / "eth_dvol_1h.json").read_text())
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    global DVOL_IV
    DVOL_IV = [iv_at.get(c["start_ms"] // 3_600_000) for c in k1h]
    ind.realized_vol_at_idx_1h = _patched_rv      # price on real IV

    _, rv_at = dvol_rv_context()
    sims = simulate_signal_set(signals, k5, sigma=0.6, expiry_hours=168.0,
                               tp1_pct=WINNER_EXIT["tp1"], tp2_pct=WINNER_EXIT["tp2"],
                               sl_pct=WINNER_EXIT["sl"], option_horizon_h=WINNER_EXIT["hold_h"],
                               spread_pct=2.0, klines_1h=k1h, dynamic_sigma=True,
                               iv_rv_multiplier=1.0, sigma_clamp=(0.05, 3.0))
    rows = []
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt:
            continue
        hb = s["ts_ms"] // 3_600_000
        iv, rv = iv_at.get(hb), rv_at.get(hb)
        rows.append({"idx": s["idx_5m"], "pnl": opt["pnl_pct"],
                     "sl": 1 if opt.get("resolution") == "sl" else 0,
                     "regime": s.get("regime"),
                     "vrp": (iv - rv) if (iv is not None and rv is not None) else None})
    rows.sort(key=lambda r: r["idx"])
    split = int(len(k5) * 0.65)
    tr = [r for r in rows if r["idx"] < split]
    ho = [r for r in rows if r["idx"] >= split]
    vrps = sorted(r["vrp"] for r in tr if r["vrp"] is not None)
    q1 = vrps[len(vrps) // 4]
    print(f"[2] {len(rows)} trades | thin-VRP cutoff (train Q1) = {q1:.3f}\n")

    filters = {
        "baseline (all)":        lambda r: True,
        "skip range":            lambda r: r["regime"] != "range",
        "skip thin-VRP":         lambda r: r["vrp"] is not None and r["vrp"] > q1,
        "skip range+thin-VRP":   lambda r: r["regime"] != "range" and r["vrp"] is not None and r["vrp"] > q1,
    }
    for fname, f in filters.items():
        print(f"  --- {fname} ---")
        summ("TRAIN", [r for r in tr if f(r)])
        summ("HOLDOUT", [r for r in ho if f(r)])
    print("\nWin = higher HOLDOUT avg & lower SL than baseline WITHOUT total collapsing (kept enough trades).")


if __name__ == "__main__":
    main()

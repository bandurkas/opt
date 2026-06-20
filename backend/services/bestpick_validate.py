#!/usr/bin/env python3
"""Item 1 — validate BESTPICK (richest-in-cluster slot admission) to deploy-readiness.

In-sample it lifted FINAL $695->$797 at flat maxDD/trade-count. Before trusting it:
  (A) MECHANISM: does higher sigma (richness) actually mean higher avg pnl? (else luck)
  (B) OOS: run CHRONO vs BESTPICK on TRAIN and HOLDOUT segments SEPARATELY (fresh equity).
  (C) ROBUSTNESS: cluster window in {1h, 2h, 4h} — is the gain stable, not a tuned knob?
Live baseline engine ($400 / MO4 / MP.15), real DVOL. No deploy — this is the test gate.

Run:  PYTHONPATH=. python3 services/bestpick_validate.py
"""
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import services.variant_backtest as vb
from services.iv_mixed_deposit import build_trades, TRAIN_FRAC
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
import services.indicators as ind
from services.strategy_config import PUT_EXIT, CALL_EXIT
import services.cluster_bestpick as cb   # reuse the ts-faithful engine (baseline $400/MO4)

_orig_rv = ind.realized_vol_at_idx_1h
DVOL_IV = []


def _patched_rv(closes, i, lookback_h=168):
    if 0 <= i < len(DVOL_IV) and DVOL_IV[i] is not None:
        return DVOL_IV[i]
    return _orig_rv(closes, i, lookback_h)


CFG = {
    "P": {"tp1": PUT_EXIT["tp1_pct"], "tp2": PUT_EXIT["tp2_pct"], "sl": PUT_EXIT["sl_pct"],
          "hold": PUT_EXIT["hold_h"], "expiry": 168.0},
    "C": {"tp1": CALL_EXIT["tp1_pct"], "tp2": CALL_EXIT["tp2_pct"], "sl": CALL_EXIT["sl_pct"],
          "hold": CALL_EXIT["hold_h"], "expiry": 24.0},
}


def sigma_pnl_monotonicity(trades, label):
    ts = [t for t in trades if t.get("sigma")]
    ts.sort(key=lambda t: t["sigma"])
    q = len(ts) // 4
    print(f"  {label}: sigma quartiles -> avg pnl_pct")
    for i, name in enumerate(["Q1 low-σ", "Q2", "Q3", "Q4 high-σ"]):
        seg = ts[i * q:(i + 1) * q] if i < 3 else ts[3 * q:]
        avg = 100 * st.fmean(t["pnl_pct"] for t in seg)
        sig = st.fmean(t["sigma"] for t in seg)
        print(f"     {name:10} n={len(seg):4}  σ~{sig:.2f}  avg pnl {avg:+6.2f}%")


def order(trades, window_h):
    if window_h is None:                       # chronological
        return sorted(trades, key=lambda t: t["ts"])
    w = window_h * 3600 * 1000
    return sorted(trades, key=lambda t: (t["ts"] // w, -t.get("sigma", 0.0)))


def main():
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_coin("eth", data_dir)
    k1h = sorted(k1h, key=lambda c: c["start_ms"])
    dvol = json.loads((Path(data_dir) / "eth_dvol_1h.json").read_text())
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    global DVOL_IV
    DVOL_IV = [iv_at.get(c["start_ms"] // 3_600_000) for c in k1h]
    ind.realized_vol_at_idx_1h = _patched_rv
    cb.DVOL_IV = DVOL_IV                        # keep cb's pricing patch consistent

    vb.PUT_GEN_KWARGS = {**vb.PUT_GEN_KWARGS, "vol_threshold": 0.50}
    vb.CALL_GEN_KWARGS = {**vb.CALL_GEN_KWARGS, "vol_threshold": 0.60}
    sigs = vb.generate(k5, k15, k1h, variant="v3")
    p = [s for s in sigs if s["side"] == "P"]
    c = [s for s in sigs if s["side"] == "C"]
    trades = sorted(build_trades(p, k5, k1h, CFG["P"]) + build_trades(c, k5, k1h, CFG["C"]),
                    key=lambda t: t["ts"])
    ts0, ts1 = trades[0]["ts"], trades[-1]["ts"]
    split = ts0 + TRAIN_FRAC * (ts1 - ts0)
    train = [t for t in trades if t["ts"] < split]
    hold = [t for t in trades if t["ts"] >= split]
    print(f"trades={len(trades)} (train {len(train)} / holdout {len(hold)}) | engine $400/MO4\n")

    print("(A) MECHANISM — does richness (σ) predict pnl?")
    sigma_pnl_monotonicity(train, "TRAIN")
    sigma_pnl_monotonicity(hold, "HOLDOUT")

    print("\n(B/C) CHRONO vs BESTPICK by cluster window, TRAIN and HOLDOUT separately:")
    print(f"  {'order':16} | {'TRAIN taken/FINAL/ROI/DD':30} | {'HOLDOUT taken/FINAL/ROI/DD':30}")
    for win in (None, 1, 2, 4):
        name = "CHRONO" if win is None else f"BESTPICK {win}h"
        tt, te, td = cb.engine(train, order(train, win))
        ht, he, hd = cb.engine(hold, order(hold, win))
        print(f"  {name:16} | {tt:4} ${te:7,.0f} {(te/cb.START-1)*100:+5.0f}% DD{td:4.1f}% "
              f"        | {ht:4} ${he:7,.0f} {(he/cb.START-1)*100:+5.0f}% DD{hd:4.1f}%")
    print("\nPASS = BESTPICK beats CHRONO on HOLDOUT too, across windows, with monotone σ->pnl.")


if __name__ == "__main__":
    main()

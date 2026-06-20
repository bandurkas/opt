#!/usr/bin/env python3
"""Iteration 3 — does lowering the vol_threshold gate bank MORE $ on the real account
engine, or just clog margin slots?  ($400 / 15% margin / MAX_OPEN=4 / compound / CB).

Iter2 showed lowering vol_threshold lifts entry COUNT and flat-1% $/day with stable
train~holdout down to ~0.35-0.20. But flat-1% ignores margin-slot contention (puts hold
168h). This re-tests the candidates on iv_mixed_deposit.run_engine (the lens that decides
by FINAL $ + maxDD), pricing on REAL DVOL (same monkeypatch as iter1/2), live exits.

Per config we patch the per-side vol_threshold, regenerate (variant v3), build trades,
run the account engine, and report taken / blocked / FINAL $ / maxDD / holdout per-trade.

Run:  PYTHONPATH=. python3 services/vol_threshold_account.py
"""
import json
import os
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import services.variant_backtest as vb
from services.iv_mixed_deposit import build_trades, run_engine, TRAIN_FRAC
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
import services.indicators as ind
from services.strategy_config import PUT_EXIT, CALL_EXIT

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

# (label, put_vol_threshold, call_vol_threshold)
CONFIGS = [
    ("BASELINE live  P0.50/C0.60", 0.50, 0.60),
    ("CAND-A         P0.35/C0.45", 0.35, 0.45),
    ("CAND-B         P0.20/C0.30", 0.20, 0.30),
    ("CAND-C         P0.35/C0.35", 0.35, 0.35),
]


def holdout_avg(trades, split_ts):
    ho = [t["pnl_pct"] * 100 for t in trades if t["ts"] >= split_ts]
    tr = [t["pnl_pct"] * 100 for t in trades if t["ts"] < split_ts]
    return (st.fmean(tr) if tr else 0, len(tr), st.fmean(ho) if ho else 0, len(ho))


def main():
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_coin("eth", data_dir)
    k1h = sorted(k1h, key=lambda c: c["start_ms"])
    dvol = json.loads((Path(data_dir) / "eth_dvol_1h.json").read_text())
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    global DVOL_IV
    DVOL_IV = [iv_at.get(c["start_ms"] // 3_600_000) for c in k1h]
    ind.realized_vol_at_idx_1h = _patched_rv

    ts_span = (k5[0]["start_ms"], k5[-1]["start_ms"])
    split_ts = ts_span[0] + TRAIN_FRAC * (ts_span[1] - ts_span[0])
    span_days = (ts_span[1] - ts_span[0]) / 86_400_000

    summary = []
    for label, p_vt, c_vt in CONFIGS:
        vb.PUT_GEN_KWARGS = {**vb.PUT_GEN_KWARGS, "vol_threshold": p_vt}
        vb.CALL_GEN_KWARGS = {**vb.CALL_GEN_KWARGS, "vol_threshold": c_vt}
        sigs = vb.generate(k5, k15, k1h, variant="v3")
        p = [s for s in sigs if s["side"] == "P"]
        c = [s for s in sigs if s["side"] == "C"]
        trades = build_trades(p, k5, k1h, CFG["P"]) + build_trades(c, k5, k1h, CFG["C"])
        n_taken, equity, _ = run_engine(trades, f"### {label}  (raw P={len(p)} C={len(c)})")
        tr_a, tr_n, ho_a, ho_n = holdout_avg(trades, split_ts)
        summary.append((label, len(sigs), len(trades), n_taken, equity, tr_a, tr_n, ho_a, ho_n))

    print("\n" + "=" * 92)
    print(f"SUMMARY  ($400 start, MAX_OPEN=4, real DVOL, {span_days:.0f}d span)")
    print(f"{'config':30} {'raw':>5} {'trades':>6} {'taken':>5} {'FINAL$':>9} "
          f"{'TRavg':>7} {'HOavg':>7} {'HOn':>5}")
    base_eq = None
    for label, raw, ntr, taken, eq, tr_a, tr_n, ho_a, ho_n in summary:
        if base_eq is None:
            base_eq = eq
        d = f"{(eq/base_eq-1)*100:+.0f}%" if base_eq else ""
        print(f"{label:30} {raw:5} {ntr:6} {taken:5} ${eq:8,.0f} "
              f"{tr_a:+6.1f}% {ho_a:+6.1f}% {ho_n:5}  vs base {d}")
    print("\nWin = FINAL$ > baseline AND maxDD not worse AND holdout avg >= +1% AND TR~HO.")
    print("If 'taken' barely rises while raw jumps -> margin slots clogged (more entries don't bank).")


if __name__ == "__main__":
    main()

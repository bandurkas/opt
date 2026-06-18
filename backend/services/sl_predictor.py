#!/usr/bin/env python3
"""SL-predictor analysis for the validated short-CALL strategy.

Question: at ENTRY, can we tell which short-call trades will hit SL (price breaks
up through the strike) vs which will win? If yes, we either SKIP them (lifts the
options strategy directly: SL trades are the big bleed) or HEDGE only them with a
long perp (which we showed is +EV exactly on the SL subset).

Pure entry features (no look-ahead): regime, signal score, MTF alignment, realized
vol percentile, ADX score, bull ratio, plus the REAL implied-vol context from
Deribit DVOL and the variance premium VRP = IV - RV at entry. Label = (resolution
== 'sl'). Prints SL-rate + avg option PnL per feature bucket, and the lift vs base.
"""
import json
import math
import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.backtest_data import load_local_set                  # noqa: E402
from services.backtest import simulate_signal_set                  # noqa: E402
from services.option_futures_complement import (gen_parallel, WINNER_GEN,  # noqa: E402
                                                WINNER_EXIT)
from multiprocessing import cpu_count                              # noqa: E402

DATA = Path(os.path.expanduser("~/Desktop/options/data"))


def dvol_rv_context():
    """hour-bucket -> (iv_annual, rv_168h_annual) at that hour, causal."""
    dvol = json.loads((DATA / "eth_dvol_1h.json").read_text())
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    k1h = json.loads((DATA / "eth_1h.json").read_text()); k1h.sort(key=lambda c: c["start_ms"])
    closes = [float(c["close"]) for c in k1h]
    lr = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    rv_at = {}
    for i, c in enumerate(k1h):
        hb = c["start_ms"] // 3_600_000
        if i >= 168:
            rv_at[hb] = st.pstdev(lr[i - 168:i]) * math.sqrt(8760)
    return iv_at, rv_at


def main():
    ncore = cpu_count()
    print(f"[1] klines + parallel signal gen ({ncore} cores)...")
    d = load_local_set(DATA); k5, k15, k1h = d["5"], d["15"], d["60"]
    signals = gen_parallel(k5, k15, k1h, WINNER_GEN, ncore)
    sims = simulate_signal_set(signals, k5, sigma=0.6, expiry_hours=168.0,
                               tp1_pct=WINNER_EXIT["tp1"], tp2_pct=WINNER_EXIT["tp2"],
                               sl_pct=WINNER_EXIT["sl"], option_horizon_h=WINNER_EXIT["hold_h"],
                               spread_pct=2.0, tsl_trigger_pct=0.0, tsl_offset_pct=0.0)
    iv_at, rv_at = dvol_rv_context()

    rows = []
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt:
            continue
        hb = s["ts_ms"] // 3_600_000
        iv = iv_at.get(hb); rv = rv_at.get(hb)
        rows.append({
            "sl": 1 if opt.get("resolution") == "sl" else 0,
            "pnl": opt["pnl_pct"],
            "regime": s.get("regime"),
            "score": s.get("score"),
            "mtf_aligned": s.get("mtf_aligned"),
            "adx": s.get("adx_score"),
            "bull": s.get("bull_ratio"),
            "vol": s.get("vol_current"),
            "iv": iv, "rv": rv,
            "vrp": (iv - rv) if (iv is not None and rv is not None) else None,
        })
    n = len(rows)
    base = 100 * sum(r["sl"] for r in rows) / n
    print(f"\n[2] {n} trades | base SL-rate {base:.1f}% | avg PnL {st.fmean([r['pnl'] for r in rows]):+.2f}%\n")

    def show(label, keyfn, order=None):
        buck = defaultdict(list)
        for r in rows:
            k = keyfn(r)
            if k is not None:
                buck[k].append(r)
        print(f"--- SL-rate by {label} (base {base:.1f}%) ---")
        keys = order if order else sorted(buck.keys(), key=lambda x: (isinstance(x, str), x))
        for k in keys:
            rs = buck.get(k, [])
            if not rs:
                continue
            slr = 100 * sum(x["sl"] for x in rs) / len(rs)
            pnl = st.fmean([x["pnl"] for x in rs])
            lift = slr - base
            flag = "  <<" if abs(lift) >= 8 else ""
            print(f"  {str(k):>14}  n={len(rs):4}  SL {slr:5.1f}%  (lift {lift:+5.1f})  avgPnL {pnl:+6.2f}%{flag}")
        print()

    def q4(vals):
        v = sorted(x for x in vals if x is not None)
        if len(v) < 8:
            return None
        return [v[len(v)//4], v[len(v)//2], v[3*len(v)//4]]

    def qbucket(r, field, qs):
        x = r.get(field)
        if x is None or qs is None:
            return None
        if x <= qs[0]: return f"Q1 <={qs[0]:.3g}"
        if x <= qs[1]: return f"Q2 <={qs[1]:.3g}"
        if x <= qs[2]: return f"Q3 <={qs[2]:.3g}"
        return f"Q4 >{qs[2]:.3g}"

    show("regime", lambda r: r["regime"])
    show("mtf_aligned", lambda r: r["mtf_aligned"])
    show("score(round)", lambda r: round(r["score"]) if r["score"] is not None else None)
    vq = q4([x["vol"] for x in rows])
    show("vol_current quartile", lambda r: qbucket(r, "vol", vq))
    show("DVOL(IV) quartile", lambda r: qbucket(r, "iv", q4([x["iv"] for x in rows])))
    show("VRP=IV-RV quartile", lambda r: qbucket(r, "vrp", q4([x["vrp"] for x in rows])))

    # combined: regime x VRP sign (ties to the live 4 transition-SLs + project VRP thesis)
    print("--- regime x VRP-sign ---")
    cb = defaultdict(list)
    for r in rows:
        if r["vrp"] is None or r["regime"] is None:
            continue
        cb[(r["regime"], "IV>RV" if r["vrp"] > 0 else "IV<=RV")].append(r)
    for k in sorted(cb.keys()):
        rs = cb[k]
        slr = 100 * sum(x["sl"] for x in rs) / len(rs)
        print(f"  {str(k):>26}  n={len(rs):4}  SL {slr:5.1f}%  avgPnL {st.fmean([x['pnl'] for x in rs]):+6.2f}%")


if __name__ == "__main__":
    main()

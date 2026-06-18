#!/usr/bin/env python3
"""Re-price the validated short-CALL strategy with REAL implied vol (Deribit DVOL).

Their backtest prices premium off σ=0.6 (constant) or dynamic_sigma = RV_168h*1.05
(an RV-based PROXY for IV, since they had no IV history). We now have real IV (DVOL,
16mo). This injects DVOL as σ_t (monkeypatch realized_vol_at_idx_1h, no edits to the
validated module) and compares three pricing modes on the SAME signals, train/holdout:
  const0.6 | dynRVx1.05 (their proxy) | realDVOL (market IV)
σ changes the premium PATH -> which trades TP vs SL -> resolution & pnl. We check if
real-IV pricing changes the strategy and whether it's better / more robust OOS.
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
import services.indicators as ind                                 # noqa: E402
from multiprocessing import cpu_count                             # noqa: E402

DATA = Path(os.path.expanduser("~/Desktop/options/data"))
_orig_rv = ind.realized_vol_at_idx_1h
DVOL_IV = []   # by 1h index, filled in main


def _patched_rv(closes, i, lookback_h=168):
    if 0 <= i < len(DVOL_IV) and DVOL_IV[i] is not None:
        return DVOL_IV[i]
    return _orig_rv(closes, i, lookback_h)


def stats(trades, lo, hi):
    sub = [t for t in trades if lo <= t["idx"] < hi]
    if not sub:
        return "n=0"
    pnls = [t["pnl"] for t in sub]
    sl = 100 * sum(t["sl"] for t in sub) / len(sub)
    return (f"n={len(sub):4} | avg {st.fmean(pnls):+6.2f}% | total {sum(pnls):+8.1f}% | "
            f"SL {sl:4.1f}% | win {100*sum(p>0 for p in pnls)/len(sub):4.1f}%")


def run_mode(name, signals, k5, k1h, sigma, dynamic, mult=1.0):
    sims = simulate_signal_set(signals, k5, sigma=sigma, expiry_hours=168.0,
                               tp1_pct=WINNER_EXIT["tp1"], tp2_pct=WINNER_EXIT["tp2"],
                               sl_pct=WINNER_EXIT["sl"], option_horizon_h=WINNER_EXIT["hold_h"],
                               spread_pct=2.0, klines_1h=k1h, dynamic_sigma=dynamic,
                               iv_rv_multiplier=mult, sigma_clamp=(0.05, 3.0))
    trades = []
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt:
            continue
        trades.append({"idx": s["idx_5m"], "pnl": opt["pnl_pct"],
                       "sl": 1 if opt.get("resolution") == "sl" else 0})
    trades.sort(key=lambda t: t["idx"])
    n5 = len(k5); split = int(n5 * 0.65)
    print(f"\n### {name}  ({len(trades)} trades)")
    print(f"  TRAIN  : {stats(trades, 0, split)}")
    print(f"  HOLDOUT: {stats(trades, split, n5)}")


def main():
    ncore = cpu_count()
    print(f"[1] klines + parallel signal gen ({ncore} cores)...")
    d = load_local_set(DATA); k5, k15, k1h = d["5"], d["15"], d["60"]
    k1h = sorted(k1h, key=lambda c: c["start_ms"])
    signals = gen_parallel(k5, k15, k1h, WINNER_GEN, ncore)
    print(f"    {len(signals)} signals")

    dvol = json.loads((DATA / "eth_dvol_1h.json").read_text())
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    global DVOL_IV
    DVOL_IV = [iv_at.get(c["start_ms"] // 3_600_000) for c in k1h]
    cov = 100 * sum(x is not None for x in DVOL_IV) / len(DVOL_IV)
    print(f"    DVOL coverage over 1h bars: {cov:.0f}%")

    run_mode("const sigma=0.6 (original)", signals, k5, k1h, 0.6, False)
    run_mode("dynamic RV*1.05 (their proxy)", signals, k5, k1h, 0.6, True, mult=1.05)
    # switch to real DVOL (patched fn returns IV directly; mult=1.0)
    ind.realized_vol_at_idx_1h = _patched_rv
    run_mode("REAL DVOL implied vol", signals, k5, k1h, 0.6, True, mult=1.0)
    print("\nIf realDVOL changes resolutions/PnL & holds up OOS, re-pricing on real IV is a real upgrade.")


if __name__ == "__main__":
    main()

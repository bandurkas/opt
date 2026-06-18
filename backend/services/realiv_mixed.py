#!/usr/bin/env python3
"""Round 3: MIXED put+call book, priced on REAL DVOL, OOS-honest.

Two validated halves combined:
  PUTS : sell ATM put in RANGE, MTF up   (PUT_GEN_KWARGS / PUT_EXIT, 168h expiry)
  CALLS: sell ATM call in TRANSITION only (Round-2 OOS win), MTF down (CALL_EXIT, 24h)
A mixed book = more (and more diversified, opposite-direction) trades. Question: does
it raise TOTAL return / $-per-day at similar per-trade edge, holding up OOS?

Real IV via monkeypatch (DVOL). Reports per-side and combined, train/holdout, plus a
rough $/day at $400 with fixed 1%-of-equity sizing.
"""
import json
import os
import statistics as st
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.backtest_data import load_local_set                 # noqa: E402
from services.backtest import simulate_signal_set                 # noqa: E402
from services.option_futures_complement import gen_parallel        # noqa: E402
from services.strategy_config import PUT_GEN_KWARGS, PUT_EXIT, CALL_GEN_KWARGS, CALL_EXIT  # noqa: E402
import services.indicators as ind                                 # noqa: E402
from multiprocessing import cpu_count                             # noqa: E402

DATA = Path(os.path.expanduser("~/Desktop/options/data"))
_orig_rv = ind.realized_vol_at_idx_1h
DVOL_IV = []


def _patched_rv(closes, i, lookback_h=168):
    if 0 <= i < len(DVOL_IV) and DVOL_IV[i] is not None:
        return DVOL_IV[i]
    return _orig_rv(closes, i, lookback_h)


def sim_side(signals, k5, k1h, exit_kw, expiry_h):
    sims = simulate_signal_set(signals, k5, sigma=0.6, expiry_hours=float(expiry_h),
                               tp1_pct=exit_kw["tp1_pct"], tp2_pct=exit_kw["tp2_pct"],
                               sl_pct=exit_kw["sl_pct"], option_horizon_h=exit_kw["hold_h"],
                               spread_pct=2.0, klines_1h=k1h, dynamic_sigma=True,
                               iv_rv_multiplier=1.0, sigma_clamp=(0.05, 3.0))
    out = []
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" in opt:
            out.append({"idx": s["idx_5m"], "pnl": opt["pnl_pct"],
                        "sl": 1 if opt.get("resolution") == "sl" else 0, "side": s["side"]})
    return out


def summ(label, rows):
    if not rows:
        print(f"    {label:16} n=0"); return
    p = [r["pnl"] for r in rows]
    sl = 100 * sum(r["sl"] for r in rows) / len(rows)
    print(f"    {label:16} n={len(rows):4} | avg {st.fmean(p):+6.2f}% | total {sum(p):+8.1f}% | "
          f"SL {sl:4.1f}% | win {100*sum(x>0 for x in p)/len(rows):4.1f}%")


def main():
    ncore = cpu_count()
    print(f"[1] klines + parallel gen x2 ({ncore} cores)...")
    d = load_local_set(DATA); k5, k15, k1h = d["5"], d["15"], d["60"]
    k1h = sorted(k1h, key=lambda c: c["start_ms"])
    call_gen = dict(CALL_GEN_KWARGS); call_gen["regime_filter"] = ["transition"]   # Round-2 OOS win
    puts = gen_parallel(k5, k15, k1h, PUT_GEN_KWARGS, ncore)
    calls = gen_parallel(k5, k15, k1h, call_gen, ncore)
    print(f"    puts {len(puts)} | calls(transition) {len(calls)}")

    dvol = json.loads((DATA / "eth_dvol_1h.json").read_text())
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    global DVOL_IV
    DVOL_IV = [iv_at.get(c["start_ms"] // 3_600_000) for c in k1h]
    ind.realized_vol_at_idx_1h = _patched_rv

    put_tr = sim_side(puts, k5, k1h, PUT_EXIT, 168)
    call_tr = sim_side(calls, k5, k1h, CALL_EXIT, 24)
    allt = sorted(put_tr + call_tr, key=lambda r: r["idx"])
    split = int(len(k5) * 0.65)

    def block(name, rows):
        tr = [r for r in rows if r["idx"] < split]
        ho = [r for r in rows if r["idx"] >= split]
        print(f"\n### {name}")
        summ("TRAIN", tr); summ("HOLDOUT", ho)

    block("PUTS only (range)", put_tr)
    block("CALLS only (transition)", call_tr)
    block("MIXED book", allt)

    # rough $/day at $400, 1% equity per trade, over the ~365d span
    span_days = (k5[-1]["start_ms"] - k5[0]["start_ms"]) / 86_400_000
    for name, rows in (("CALLS", call_tr), ("PUTS", put_tr), ("MIXED", allt)):
        ho = [r for r in rows if r["idx"] >= split]
        ho_days = span_days * 0.35
        usd = sum(r["pnl"] / 100.0 * 0.01 * 400 for r in ho)   # 1% of $400 per trade
        print(f"  ~$/day HOLDOUT {name:6}: ${usd/ho_days:+.3f}/day  ({len(ho)} trades / {ho_days:.0f}d)")
    print("\nWin = MIXED holdout total & $/day > either side alone, per-trade edge intact.")


if __name__ == "__main__":
    main()

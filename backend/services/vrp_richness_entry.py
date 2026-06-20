#!/usr/bin/env python3
"""VRP-richness ENTRY gate test — can we get MORE entries without losing edge?

Idea (grounded in opt21's live VRP read): the live gate is a RELATIVE realized-vol
PERCENTILE (`vol_threshold`), which goes silent in calm markets. But IV can be richly
above RV in calm regimes (high VRP). So we test replacing/relaxing the vol-percentile
gate with an ABSOLUTE VRP-richness floor:  enter only if  DVOL(real IV) - RV_24h >= floor.

Engine: real DVOL pricing via the same monkeypatch realiv_mixed uses (IV independent of
RV -> VRP not circular). Each signal already carries `vol_current` = RV_24h at entry, so
VRP = DVOL_at_hour - vol_current is free. We sweep (vol_threshold x floor), report per
side + mixed, train/holdout (65/35). Uses all cores.

Acceptance (autonomous-research bar): holdout avg >= +1%/trade AND holdout n >= 100 AND
n > baseline. Plus train ~ holdout (no OOS drift).

Run:  PYTHONPATH=. python3 services/vrp_richness_entry.py
"""
import json
import os
import statistics as st
import sys
from pathlib import Path
from multiprocessing import cpu_count

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.backtest_data import load_local_set
from services.backtest import simulate_signal_set
from services.option_futures_complement import gen_parallel
from services.strategy_config import PUT_GEN_KWARGS, PUT_EXIT, CALL_GEN_KWARGS, CALL_EXIT
import services.indicators as ind

DATA = Path(os.path.expanduser("~/Desktop/options/data"))
_orig_rv = ind.realized_vol_at_idx_1h
DVOL_IV = []
IV_AT = {}            # hour-bucket -> real IV (fraction)


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
                        "sl": 1 if opt.get("resolution") == "sl" else 0})
    return out


def attach_vrp(signals, k5):
    """Tag each signal with vrp = DVOL(real IV at its hour) - vol_current(RV_24h)."""
    for s in signals:
        hr = k5[s["idx_5m"]]["start_ms"] // 3_600_000
        dv = IV_AT.get(hr)
        rv = s.get("vol_current")
        s["_vrp"] = (dv - rv) if (dv is not None and rv is not None) else None
        s["_dvol"] = dv
    return signals


def vrp_filter(signals, floor):
    if floor is None:                       # no VRP gate at all (true control)
        return list(signals)
    return [s for s in signals if s.get("_vrp") is not None and s["_vrp"] >= floor]


def stats(rows, lo, hi):
    """avg/total/win/sl over rows with lo<=idx<hi; returns dict or None."""
    p = [r["pnl"] for r in rows if lo <= r["idx"] < hi]
    sl = [r["sl"] for r in rows if lo <= r["idx"] < hi]
    if not p:
        return None
    return {"n": len(p), "avg": st.fmean(p), "total": sum(p),
            "sl": 100 * sum(sl) / len(sl), "win": 100 * sum(x > 0 for x in p) / len(p)}


def main():
    ncore = cpu_count()
    print(f"[1] load klines, attach DVOL ({ncore} cores)...")
    d = load_local_set(DATA)
    k5, k15, k1h = d["5"], d["15"], sorted(d["60"], key=lambda c: c["start_ms"])
    dvol = json.loads((DATA / "eth_dvol_1h.json").read_text())
    global DVOL_IV, IV_AT
    IV_AT = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    DVOL_IV = [IV_AT.get(c["start_ms"] // 3_600_000) for c in k1h]
    ind.realized_vol_at_idx_1h = _patched_rv
    split = int(len(k5) * 0.65)

    VOL_THRESHOLDS = [0.50, 0.35, 0.20, 0.10, 0.0]   # 0.50=live gate; lower = admit calm
    FLOORS = [None, 0.04, 0.08]            # None = NO VRP gate (true control); else IV-RV floor

    sides = [("PUT", PUT_GEN_KWARGS, PUT_EXIT, 168),
             ("CALL", {**CALL_GEN_KWARGS, "regime_filter": ["transition"]}, CALL_EXIT, 24)]

    # cache: gen per (side, vol_threshold) once; floor is a cheap post-filter
    gen_cache = {}
    for sname, gkw, _, _ in sides:
        for vt in VOL_THRESHOLDS:
            kw = {**gkw, "vol_threshold": vt}
            print(f"[gen] {sname} vol_threshold={vt} ...", flush=True)
            sigs = attach_vrp(gen_parallel(k5, k15, k1h, kw, ncore), k5)
            gen_cache[(sname, vt)] = sigs
            print(f"      -> {len(sigs)} raw signals", flush=True)

    def ffmt(fl):
        return " none" if fl is None else f"{fl:>5.2f}"

    print("\n" + "=" * 96)
    print(f"{'side':4} {'vt':>4} {'floor':>5} | {'n_all':>5} | "
          f"{'TRAIN n/avg':>14} | {'HOLD n/avg':>14} | {'HOLD win/SL':>12} | verdict")
    print("-" * 96)

    mixed = {}  # (vt,floor) -> rows for mixed book
    for sname, gkw, exit_kw, exp in sides:
        for vt in VOL_THRESHOLDS:
            base = gen_cache[(sname, vt)]
            for fl in FLOORS:
                sigs = vrp_filter(base, fl)
                rows = sim_side(sigs, k5, k1h, exit_kw, exp) if sigs else []
                tr = stats(rows, 0, split)
                ho = stats(rows, split, len(k5))
                mixed.setdefault((vt, fl), []).extend(rows)
                if ho:
                    ok = (ho["avg"] >= 1.0 and ho["n"] >= 100)
                    v = "OK" if ok else ""
                    print(f"{sname:4} {vt:>4} {ffmt(fl)} | {len(rows):5} | "
                          f"{(tr['n'] if tr else 0):4}/{(tr['avg'] if tr else 0):+6.2f}% | "
                          f"{ho['n']:4}/{ho['avg']:+6.2f}% | "
                          f"{ho['win']:4.0f}%/{ho['sl']:4.0f}% | {v}")
                else:
                    print(f"{sname:4} {vt:>4} {ffmt(fl)} | {len(rows):5} | (no holdout)")

    print("\n" + "=" * 96)
    print("MIXED book (PUT+CALL) by (vol_threshold, floor). TR=train HO=holdout, gap=HO-TR:")
    print(f"{'vt':>4} {'floor':>5} | {'n_all':>5} | {'TR avg':>7} {'HO n':>5} {'HO avg':>7} "
          f"{'gap':>6} {'HO tot':>8} {'$/day':>6} | verdict")
    span_days = (k5[-1]["start_ms"] - k5[0]["start_ms"]) / 86_400_000
    ho_days = span_days * 0.35
    base_ho_n = None
    for (vt, fl), rows in sorted(mixed.items(), key=lambda kv: (kv[0][0], 9 if kv[0][1] is None else kv[0][1])):
        tr = stats(rows, 0, split)
        ho = stats(rows, split, len(k5))
        if not ho or not tr:
            continue
        if vt == 0.50 and fl is None:
            base_ho_n = ho["n"]              # TRUE baseline = live gate, no VRP filter
        usd = sum(r["pnl"] / 100.0 * 0.01 * 400 for r in rows if r["idx"] >= split) / ho_days
        more = (base_ho_n is not None and ho["n"] > base_ho_n)
        gap = ho["avg"] - tr["avg"]
        stable = abs(gap) < 8.0             # train~holdout sanity (avg within 8pp)
        ok = (ho["avg"] >= 1.0 and ho["n"] >= 100 and more and stable)
        tag = "OK+more" if ok else ("more,unstable" if (more and not stable)
                                    else ("edge-ok" if ho["avg"] >= 1.0 else ""))
        print(f"{vt:>4} {ffmt(fl)} | {len(rows):5} | {tr['avg']:+6.2f}% {ho['n']:5} "
              f"{ho['avg']:+6.2f}% {gap:+6.1f} {ho['total']:+7.0f}% {usd:+5.2f} | {tag}")
    print(f"\nTRUE baseline (vt0.50/floor none) holdout n = {base_ho_n}. "
          f"Goal: holdout n > baseline AND avg >= +1% AND |gap| < 8pp.")


if __name__ == "__main__":
    main()

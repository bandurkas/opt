"""Variant comparison: fix the trend-zone deadlock in V2 hybrid.

Baseline == generate_hybrid_v2 logic (documented n~936, +7.09%/365d).
We test 3 proposed fixes for the regime⊥MTF contradiction in the trend zone
(the single-allowed-side region where ret_7d crosses ±0.5%):

  V1  drop the regime filter when side is forced by ret_7d (trend zone)
  V2  weaken MTF to >=1 aligned TF in the trend zone (sign already given by ret_7d)
  V3  raise the ADX 'trend' threshold 25 -> 35 (widens range/transition)
  COMBO  V1+V2+V3

For each: report n, WR, avg, sharpe, max-consec-loss, losing-months, per-side,
and signal counts split by zone (trend vs range).

Run:
    cd backend && PYTHONPATH=. python3 services/variant_backtest.py
"""
from __future__ import annotations

import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.indicators import adx, ema, realized_vol
from services.local_optimizer import find_data_dir, load_local
from services.momentum_mtf import analyze_tf, consensus
from services.strategy_config import (
    PUT_GEN_KWARGS, CALL_GEN_KWARGS, PUT_EXIT, CALL_EXIT, RET_7D_THRESHOLD,
)

BARS_7D = 2016


def regime_of(s1h, trend_adx: float = 25.0, range_adx: float = 20.0):
    a = adx(s1h, 14)
    if a is None:
        return "unknown", None
    if a > trend_adx:
        return "trend", a
    if a < range_adx:
        return "range", a
    return "transition", a


def generate(k5, k15, k1h, *, variant: str = "baseline",
             ret_thr: float = RET_7D_THRESHOLD, history_window: int = 240):
    put_gen, call_gen = PUT_GEN_KWARGS, CALL_GEN_KWARGS
    out = []
    last_idx = -10_000
    i15 = i1h = 0
    trend_adx = 35.0 if variant in ("v3", "combo") else 25.0
    drop_regime_trendzone = variant in ("v1", "combo")
    weak_mtf_trendzone = variant in ("v2", "combo")

    for i, c5 in enumerate(k5):
        ts_end = c5["start_ms"] + 5 * 60 * 1000
        while i15 < len(k15) and k15[i15]["start_ms"] + 15 * 60 * 1000 <= ts_end:
            i15 += 1
        while i1h < len(k1h) and k1h[i1h]["start_ms"] + 60 * 60 * 1000 <= ts_end:
            i1h += 1
        s5 = k5[max(0, i + 1 - history_window):i + 1]
        s15 = k15[max(0, i15 - history_window):i15]
        s1h = k1h[max(0, i1h - history_window):i1h]
        if i < 60 or i < BARS_7D or len(s5) < 50 or len(s15) < 50 or len(s1h) < 200:
            continue

        prev_close = k5[i - BARS_7D]["close"]
        ret_7d = (c5["close"] - prev_close) / prev_close * 100

        if ret_7d > ret_thr:
            allowed = ["P"]
        elif ret_7d < -ret_thr:
            allowed = ["C"]
        else:
            allowed = ["P", "C"]
        in_trend_zone = len(allowed) == 1

        regime_name, _ = regime_of(s1h, trend_adx=trend_adx)

        # Global trend skip (baseline). In trend zone with V1, we keep going.
        if regime_name == "trend" and not (in_trend_zone and drop_regime_trendzone):
            continue

        mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
        direction, aligned = mtf["direction"], mtf["tfs_aligned"]

        closes_1h = [c["close"] for c in s1h]
        if len(closes_1h) < 168 + 20:
            continue
        rolling_vols = []
        for j in range(20, len(closes_1h)):
            rv = realized_vol(closes_1h[:j + 1], lookback=24)
            if rv is not None:
                rolling_vols.append(rv)
        if len(rolling_vols) < 30:
            continue
        current_vol = rolling_vols[-1]
        sorted_vols = sorted(rolling_vols)

        emitted = None
        for side in allowed:
            gen = put_gen if side == "P" else call_gen
            thr = sorted_vols[int(len(sorted_vols) * gen["vol_threshold"])]
            if current_vol < thr:
                continue

            # regime filter (per-side) — skipped in trend zone for V1
            if not (in_trend_zone and drop_regime_trendzone):
                rf = gen.get("regime_filter") or (["range"] if side == "P" else ["range", "transition"])
                if regime_name not in rf:
                    continue

            # MTF filter
            mtf_dir = gen.get("mtf_direction_filter")
            min_aligned = 1 if (in_trend_zone and weak_mtf_trendzone) else 2
            if mtf_dir == "up" and (direction != "up" or aligned < min_aligned):
                continue
            if mtf_dir == "down" and (direction != "down" or aligned < min_aligned):
                continue

            # bull filter (Put only, matching generate_hybrid_v2 baseline)
            if side == "P":
                bull_max = gen.get("bull_market_ratio_max")
                if bull_max is not None and len(closes_1h) >= 200:
                    e50, e200 = ema(closes_1h, 50), ema(closes_1h, 200)
                    if e50 and e200 and e200 > 0 and e50 / e200 > bull_max:
                        continue

            emitted = side
            break

        if emitted is None:
            continue

        cd = max(put_gen.get("cooldown_bars", 6), call_gen.get("cooldown_bars", 6))
        if i - last_idx < cd:
            continue

        out.append({
            "idx_5m": i, "ts_ms": ts_end, "close": c5["close"], "side": emitted,
            "regime": regime_name, "ret_7d": round(ret_7d, 2),
            "zone": "trend" if in_trend_zone else "range",
            "position": "short_premium",
        })
        last_idx = i
    return out


def stats(sims):
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return None
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    st = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sh = (statistics.mean(pnls) / st) if st > 0 else 0
    mc = cl = 0
    for p in pnls:
        cl = cl + 1 if p < 0 else 0
        mc = max(mc, cl)
    monthly, by_side = {}, {}
    for s in sims:
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m")
        pnl = s.get("option", {}).get("pnl_pct")
        if pnl is not None:
            monthly.setdefault(ts, []).append(pnl)
            by_side.setdefault(s.get("side", "?"), []).append(pnl)
    lm = sum(1 for ps in monthly.values() if statistics.mean(ps) < 0)
    ss = {sd: {"n": len(sp), "wr": round(sum(1 for p in sp if p > 0) / len(sp), 3),
               "avg": round(statistics.mean(sp), 2)} for sd, sp in by_side.items()}
    return {"n": len(pnls), "wr": round(wr, 3), "avg": round(statistics.mean(pnls), 2),
            "sharpe": round(sh, 2), "total": round(sum(pnls), 1),
            "mc": mc, "lm": lm, "tm": len(monthly), "by_side": ss}


def sim_set(sigs, k5):
    p = [s for s in sigs if s["side"] == "P"]
    c = [s for s in sigs if s["side"] == "C"]
    ps = simulate_signal_set(p, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=PUT_EXIT["tp1_pct"], tp2_pct=PUT_EXIT["tp2_pct"], sl_pct=PUT_EXIT["sl_pct"],
            option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if p else []
    cs = simulate_signal_set(c, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=CALL_EXIT["tp1_pct"], tp2_pct=CALL_EXIT["tp2_pct"], sl_pct=CALL_EXIT["sl_pct"],
            option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if c else []
    return ps + cs


def main():
    t0 = time.time()
    k5, k15, k1h = load_local(find_data_dir(None))
    print(f"klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}\n", flush=True)

    variants = ["baseline", "v1", "v2", "v3", "combo"]
    labels = {"baseline": "Baseline (live V2)", "v1": "V1 drop-regime-trendzone",
              "v2": "V2 weak-MTF-trendzone", "v3": "V3 ADX trend>35", "combo": "COMBO v1+v2+v3"}
    res = {}
    for v in variants:
        sigs = generate(k5, k15, k1h, variant=v)
        tz = sum(1 for s in sigs if s["zone"] == "trend")
        rz = len(sigs) - tz
        tzp = sum(1 for s in sigs if s["zone"] == "trend" and s["side"] == "P")
        tzc = sum(1 for s in sigs if s["zone"] == "trend" and s["side"] == "C")
        st = stats(sim_set(sigs, k5))
        res[v] = (st, tz, rz, tzp, tzc)
        if st:
            print(f"{labels[v]:<26} sigs={len(sigs):>4} (trend={tz} [P{tzp}/C{tzc}], range={rz})", flush=True)
        else:
            print(f"{labels[v]:<26} sigs={len(sigs):>4} — no sims", flush=True)

    print(f"\n{'='*104}")
    print(f"{'Config':<26} {'n':>5} {'WR':>6} {'avg':>8} {'sharpe':>7} {'total':>10} {'maxCL':>6} {'losM':>5} {'/mo':>4}")
    print("-" * 104)
    for v in variants:
        st, tz, rz, tzp, tzc = res[v]
        if not st:
            print(f"{labels[v]:<26} — 0 trades")
            continue
        print(f"{labels[v]:<26} {st['n']:>5} {st['wr']*100:>5.1f}% {st['avg']:>+7.2f}% "
              f"{st['sharpe']:>+6.2f} {st['total']:>+9.1f}% {st['mc']:>6} {st['lm']:>5} {st['tm']:>4}")
        bs = st["by_side"]
        for sd in ("P", "C"):
            if sd in bs:
                b = bs[sd]
                print(f"    {'Put' if sd=='P' else 'Call':<22} {b['n']:>5} {b['wr']*100:>5.1f}% {b['avg']:>+7.2f}%")

    # pick best by avg, then sharpe, requiring lm small
    scored = [(v, res[v][0]) for v in variants if res[v][0]]
    best = max(scored, key=lambda x: (x[1]["avg"], x[1]["sharpe"], -x[1]["lm"]))
    print(f"\nBEST by avg/sharpe: {labels[best[0]]}  (avg {best[1]['avg']:+.2f}%, "
          f"sharpe {best[1]['sharpe']:+.2f}, losing-months {best[1]['lm']}/{best[1]['tm']})")
    print(f"elapsed {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

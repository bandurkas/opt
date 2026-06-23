"""variant_backtest.py's V2-hybrid / ADX-cutoff variant comparison, re-run on
ETH's full 4-year Bybit history (eth_long_{5m,15m,1h}.json, 2022-06 on) instead
of the ~1y eth_{5m,15m,1h}.json window. Same generate()/sim_set()/stats() logic
— this file only swaps the data source and parallelizes across the 5 variants
(each variant's walk is inherently sequential — cooldown/regime state carries
across the whole series — so the safe parallelism unit is "one variant per
core", not chunking within a variant).

Run:
    cd backend && PYTHONPATH=. python3 services/variant_backtest_4y.py
"""
from __future__ import annotations

import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.variant_backtest import generate, sim_set, stats

VARIANTS = ["baseline", "v1", "v2", "v3", "combo"]
labels = {"baseline": "Baseline (live V2)", "v1": "V1 drop-regime-trendzone",
          "v2": "V2 weak-MTF-trendzone", "v3": "V3 ADX trend>35", "combo": "COMBO v1+v2+v3"}


def _run_variant(args):
    v, k5, k15, k1h = args
    sigs = generate(k5, k15, k1h, variant=v)
    tz = sum(1 for s in sigs if s["zone"] == "trend")
    rz = len(sigs) - tz
    tzp = sum(1 for s in sigs if s["zone"] == "trend" and s["side"] == "P")
    tzc = sum(1 for s in sigs if s["zone"] == "trend" and s["side"] == "C")
    st = stats(sim_set(sigs, k5))
    return v, (st, tz, rz, tzp, tzc, len(sigs))


def main():
    t0 = time.time()
    k5, k15, k1h = load_coin("eth_long", find_data_dir(None))
    print(f"klines (4y): 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}\n", flush=True)

    with Pool(len(VARIANTS)) as pool:
        results = pool.map(_run_variant, [(v, k5, k15, k1h) for v in VARIANTS])
    res = dict(results)

    for v in VARIANTS:
        st, tz, rz, tzp, tzc, n = res[v]
        if st:
            print(f"{labels[v]:<26} sigs={n:>5} (trend={tz} [P{tzp}/C{tzc}], range={rz})", flush=True)
        else:
            print(f"{labels[v]:<26} sigs={n:>5} — no sims", flush=True)

    print(f"\n{'='*104}")
    print(f"{'Config':<26} {'n':>5} {'WR':>6} {'avg':>8} {'sharpe':>7} {'total':>10} {'maxCL':>6} {'losM':>5} {'/mo':>4}")
    print("-" * 104)
    for v in VARIANTS:
        st, tz, rz, tzp, tzc, n = res[v]
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

    scored = [(v, res[v][0]) for v in VARIANTS if res[v][0]]
    best = max(scored, key=lambda x: (x[1]["avg"], x[1]["sharpe"], -x[1]["lm"]))
    print(f"\nBEST by avg/sharpe: {labels[best[0]]}  (avg {best[1]['avg']:+.2f}%, "
          f"sharpe {best[1]['sharpe']:+.2f}, losing-months {best[1]['lm']}/{best[1]['tm']})")
    print(f"elapsed {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

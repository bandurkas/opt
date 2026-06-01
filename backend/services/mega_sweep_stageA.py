"""Mega-sweep Stage A: fast signal-only screening.

Generate signals for all gen combos, filter by basic stats (n_signals,
frequency, spread across months). Saves top-K gen combos for Stage B.

Grid:
  vol: [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]  (8)
  cd: [2, 3, 4, 5, 6, 8, 10, 12]                           (8)
  regime: range, range+transition                            (2)
  adx_max: None, 15, 20, 25                                 (4)
  bull: None, 1.03, 1.05, 1.08                              (4)
Total gen combos: 8×8×2×4×4 = 2,048
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import statistics
import sys
import time
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.holdout_split import HOLDOUT_DAYS, holdout_cutoff_ms, split_signals_by_holdout
from services.local_optimizer import find_data_dir, load_local
from services.strategy_registry import gen_sell_premium_iv_high

_K5: list = []
_K15: list = []
_K1H: list = []


def _init_worker(k5, k15, k1h):
    global _K5, _K15, _K1H
    _K5, _K15, _K1H = k5, k15, k1h


def gen_key(gen: dict) -> str:
    return hashlib.md5(json.dumps(gen, sort_keys=True).encode()).hexdigest()[:12]


def _count_consec(signals):
    """Max consecutive losses assuming 50% WR baseline (rough proxy)."""
    # Without sim, use month-level uniformity as proxy
    return 0  # filled in by BS sim


def _signal_score(gen, signals, k5):
    """Score signals without full simulation:
    - n_signals in train pool (higher = more opportunities)
    - signal frequency uniformity across months (penalize clumpy)
    - penalty for too-few signals
    """
    cutoff = holdout_cutoff_ms(k5)
    train_pool, holdout = split_signals_by_holdout(signals, cutoff)
    n_train = len(train_pool)
    n_holdout = len(holdout)

    # Monthly uniformity
    monthly = {}
    for s in signals:
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
        m = ts.strftime("%Y-%m")
        monthly.setdefault(m, 0)
        monthly[m] += 1

    n_months = len(monthly)
    avg_per_month = len(signals) / max(n_months, 1)
    if n_months > 1:
        std_per_month = statistics.stdev(monthly.values())
        cv = std_per_month / avg_per_month if avg_per_month > 0 else 999
    else:
        cv = 999

    # Score: prefer moderate frequency (20-100/mo) + uniform distribution
    freq_score = min(1.0, n_train / 100.0)  # bonus for more signals
    uniformity_pen = cv * 0.5  # penalize clumpiness
    too_few_pen = max(0, 50 - n_train) * 0.1  # penalty if < 50 train signals

    return freq_score * 10 - uniformity_pen - too_few_pen + n_train * 0.05


def _worker_task(gen_tuple):
    vol, cd, regime, adx, bull = gen_tuple
    gen = {
        "vol_threshold": vol,
        "regime_filter": list(regime),
        "side": "P",
        "adx_max": adx,
        "mtf_direction_filter": "up",
        "bull_market_ratio_max": bull,
        "cooldown_bars": cd,
    }
    signals = gen_sell_premium_iv_high(_K5, _K15, _K1H, **gen)
    label = (f"P.v{vol}.cd{cd}.{'r' if regime == ('range',) else 'rt'}"
             f".{f'adx{adx}' if adx else 'no'}.{f'b{bull}' if bull else 'nb'}")
    score = _signal_score(gen, signals, _K5)

    # Monthly breakdown
    monthly = {}
    for s in signals:
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
        m = ts.strftime("%Y-%m")
        monthly.setdefault(m, 0)
        monthly[m] += 1

    cutoff = holdout_cutoff_ms(_K5)
    train_pool, holdout = split_signals_by_holdout(signals, cutoff)

    return {
        "gen": gen,
        "label": label,
        "n_signals": len(signals),
        "n_train": len(train_pool),
        "n_holdout": len(holdout),
        "n_months": len(monthly),
        "avg_per_month": round(len(signals) / max(len(monthly), 1), 1),
        "score": round(score, 2),
    }


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== MEGA SWEEP Stage A: Signal screening ===", flush=True)
    print(f"  data: {data_dir}", flush=True)

    k5, k15, k1h = load_local(data_dir)
    print(f"  klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}", flush=True)
    print(f"  holdout: last {HOLDOUT_DAYS}d", flush=True)

    # Gen param grid: 8×8×2×4×4 = 2,048
    gen_combos = list(product(
        [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70],
        [2, 3, 4, 5, 6, 8, 10, 12],
        [("range",), ("range", "transition")],
        [None, 15, 20, 25],
        [None, 1.03, 1.05, 1.08],
    ))
    print(f"  Gen combos: {len(gen_combos):,}", flush=True)

    workers = min(8, len(gen_combos))
    results = []

    with mp.Pool(workers, initializer=_init_worker, initargs=(k5, k15, k1h)) as pool:
        for i, row in enumerate(pool.imap_unordered(_worker_task, gen_combos), 1):
            results.append(row)
            if i % 200 == 0 or i == len(gen_combos):
                best = max(results, key=lambda r: r["score"])
                print(f"  [{i}/{len(gen_combos)}] best_score={best['score']:.2f} "
                      f"label={best['label']} n={best['n_signals']}", flush=True)

    ranked = sorted(results, key=lambda r: r["score"], reverse=True)

    print(f"\n{'='*100}")
    print(f"TOP 30 gen configs by signal quality score")
    print(f"{'label':<55} {'sig':>5} {'train':>6} {'hold':>5} "
          f"{'mo':>4} {'avg/mo':>7} {'score':>7}")
    for r in ranked[:30]:
        print(f"{r['label'][:55]:<55} {r['n_signals']:>5} "
              f"{r['n_train']:>6} {r['n_holdout']:>5} "
              f"{r['n_months']:>4} {r['avg_per_month']:>7.1f} {r['score']:>7.2f}")

    # Save top 50 for Stage B
    repo = Path(__file__).resolve().parents[2]
    top50 = ranked[:50]
    out_path = repo / "sweep_results" / "mega_sweep_stageA_top50.json"
    payload = {
        "stage": "A_signal_screen",
        "n_gen_combos": len(gen_combos),
        "elapsed_s": round(time.time() - t0, 1),
        "top50": [
            {k: r[k] for k in ["label", "gen", "n_signals", "n_train", "n_holdout",
                               "n_months", "avg_per_month", "score"]}
            for r in top50
        ],
    }
    out_path.write_text(json.dumps(payload, indent=2))
    elapsed = round(time.time() - t0, 1)
    print(f"\nSaved top 50 → {out_path} ({elapsed}s)", flush=True)


if __name__ == "__main__":
    main()

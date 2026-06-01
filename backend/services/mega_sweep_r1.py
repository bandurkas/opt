"""Mega-sweep Round 1: Put MTF-up focused parameter sweep.

Fixes exits to the current LIVE exit, sweeps gen params broadly.
~1,024 combos — fast enough for parallel execution.

Key dimensions:
- vol_threshold: 0.35-0.75 (8 values)
- cd: 2-12 (8 values)
- regime: range, range+transition (2)
- adx_max: None, 15, 20, 25 (4)
- bull_market_ratio_max: None, 1.03, 1.05, 1.08 (4)
- hold_h: 48, 72, 96, 120, 144, 168 (6) — via exit variants
"""
from __future__ import annotations

import json
import multiprocessing as mp
import statistics
import sys
import time
from itertools import product
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.holdout_split import HOLDOUT_DAYS, holdout_cutoff_ms, split_signals_by_holdout
from services.local_optimizer import find_data_dir, load_local
from services.strategy_registry import gen_sell_premium_iv_high

SIGNAL_CACHE: dict[str, list] = {}
_K5: list = []
_K15: list = []
_K1H: list = []


def _init_worker(k5, k15, k1h):
    global _K5, _K15, _K1H
    _K5, _K15, _K1H = k5, k15, k1h
    SIGNAL_CACHE.clear()


def gen_key(gen: dict) -> str:
    import hashlib
    return hashlib.md5(json.dumps(gen, sort_keys=True).encode()).hexdigest()[:12]


def get_signals(k5, k15, k1h, gen: dict) -> list:
    key = gen_key(gen)
    if key not in SIGNAL_CACHE:
        SIGNAL_CACHE[key] = gen_sell_premium_iv_high(k5, k15, k1h, **gen)
    return SIGNAL_CACHE[key]


def _max_consec_loss(pnls):
    mx = cl = 0
    for p in pnls:
        cl = cl + 1 if p < 0 else 0
        mx = max(mx, cl)
    return mx


def _sim_stats(sims):
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return None
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    st = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sh = (statistics.mean(pnls) / st) if st > 0 else None
    # Monthly breakdown
    monthly = {}
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt:
            continue
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
        m = ts.strftime("%Y-%m")
        monthly.setdefault(m, []).append(opt["pnl_pct"])
    losing_months = sum(1 for ps in monthly.values() if statistics.mean(ps) < 0)
    return {
        "n": len(pnls), "wr": round(wr, 3),
        "avg": round(statistics.mean(pnls), 2),
        "median": round(statistics.median(pnls), 2),
        "stdev": round(st, 2),
        "sharpe": round(sh, 2) if sh is not None else None,
        "total": round(sum(pnls), 1),
        "max_consec_loss": _max_consec_loss(pnls),
        "losing_months": losing_months,
        "total_months": len(monthly),
    }


def score_combo(row):
    """Score for ranking: OOS avg, penalized for consec losses & losing months."""
    te = row.get("test") or {}
    tr = row.get("train") or {}
    te_avg = te.get("avg")
    te_n = te.get("n") or 0
    te_sh = te.get("sharpe") or 0
    tr_avg = tr.get("avg") or 0
    if te_avg is None or te_n < 15:
        return -999.0
    base = te_avg * min(2.0, te_n / 30.0)
    sh_bonus = 2.0 * max(0, te_sh)
    overfit = max(0.0, (tr_avg - te_avg) - 3.0) * 0.5 if tr else 0.0
    cl_pen = te.get("max_consec_loss", 0) * 1.0
    lm = te.get("losing_months", 0)
    tm = te.get("total_months", 1)
    mo_pen = (lm / max(tm, 1)) * 15.0
    return base + sh_bonus - overfit - cl_pen - mo_pen


def evaluate_combo(k5, k15, k1h, gen, ex, sigma=0.6, spread=2.0):
    signals = get_signals(k5, k15, k1h, gen)
    cutoff = holdout_cutoff_ms(k5)
    train_pool, holdout = split_signals_by_holdout(signals, cutoff)
    if len(train_pool) < 10:
        return {"gen": gen, "exit": ex, "n_signals": len(signals),
                "train": None, "test": None, "holdout": None, "score": -999}
    idx = int(len(train_pool) * 0.70)
    tr_sigs = train_pool[:idx]
    te_sigs = train_pool[idx:]

    def _sim(sigs):
        if not sigs:
            return None
        return simulate_signal_set(
            sigs, k5, sigma=sigma, expiry_hours=168.0,
            tp1_pct=ex["tp1"], tp2_pct=ex["tp2"], sl_pct=ex["sl"],
            option_horizon_h=ex["hold_h"], spread_pct=spread,
        )

    tr_stats = _sim_stats(_sim(tr_sigs))
    te_stats = _sim_stats(_sim(te_sigs))
    ho_stats = _sim_stats(_sim(holdout)) if holdout else None

    row = {
        "gen": gen, "exit": ex, "n_signals": len(signals),
        "train": tr_stats, "test": te_stats, "holdout": ho_stats,
    }
    row["score"] = score_combo(row)
    return row


def _evaluate_task(task):
    """Module-level worker for multiprocessing pickling."""
    k5i, k15i, k1hi, gen, ex, label = task
    row = evaluate_combo(k5i, k15i, k1hi, gen, ex)
    row["label"] = label
    return row


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== MEGA SWEEP Round 1: Put MTF-up ===", flush=True)
    print(f"  data: {data_dir}", flush=True)

    k5, k15, k1h = load_local(data_dir)
    print(f"  klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}", flush=True)

    # Exits to test (representative spread of hold_h + SL combos)
    exits = [
        {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24, "lbl": "t24_sl50"},
        {"tp1": 0.30, "tp2": 0.50, "sl": 0.75, "hold_h": 24, "lbl": "t24_sl75"},
        {"tp1": 0.40, "tp2": 0.60, "sl": 0.75, "hold_h": 48, "lbl": "t48_sl75"},
        {"tp1": 0.40, "tp2": 0.60, "sl": 1.00, "hold_h": 48, "lbl": "t48_sl100"},
        {"tp1": 0.50, "tp2": 0.70, "sl": 1.00, "hold_h": 72, "lbl": "t72_sl100"},
        {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "lbl": "t72_sl150"},
        {"tp1": 0.50, "tp2": 0.70, "sl": 1.00, "hold_h": 96, "lbl": "t96_sl100"},
        {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 96, "lbl": "t96_sl150"},  # current LIVE
        {"tp1": 0.50, "tp2": 0.70, "sl": 2.00, "hold_h": 96, "lbl": "t96_sl200"},
        {"tp1": 0.60, "tp2": 0.80, "sl": 1.50, "hold_h": 120, "lbl": "t120_sl150"},
        {"tp1": 0.60, "tp2": 0.80, "sl": 2.00, "hold_h": 120, "lbl": "t120_sl200"},
        {"tp1": 0.60, "tp2": 0.80, "sl": 2.50, "hold_h": 120, "lbl": "t120_sl250"},
        {"tp1": 0.70, "tp2": 1.00, "sl": 2.00, "hold_h": 144, "lbl": "t144_sl200"},
        {"tp1": 0.70, "tp2": 1.00, "sl": 3.00, "hold_h": 144, "lbl": "t144_sl300"},
        {"tp1": 0.80, "tp2": 1.20, "sl": 2.00, "hold_h": 168, "lbl": "t168_sl200"},
        {"tp1": 0.80, "tp2": 1.20, "sl": 3.00, "hold_h": 168, "lbl": "t168_sl300"},
    ]

    # Gen param grid
    combos = []
    for vol, cd, regime, adx, bull, ex in product(
        [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70],  # 8
        [2, 3, 4, 5, 6, 8, 10, 12],                          # 8
        [("range",), ("range", "transition")],                # 2
        [None, 15, 20, 25],                                   # 4
        [None, 1.03, 1.05, 1.08],                             # 4
        exits,                                                 # 16
    ):
        gen = {
            "vol_threshold": vol,
            "regime_filter": list(regime),
            "side": "P",
            "adx_max": adx,
            "mtf_direction_filter": "up",
            "bull_market_ratio_max": bull,
            "cooldown_bars": cd,
        }
        label = (f"P.v{vol}.cd{cd}.{'r' if regime == ('range',) else 'rt'}"
                 f".{f'adx{adx}' if adx else 'no'}.{f'b{bull}' if bull else 'nb'}.{ex['lbl']}")
        combos.append((gen, ex, label))

    print(f"  Total combos: {len(combos):,}", flush=True)
    print(f"  8×8×2×4×4×16 = {8*8*2*4*4*16:,}", flush=True)

    # Run in parallel
    workers = min(8, len(combos))
    results = []
    tasks = [(k5, k15, k1h, gen, ex, label) for gen, ex, label in combos]

    def _local_worker(task):
        k5i, k15i, k1hi, gen, ex, label = task
        row = evaluate_combo(k5i, k15i, k1hi, gen, ex)
        row["label"] = label
        return row

    if workers <= 1:
        for i, task in enumerate(tasks, 1):
            row = _local_worker(task)
            results.append(row)
            if i % 50 == 0 or i == len(tasks):
                best = max(results, key=score_combo)
                print(f"  [{i}/{len(tasks)}] best_score={best['score']:.2f} label={best['label'][:50]}", flush=True)
    else:
        with mp.Pool(workers, initializer=_init_worker, initargs=(k5, k15, k1h)) as pool:
            for i, row in enumerate(pool.imap_unordered(_evaluate_task, tasks), 1):
                results.append(row)
                if i % 50 == 0 or i == len(tasks):
                    best = max(results, key=score_combo)
                    print(f"  [{i}/{len(tasks)}] best_score={best['score']:.2f} label={best['label'][:50]}", flush=True)

    # Print top 30
    ranked = sorted(results, key=score_combo, reverse=True)
    print(f"\n{'='*130}")
    print(f"TOP 30 by OOS score")
    hdr = (f"{'label':<72} {'sig':>4} {'tr_n':>5} {'tr_avg':>7} "
           f"{'te_n':>5} {'te_avg':>7} {'te_sh':>6} {'cl':>4} {'lm':>4} {'score':>7}")
    print(hdr)
    for r in ranked[:30]:
        tr, te = r.get("train") or {}, r.get("test") or {}
        cl = te.get("max_consec_loss", 0)
        lm = te.get("losing_months", 0)
        print(f"{r['label'][:72]:<72} {r['n_signals']:>4} "
              f"{tr.get('n', 0):>5} {tr.get('avg', 0):>+6.2f}% "
              f"{te.get('n', 0):>5} {te.get('avg', 0):>+6.2f}% {te.get('sharpe', 0):>+5.2f} "
              f"{cl:>4} {lm:>4} {score_combo(r):>7.2f}")

    # Save results
    repo = Path(__file__).resolve().parents[2]
    out_path = repo / "sweep_results" / "mega_sweep_put_r1.json"
    best = ranked[0] if ranked else None
    payload = {
        "round": "put_r1",
        "sigma": 0.6,
        "spread": 2.0,
        "elapsed_s": round(time.time() - t0, 1),
        "n_combos": len(results),
        "results": [
            {k: r[k] for k in ["label", "gen", "exit", "n_signals", "train", "test", "holdout", "score"]}
            for r in ranked[:100]
        ],
        "best": best,
    }
    out_path.write_text(json.dumps(payload, indent=2))

    elapsed = round(time.time() - t0, 1)
    print(f"\nSaved → {out_path} ({elapsed}s)", flush=True)

    if best:
        te = best.get("test") or {}
        tr = best.get("train") or {}
        ho = best.get("holdout") or {}
        print(f"\n{'='*70}")
        print(f"BEST: {best['label']}")
        print(f"  gen: {json.dumps(best['gen'])}")
        print(f"  exit: {json.dumps(best['exit'])}")
        if tr:
            print(f"  train: n={tr.get('n')} avg={tr.get('avg'):+.2f}% sh={tr.get('sharpe')} "
                  f"cl={tr.get('max_consec_loss')} lm={tr.get('losing_months')}")
        if te:
            print(f"  test:  n={te.get('n')} avg={te.get('avg'):+.2f}% sh={te.get('sharpe')} "
                  f"cl={te.get('max_consec_loss')} lm={te.get('losing_months')}")
        if ho:
            print(f"  holdout: n={ho.get('n')} avg={ho.get('avg'):+.2f}% sh={ho.get('sharpe')} "
                  f"cl={ho.get('max_consec_loss')} lm={ho.get('losing_months')}")
        print(f"  score: {best['score']:.2f}")


if __name__ == "__main__":
    main()

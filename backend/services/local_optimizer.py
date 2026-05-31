"""Local parameter optimizer for sell_premium strategy.

Uses VPS-exported klines + 70/30 time-ordered train/test split.
Caches signal generation per gen-key for speed.

Usage:
    cd backend && PYTHONPATH=. python3 services/local_optimizer.py
    cd backend && PYTHONPATH=. python3 services/local_optimizer.py --round refine --seed sweep_results/local_opt_iter1.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import statistics
import sys
import time
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.strategy_registry import gen_sell_premium_iv_high
from services.strategy_sweep import split_signals

_IV_MAP = {"5": "5m", "15": "15m", "60": "1h"}
SIGNAL_CACHE: dict[str, list] = {}
_K5: list = []
_K15: list = []
_K1H: list = []


def _init_worker(k5, k15, k1h):
    global _K5, _K15, _K1H
    _K5, _K15, _K1H = k5, k15, k1h
    SIGNAL_CACHE.clear()


def find_data_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve()
    for c in (here.parents[2] / "data", Path("/data")):
        if (c / "eth_5m.json").exists():
            return c
    return here.parents[2] / "data"


def load_local(data_dir: Path) -> tuple[list, list, list]:
    out = {}
    for iv, fname in _IV_MAP.items():
        path = data_dir / f"eth_{fname}.json"
        candles = json.loads(path.read_text())
        out[iv] = candles
    return out["5"], out["15"], out["60"]


def gen_key(gen: dict) -> str:
    return hashlib.md5(json.dumps(gen, sort_keys=True).encode()).hexdigest()[:12]


def get_signals(k5, k15, k1h, gen: dict) -> list:
    key = gen_key(gen)
    if key not in SIGNAL_CACHE:
        SIGNAL_CACHE[key] = gen_sell_premium_iv_high(k5, k15, k1h, **gen)
    return SIGNAL_CACHE[key]


def score_row(row: dict) -> float:
    """Higher = better. Prioritize OOS avg with min sample + stability."""
    te = row.get("test") or {}
    tr = row.get("train") or {}
    te_avg = te.get("avg")
    te_n = te.get("n") or 0
    te_sh = te.get("sharpe") or 0
    tr_avg = tr.get("avg") or 0
    if te_avg is None or te_n < 30:
        return -999.0
    overfit_pen = max(0.0, (tr_avg - te_avg) - 5.0) * 0.5 if tr else 0.0
    sample_bonus = min(1.0, te_n / 100.0)
    return te_avg * sample_bonus + 0.4 * (te_sh or 0) - overfit_pen


def fast_eval(signals, k5, k1h, *, sigma, spread, exit_kw, dynamic_sigma=False,
              test_only: bool = False) -> dict:
    """Train+test stats; test_only skips train for fast screening."""
    if not signals:
        return {"train": None, "test": None}
    train, test = split_signals(signals, split_pct=0.70)

    def _sim(sigs):
        if not sigs:
            return None
        sims = simulate_signal_set(
            sigs, k5, sigma=sigma, expiry_hours=168.0,
            tp1_pct=exit_kw["tp1"], tp2_pct=exit_kw["tp2"], sl_pct=exit_kw["sl"],
            option_horizon_h=exit_kw["hold_h"], spread_pct=spread,
            klines_1h=k1h if dynamic_sigma else None,
            dynamic_sigma=dynamic_sigma,
        )
        pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
        if not pnls:
            return None
        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        s = statistics.stdev(pnls) if len(pnls) > 1 else 0
        sh = (statistics.mean(pnls) / s) if s > 0 else None
        return {
            "n": len(pnls), "wr": round(wr, 3),
            "avg": round(statistics.mean(pnls), 2),
            "median": round(statistics.median(pnls), 2),
            "stdev": round(s, 2),
            "sharpe": round(sh, 2) if sh is not None else None,
            "total": round(sum(pnls), 1),
        }

    if test_only:
        return {"train": None, "test": _sim(test)}
    return {"train": _sim(train), "test": _sim(test)}


def run_combo(k5, k15, k1h, gen: dict, exit_kw: dict, *, sigma: float, spread: float,
              dynamic_sigma: bool = False, test_only: bool = False) -> dict:
    signals = get_signals(k5, k15, k1h, gen)
    res = fast_eval(
        signals, k5, k1h,
        sigma=sigma, spread=spread, exit_kw=exit_kw,
        dynamic_sigma=dynamic_sigma, test_only=test_only,
    )
    return {
        "gen": gen,
        "exit": exit_kw,
        "sigma": sigma,
        "spread": spread,
        "dynamic_sigma": dynamic_sigma,
        "n_signals": len(signals),
        **res,
    }


EXITS_SHORT = [
    {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24, "lbl": "decay_24h"},
    {"tp1": 0.40, "tp2": 0.60, "sl": 1.00, "hold_h": 48, "lbl": "decay_48h_wide"},
    {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "lbl": "decay_72h_widest"},
    {"tp1": 0.25, "tp2": 0.45, "sl": 0.40, "hold_h": 18, "lbl": "tight_18h"},
    {"tp1": 0.35, "tp2": 0.55, "sl": 0.70, "hold_h": 36, "lbl": "med_36h"},
    {"tp1": 0.30, "tp2": 0.80, "sl": 0.40, "hold_h": 24, "lbl": "wide_tp2_24h"},
]


def grid_broad() -> list[tuple[dict, dict, str]]:
    """Round 1: focused exploration (~54 combos)."""
    combos = []
    specs = [
        # C + MTF down (current live direction)
        ("C", "down", [0.55, 0.60, 0.65, 0.70], [6, 12], [("range", "transition")],
         [None, 1.03, 1.05], EXITS_SHORT[:3]),
        # P + MTF up (iter4 co-winner)
        ("P", "up", [0.50, 0.55, 0.60], [6, 12], [("range",), ("range", "transition")],
         [None, 1.05], EXITS_SHORT[:3]),
    ]
    for side, mtf, vols, cds, regimes_list, bulls, exits in specs:
        for vol, cd, regimes, bull, ex in product(vols, cds, regimes_list, bulls, exits):
            gen = {
                "vol_threshold": vol,
                "regime_filter": list(regimes),
                "side": side,
                "adx_max": None,
                "mtf_direction_filter": mtf,
                "bull_market_ratio_max": bull,
                "cooldown_bars": cd,
            }
            label = (f"sp.{side}_mtf{mtf}.cd{cd}.v{vol}."
                     f"{'+'.join(regimes)}.bull{bull}.{ex['lbl']}")
            combos.append((gen, ex, label))
    return combos


def grid_put_refine() -> list[tuple[dict, dict, str]]:
    """Round 3: deep refine around sell-Put MTF-up winner (~72 combos)."""
    combos = []
    top_exits = [EXITS_SHORT[2], EXITS_SHORT[1], EXITS_SHORT[3]]  # 72h, 48h, 36h
    for vol, cd, bull, regimes, ex in product(
        [0.45, 0.50, 0.55],
        [6, 12],
        [None, 1.05, 1.08],
        [("range",), ("range", "transition")],
        top_exits,
    ):
        gen = {
            "vol_threshold": vol,
            "regime_filter": list(regimes),
            "side": "P",
            "adx_max": None,
            "mtf_direction_filter": "up",
            "bull_market_ratio_max": bull,
            "cooldown_bars": cd,
        }
        label = (f"put.v{vol}.cd{cd}.bull{bull}.{'+'.join(regimes)}.{ex['lbl']}")
        combos.append((gen, ex, label))
    return combos


def _grid_put_refine_adx(seeds: list[dict]) -> list[tuple[dict, dict, str]]:
    """ADX cap on top-3 put seeds only."""
    combos = []
    for seed in sorted(seeds, key=score_row, reverse=True)[:3]:
        for adx in [18, 22, 25]:
            gen = {**seed["gen"], "adx_max": adx}
            combos.append((gen, seed["exit"], f"adx{adx}.{seed.get('label','')[:40]}"))
    return combos


def grid_refine(seeds: list[dict]) -> list[tuple[dict, dict, str]]:
    """Round 2+: perturb top seeds."""
    combos = []
    seen = set()
    for seed in seeds[:5]:
        g0 = seed["gen"]
        e0 = seed["exit"]
        vol0 = g0["vol_threshold"]
        cd0 = g0["cooldown_bars"]
        bull0 = g0.get("bull_market_ratio_max")
        for dvol in [-0.05, 0, 0.05]:
            for dcd in [-6, 0, 6]:
                for bull in {bull0, 1.03, 1.05, 1.08, None}:
                    for ex in EXITS_SHORT:
                        vol = round(max(0.45, min(0.80, vol0 + dvol)), 2)
                        cd = max(3, cd0 + dcd)
                        gen = {
                            **g0,
                            "vol_threshold": vol,
                            "cooldown_bars": cd,
                            "bull_market_ratio_max": bull,
                        }
                        key = (gen_key(gen), ex["lbl"])
                        if key in seen:
                            continue
                        seen.add(key)
                        label = f"refine.{gen_key(gen)}.{ex['lbl']}"
                        combos.append((gen, ex, label))
    return combos


def grid_adx(seeds: list[dict]) -> list[tuple[dict, dict, str]]:
    combos = []
    for seed in seeds[:3]:
        g0 = dict(seed["gen"])
        e0 = seed["exit"]
        for adx in [None, 18, 22, 25]:
            gen = {**g0, "adx_max": adx}
            combos.append((gen, e0, f"adx{adx}.{seed.get('label','')}"))
    return combos


def _worker_task(payload: tuple) -> dict:
    gen, ex, label, sigma, spread, dynamic_sigma, test_only = payload
    row = run_combo(_K5, _K15, _K1H, gen, ex, sigma=sigma, spread=spread,
                    dynamic_sigma=dynamic_sigma, test_only=test_only)
    row["label"] = label
    row["score"] = score_row(row)
    return row


def print_top(results: list[dict], n: int = 15) -> None:
    ranked = sorted(results, key=score_row, reverse=True)
    print(f"\n{'='*90}")
    print(f"TOP {n} by OOS score (test_avg × sample + sharpe − overfit penalty)")
    print(f"{'label':<55} {'tr_n':>5} {'tr_avg':>7} {'te_n':>5} {'te_avg':>7} {'te_sh':>6} {'score':>6}")
    for r in ranked[:n]:
        tr, te = r.get("train") or {}, r.get("test") or {}
        print(f"{r['label'][:55]:<55} {tr.get('n',0):>5} {tr.get('avg',0):>+6.2f}% "
              f"{te.get('n',0):>5} {te.get('avg',0):>+6.2f}% {te.get('sharpe',0):>+5.2f} "
              f"{score_row(r):>6.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--round", choices=["broad", "refine", "put_refine", "adx", "sigma"], default="broad")
    ap.add_argument("--seed", default=None, help="prior results JSON for refine/adx/sigma")
    ap.add_argument("--sigma", type=float, default=0.6)
    ap.add_argument("--spread", type=float, default=2.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--test-only", action="store_true", help="screen on OOS test split only")
    ap.add_argument("--dynamic-sigma", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[2]
    out_path = Path(args.out) if args.out else repo / "sweep_results" / f"local_opt_{args.round}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data_dir = find_data_dir(args.data_dir)
    print(f"=== Local optimizer — round={args.round} ===", flush=True)
    print(f"  data: {data_dir}", flush=True)

    t0 = time.time()
    k5, k15, k1h = load_local(data_dir)
    print(f"  klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}", flush=True)

    seeds: list[dict] = []
    if args.seed:
        seeds = json.loads(Path(args.seed).read_text()).get("results", [])

    if args.round == "broad":
        combos = grid_broad()
    elif args.round == "put_refine":
        combos = grid_put_refine()
    elif args.round == "refine":
        if not seeds:
            raise SystemExit("--seed required for refine round")
        combos = grid_refine(seeds)
    elif args.round == "adx":
        if not seeds:
            raise SystemExit("--seed required for adx round")
        combos = grid_adx(seeds)
    else:  # sigma
        if not seeds:
            raise SystemExit("--seed required for sigma round")
        combos = []
        top = sorted(seeds, key=score_row, reverse=True)[:3]
        for s in top:
            for ex in [s["exit"]]:
                combos.append((s["gen"], ex, f"sigma_sweep.{s.get('label','')[:30]}"))

    results: list[dict] = []
    print(f"\nRunning {len(combos)} combos...", flush=True)

    if args.round == "sigma":
        top = sorted(seeds, key=score_row, reverse=True)[:3]
        for s in top:
            for sigma in [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]:
                for spread in [1.5, 2.0, 2.5, 3.0]:
                    for dyn in [False, True]:
                        row = run_combo(k5, k15, k1h, s["gen"], s["exit"],
                                        sigma=sigma, spread=spread, dynamic_sigma=dyn)
                        row["label"] = f"{s.get('label','')[:40]}.s{sigma}.sp{spread}.dyn{dyn}"
                        row["score"] = score_row(row)
                        results.append(row)
                        te = row.get("test") or {}
                        print(f"  [{len(results)}/{len(top)*7*4*2}] {row['label'][:60]} "
                              f"te={te.get('avg','?'):+} n={te.get('n',0)}", flush=True)
    else:
        tasks = [(gen, ex, label, args.sigma, args.spread, args.dynamic_sigma, args.test_only)
                 for gen, ex, label in combos]
        workers = max(1, min(args.workers, len(tasks)))
        print(f"  parallel workers: {workers}", flush=True)
        if workers == 1:
            for i, task in enumerate(tasks, 1):
                row = _worker_task(task)
                results.append(row)
                if i % 5 == 0 or i == len(tasks):
                    print(f"  [{i}/{len(tasks)}] best score={max(results, key=score_row)['score']:.2f}",
                          flush=True)
        else:
            with mp.Pool(workers, initializer=_init_worker, initargs=(k5, k15, k1h)) as pool:
                for i, row in enumerate(pool.imap_unordered(_worker_task, tasks), 1):
                    results.append(row)
                    if i % 5 == 0 or i == len(tasks):
                        print(f"  [{i}/{len(tasks)}] best score={max(results, key=score_row)['score']:.2f}",
                              flush=True)

    print_top(results)
    elapsed = round(time.time() - t0, 1)
    payload = {
        "round": args.round,
        "sigma": args.sigma,
        "spread": args.spread,
        "elapsed_s": elapsed,
        "n_combos": len(results),
        "results": sorted(results, key=score_row, reverse=True),
        "best": sorted(results, key=score_row, reverse=True)[0] if results else None,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nSaved → {out_path} ({elapsed}s)", flush=True)

    best = payload["best"]
    if best:
        te = best.get("test") or {}
        tr = best.get("train") or {}
        print(f"\nBEST: {best['label']}")
        if tr:
            print(f"  train: n={tr.get('n')} avg={tr.get('avg'):+.2f}%")
        print(f"  test:  n={te.get('n')} avg={te.get('avg'):+.2f}% sharpe={te.get('sharpe')}")


if __name__ == "__main__":
    main()

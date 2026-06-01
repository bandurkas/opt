"""Mega-sweep: exhaustive parameter search for sell_premium strategy.

Extends local_optimizer with:
- Wider parameter ranges (vol 0.35-0.75, cd 2-12, hold 48-168)
- ADX caps (None, 15, 20, 25)
- Bull filter variations (None, 1.03, 1.05, 1.08)
- Tighter SL variations (0.50-1.50)
- Both Put MTF-up AND Call MTF-down
- Holdout protocol (90d unseen for final validation)

Usage:
    cd backend && PYTHONPATH=. python3 services/mega_sweep.py
    cd backend && PYTHONPATH=. python3 services/mega_sweep.py --round put_mega
    cd backend && PYTHONPATH=. python3 services/mega_sweep.py --round call_explorer
    cd backend && PYTHONPATH=. python3 services/mega_sweep.py --round exits
    cd backend && PYTHONPATH=. python3 services/mega_sweep.py --round hybrid
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
from services.holdout_split import HOLDOUT_DAYS, holdout_cutoff_ms, split_signals_by_holdout
from services.local_optimizer import find_data_dir, load_local, score_row
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
    return hashlib.md5(json.dumps(gen, sort_keys=True).encode()).hexdigest()[:12]


def get_signals(k5, k15, k1h, gen: dict) -> list:
    """Full-history signals. Split by holdout in eval."""
    key = gen_key(gen)
    if key not in SIGNAL_CACHE:
        SIGNAL_CACHE[key] = gen_sell_premium_iv_high(k5, k15, k1h, **gen)
    return SIGNAL_CACHE[key]


def split_train_test(signals, k5, split_pct=0.70):
    """Split signals by holdout cutoff, then 70/30 within train pool."""
    cutoff = holdout_cutoff_ms(k5)
    train_pool, holdout = split_signals_by_holdout(signals, cutoff)
    if len(train_pool) < 10:
        return train_pool, [], holdout
    idx = int(len(train_pool) * split_pct)
    return train_pool[:idx], train_pool[idx:], holdout


def fast_eval(signals_train, signals_test, k5, k1h, *, sigma, spread, exit_kw,
              dynamic_sigma=False) -> dict:
    """Train + test stats. Returns {train: stats|None, test: stats|None}."""
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
        st = statistics.stdev(pnls) if len(pnls) > 1 else 0
        sh = (statistics.mean(pnls) / st) if st > 0 else None
        # Monthly breakdown for max DD estimation
        from datetime import datetime, timezone
        monthly = {}
        for s in sims:
            opt = s.get("option", {})
            if "pnl_pct" not in opt:
                continue
            ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
            m = ts.strftime("%Y-%m")
            monthly.setdefault(m, []).append(opt["pnl_pct"])
        losing_months = sum(1 for m, ps in monthly.items() if statistics.mean(ps) < 0)
        max_consec_loss = _max_consec_loss(pnls)
        return {
            "n": len(pnls), "wr": round(wr, 3),
            "avg": round(statistics.mean(pnls), 2),
            "median": round(statistics.median(pnls), 2),
            "stdev": round(st, 2),
            "sharpe": round(sh, 2) if sh is not None else None,
            "total": round(sum(pnls), 1),
            "losing_months": losing_months,
            "total_months": len(monthly),
            "max_consec_loss": max_consec_loss,
        }

    tr = _sim(signals_train)
    te = _sim(signals_test)
    return {"train": tr, "test": te}


def _max_consec_loss(pnls: list[float]) -> int:
    mx = cl = 0
    for p in pnls:
        if p < 0:
            cl += 1
            mx = max(mx, cl)
        else:
            cl = 0
    return mx


def score_row_v2(row: dict) -> float:
    """Improved scoring: heavily penalize max_consec_loss and losing_months."""
    te = row.get("test") or {}
    tr = row.get("train") or {}
    te_avg = te.get("avg")
    te_n = te.get("n") or 0
    te_sh = te.get("sharpe") or 0
    tr_avg = tr.get("avg") or 0
    max_cl = te.get("max_consec_loss", 0)
    losing_mo = te.get("losing_months", 0)
    total_mo = te.get("total_months", 1)

    if te_avg is None or te_n < 20:
        return -999.0

    # Base score: OOS avg weighted by sample size
    sample_bonus = min(1.5, te_n / 50.0)
    base = te_avg * sample_bonus

    # Sharpe bonus
    sh_bonus = 2.0 * (te_sh if te_sh > 0 else 0)

    # Overfit penalty
    overfit_pen = max(0.0, (tr_avg - te_avg) - 3.0) * 0.5 if tr else 0.0

    # Consecutive loss penalty (critical!)
    cl_pen = max_cl * 0.8  # -0.8 per consecutive loss

    # Losing months penalty
    losing_mo_pct = losing_mo / max(total_mo, 1)
    mo_pen = losing_mo_pct * 15.0  # up to -15 if all months lose

    return base + sh_bonus - overfit_pen - cl_pen - mo_pen


# ─── Exit grids ───

EXITS = [
    {"tp1": 0.25, "tp2": 0.45, "sl": 0.50, "hold_h": 24, "lbl": "tight_24h_sl50"},
    {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24, "lbl": "decay_24h_sl50"},
    {"tp1": 0.30, "tp2": 0.50, "sl": 0.75, "hold_h": 24, "lbl": "decay_24h_sl75"},
    {"tp1": 0.30, "tp2": 0.50, "sl": 1.00, "hold_h": 24, "lbl": "decay_24h_sl100"},
    {"tp1": 0.30, "tp2": 0.50, "sl": 1.50, "hold_h": 24, "lbl": "decay_24h_sl150"},
    {"tp1": 0.40, "tp2": 0.60, "sl": 0.75, "hold_h": 48, "lbl": "decay_48h_sl75"},
    {"tp1": 0.40, "tp2": 0.60, "sl": 1.00, "hold_h": 48, "lbl": "decay_48h_sl100"},
    {"tp1": 0.40, "tp2": 0.60, "sl": 1.50, "hold_h": 48, "lbl": "decay_48h_sl150"},
    {"tp1": 0.50, "tp2": 0.70, "sl": 1.00, "hold_h": 72, "lbl": "decay_72h_sl100"},
    {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "lbl": "decay_72h_sl150"},
    {"tp1": 0.50, "tp2": 0.70, "sl": 2.00, "hold_h": 72, "lbl": "decay_72h_sl200"},
    {"tp1": 0.50, "tp2": 0.70, "sl": 1.00, "hold_h": 96, "lbl": "decay_96h_sl100"},
    {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 96, "lbl": "decay_96h_sl150"},  # current LIVE
    {"tp1": 0.50, "tp2": 0.70, "sl": 2.00, "hold_h": 96, "lbl": "decay_96h_sl200"},
    {"tp1": 0.60, "tp2": 0.80, "sl": 1.50, "hold_h": 120, "lbl": "decay_120h_sl150"},
    {"tp1": 0.60, "tp2": 0.80, "sl": 2.00, "hold_h": 120, "lbl": "decay_120h_sl200"},
    {"tp1": 0.60, "tp2": 0.80, "sl": 2.50, "hold_h": 120, "lbl": "decay_120h_sl250"},
    {"tp1": 0.70, "tp2": 1.00, "sl": 2.00, "hold_h": 144, "lbl": "decay_144h_sl200"},
    {"tp1": 0.70, "tp2": 1.00, "sl": 3.00, "hold_h": 144, "lbl": "decay_144h_sl300"},
    {"tp1": 0.80, "tp2": 1.20, "sl": 2.00, "hold_h": 168, "lbl": "decay_168h_sl200"},
    {"tp1": 0.80, "tp2": 1.20, "sl": 3.00, "hold_h": 168, "lbl": "decay_168h_sl300"},
]


# ─── Round definitions ───

def grid_put_mega() -> list[tuple[dict, dict, str]]:
    """Mega-sweep Put MTF-up: all dimensions."""
    combos = []
    for vol, cd, hold, regime, adx, bull, ex in product(
        [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70],
        [2, 3, 4, 5, 6, 8, 10, 12],
        [48, 72, 96, 120, 144, 168],
        [("range",), ("range", "transition")],
        [None, 15, 20, 25],
        [None, 1.03, 1.05, 1.08],
        [e for e in EXITS if e["hold_h"] in [48, 72, 96, 120]],  # subset for speed
    ):
        # Skip invalid: hold_h > 168
        gen = {
            "vol_threshold": vol,
            "regime_filter": list(regime),
            "side": "P",
            "adx_max": adx,
            "mtf_direction_filter": "up",
            "bull_market_ratio_max": bull,
            "cooldown_bars": cd,
        }
        label = (f"P.v{vol}.cd{cd}.h{hold}.{'r' if regime == ('range',) else 'rt'}"
                 f".{f'adx{adx}' if adx else 'noadx'}.{f'bull{bull}' if bull else 'nobull'}.{ex['lbl']}")
        combos.append((gen, ex, label))
    return combos


def grid_call_explorer() -> list[tuple[dict, dict, str]]:
    """Explore Call MTF-down — broader ranges, fewer exit combos."""
    call_exits = [e for e in EXITS if e["hold_h"] in [24, 48, 72, 96]]
    combos = []
    for vol, cd, hold, regime, adx, bull, ex in product(
        [0.45, 0.50, 0.55, 0.60, 0.65, 0.70],
        [3, 4, 6, 8, 12],
        [24, 48, 72, 96],
        [("range",), ("range", "transition")],
        [None, 18, 22],
        [None, 1.05, 1.08, 1.10],
        call_exits,
    ):
        gen = {
            "vol_threshold": vol,
            "regime_filter": list(regime),
            "side": "C",
            "adx_max": adx,
            "mtf_direction_filter": "down",
            "bull_market_ratio_max": bull,
            "cooldown_bars": cd,
        }
        label = (f"C.v{vol}.cd{cd}.h{hold}.{'r' if regime == ('range',) else 'rt'}"
                 f".{f'adx{adx}' if adx else 'noadx'}.{f'bull{bull}' if bull else 'nobull'}.{ex['lbl']}")
        combos.append((gen, ex, label))
    return combos


def grid_put_refine(seeds: list[dict]) -> list[tuple[dict, dict, str]]:
    """Refine around top Put seeds from mega-sweep."""
    combos = []
    seen = set()
    for seed in sorted(seeds, key=score_row_v2, reverse=True)[:10]:
        g0 = seed["gen"]
        e0 = seed["exit"]
        vol0 = g0["vol_threshold"]
        cd0 = g0["cooldown_bars"]
        hold0 = e0["hold_h"]
        for dvol in [-0.05, 0.0, 0.05]:
            for dcd in [-2, -1, 0, 1, 2]:
                for dhold in [-24, 0, 24]:
                    for ex in EXITS:
                        vol = round(max(0.30, min(0.80, vol0 + dvol)), 2)
                        cd = max(2, cd0 + dcd)
                        hold = max(24, hold0 + dhold)
                        gen = {**g0, "vol_threshold": vol, "cooldown_bars": cd}
                        ex2 = {**ex, "hold_h": hold}
                        key = (gen_key(gen), ex2["lbl"], hold)
                        if key in seen:
                            continue
                        seen.add(key)
                        label = f"refine.{gen_key(gen)}.{ex2['lbl']}.h{hold}"
                        combos.append((gen, ex2, label))
    return combos


def _worker_task(payload: tuple) -> dict:
    gen, ex, label, sigma, spread = payload
    signals = get_signals(_K5, _K15, _K1H, gen)
    tr_sigs, te_sigs, _ = split_train_test(signals, _K5)
    res = fast_eval(tr_sigs, te_sigs, _K5, _K1H,
                    sigma=sigma, spread=spread, exit_kw=ex)
    row = {
        "gen": gen, "exit": ex, "label": label,
        "n_signals": len(signals),
        **res,
    }
    row["score"] = score_row_v2(row)
    return row


def print_top(results: list[dict], n: int = 20) -> None:
    ranked = sorted(results, key=score_row_v2, reverse=True)
    hdr = (f"{'label':<70} {'sig':>4} {'tr_n':>5} {'tr_avg':>7} "
           f"{'te_n':>5} {'te_avg':>7} {'te_sh':>6} {'cl':>4} {'lm':>4} {'score':>7}")
    print(f"\n{'='*130}")
    print(f"TOP {n} by OOS score (avg×sample+sharpe−overfit−consec_loss−losing_months)")
    print(hdr)
    for r in ranked[:n]:
        tr, te = r.get("train") or {}, r.get("test") or {}
        cl = te.get("max_consec_loss", 0)
        lm = te.get("losing_months", 0)
        print(f"{r['label'][:70]:<70} {r['n_signals']:>4} "
              f"{tr.get('n', 0):>5} {tr.get('avg', 0):>+6.2f}% "
              f"{te.get('n', 0):>5} {te.get('avg', 0):>+6.2f}% {te.get('sharpe', 0):>+5.2f} "
              f"{cl:>4} {lm:>4} {score_row_v2(r):>7.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--round", choices=["put_mega", "call_explorer", "put_refine", "exits_only"],
                    default="put_mega")
    ap.add_argument("--seed", default=None, help="prior results JSON for refine")
    ap.add_argument("--sigma", type=float, default=0.6)
    ap.add_argument("--spread", type=float, default=2.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[2]
    out_name = f"mega_sweep_{args.round}.json"
    out_path = Path(args.out) if args.out else repo / "sweep_results" / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data_dir = find_data_dir(args.data_dir)
    print(f"=== MEGA SWEEP — round={args.round} ===", flush=True)
    print(f"  data: {data_dir}", flush=True)

    t0 = time.time()
    k5, k15, k1h = load_local(data_dir)
    print(f"  klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}", flush=True)
    print(f"  holdout: last {HOLDOUT_DAYS}d (signals after cutoff reserved for validation)", flush=True)

    seeds = []
    if args.seed:
        seeds = json.loads(Path(args.seed).read_text()).get("results", [])

    if args.round == "put_mega":
        combos = grid_put_mega()
    elif args.round == "call_explorer":
        combos = grid_call_explorer()
    elif args.round == "put_refine":
        if not seeds:
            raise SystemExit("--seed required for refine")
        combos = grid_put_refine(seeds)
    else:
        # exits_only — use current LIVE gen kwargs, sweep only exits
        from services.strategy_config import LIVE_GEN_KWARGS
        combos = [(dict(LIVE_GEN_KWARGS), ex, f"exits.{ex['lbl']}") for ex in EXITS]

    print(f"\nRunning {len(combos)} combos with {args.workers} workers...", flush=True)

    results: list[dict] = []
    tasks = [(gen, ex, label, args.sigma, args.spread) for gen, ex, label in combos]

    workers = max(1, min(args.workers, len(tasks)))
    if workers == 1:
        for i, task in enumerate(tasks, 1):
            row = _worker_task(task)
            results.append(row)
            if i % 10 == 0 or i == len(tasks):
                print(f"  [{i}/{len(tasks)}] best={max(results, key=score_row_v2)['score']:.2f}", flush=True)
    else:
        with mp.Pool(workers, initializer=_init_worker, initargs=(k5, k15, k1h)) as pool:
            for i, row in enumerate(pool.imap_unordered(_worker_task, tasks), 1):
                results.append(row)
                if i % 10 == 0 or i == len(tasks):
                    print(f"  [{i}/{len(tasks)}] best={max(results, key=score_row_v2)['score']:.2f}",
                          flush=True)

    print_top(results, n=25)

    elapsed = round(time.time() - t0, 1)
    best = sorted(results, key=score_row_v2, reverse=True)[0] if results else None
    payload = {
        "round": args.round,
        "sigma": args.sigma,
        "spread": args.spread,
        "elapsed_s": elapsed,
        "n_combos": len(results),
        "results": sorted(results, key=score_row_v2, reverse=True)[:200],  # top 200
        "best": best,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nSaved → {out_path} ({elapsed}s)", flush=True)

    if best:
        te = best.get("test") or {}
        tr = best.get("train") or {}
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
        print(f"  score: {best['score']:.2f}")


if __name__ == "__main__":
    main()

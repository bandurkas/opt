"""BTC unconditional short-straddle sweep — tests the RAW variance-risk-premium
question directly (no ETH-tuned regime/MTF entry filters at all): does selling
BTC ATM premium on a fixed clock cadence have positive expectancy, for SOME
combination of cycle length / exit params / IV calibration?

Per-asset honest IV from multi_coin_signals.py: BTC IV~43% vs RVmed~39%
(IV/RV~1.10, a BIGGER raw VRP ratio than ETH's IV~59%/RVmed~61%, IV/RV~0.97).
The prior BTC rejections used the full ETH-calibrated V3 signal generator
(regime/MTF/ADX entry timing) — this isolates the VRP question from that
specific timing logic by selling on every clock tick, no entry filter.

Brute-force grid over 8 cores; SELECT best config by TRAIN Sharpe only (never
peek at holdout during selection), then report that one config's HOLDOUT
number as the actual answer — same discipline as iv_expiry_test.py /
range_audit.py elsewhere in this repo.

Run (plain python3, stdlib + repo modules only — no docker needed):
  cd backend && python3 services/btc_straddle_sweep.py
"""
from __future__ import annotations

import itertools
import multiprocessing as mp
import statistics as st
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set  # noqa: E402
from services.local_optimizer import find_data_dir  # noqa: E402
from services.multi_coin_signals import load_coin  # noqa: E402

COIN = sys.argv[1] if len(sys.argv) > 1 else "btc"
SIGMA_CLAMP = (0.20, 1.50)
SPREAD_PCT = 2.0
TRAIN_FRAC = 0.70

CYCLE_HOURS = (24.0, 48.0, 72.0, 168.0)
TP_COMBOS = ((0.30, 0.50), (0.40, 0.70), (0.50, 0.80))
SL_PCTS = (0.75, 1.00, 1.50, 2.00)
IV_RV_MULTS = (0.95, 1.00, 1.05, 1.10)


def build_periodic_signals(k5, cycle_h):
    step_bars = int(cycle_h * 60 / 5)
    warmup = 200
    sigs = []
    idx = warmup
    while idx < len(k5):
        close = k5[idx]["close"]
        ts_ms = k5[idx]["start_ms"] + 5 * 60 * 1000
        for side in ("C", "P"):
            sigs.append({"idx_5m": idx, "ts_ms": ts_ms, "close": close,
                         "side": side, "position": "short_premium", "_cycle": idx})
        idx += step_bars
    return sigs


def agg(pnls):
    if not pnls:
        return {"n": 0, "avg": 0.0, "sharpe": 0.0, "wr": 0.0}
    n = len(pnls)
    avg = sum(pnls) / n
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    return {"n": n, "avg": avg, "sharpe": sh, "wr": wr}


def fmt(a):
    return f"n{a['n']:>4} avg{a['avg']:>+6.2f}% WR{a['wr']*100:>3.0f}% Sh{a['sharpe']:>+5.2f}"


_K5 = _K1H = None  # loaded ONCE in main(); forked workers inherit via copy-on-write


def run_one(params):
    cycle_h, (tp1, tp2), sl, mult = params
    sigs = build_periodic_signals(_K5, cycle_h)
    out = simulate_signal_set(
        sigs, _K5, sigma=0.60, expiry_hours=cycle_h, tp1_pct=tp1, tp2_pct=tp2,
        sl_pct=sl, option_horizon_h=cycle_h, spread_pct=SPREAD_PCT,
        dynamic_sigma=True, klines_1h=_K1H, iv_rv_multiplier=mult,
        sigma_clamp=SIGMA_CLAMP,
    )
    by_cycle = {}
    for o in out:
        opt = o.get("option", {})
        if "pnl_pct" not in opt or opt.get("resolution") in ("no_entry", "no_data"):
            continue
        by_cycle.setdefault(o["_cycle"], {"ts_ms": o["ts_ms"]})[o["side"]] = opt["pnl_pct"]

    rows = []
    for c, d in sorted(by_cycle.items()):
        if "C" in d and "P" in d:
            rows.append((d["ts_ms"], (d["C"] + d["P"]) / 2))

    if not rows:
        return params, None

    ts_all = sorted(t for t, _ in rows)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    tr = [p for t, p in rows if t < split_ts]
    ho = [p for t, p in rows if t >= split_ts]
    return params, {"train": agg(tr), "holdout": agg(ho)}


def main():
    import random
    global _K5, _K1H
    data_dir = find_data_dir(None)
    print(f"loading {COIN} data...", flush=True)
    k5, k15, k1h = load_coin(COIN, data_dir)
    del k15  # not used by run_one; drop before forking to save memory
    days_back = float(sys.argv[2]) if len(sys.argv) > 2 else None
    if days_back is not None:
        cutoff_ms = k5[-1]["start_ms"] - int(days_back * 86_400_000)
        k5 = [c for c in k5 if c["start_ms"] >= cutoff_ms]
        k1h = [c for c in k1h if c["start_ms"] >= cutoff_ms]
    _K5, _K1H = k5, k1h
    print(f"  {len(k5)} 5m bars, {len(k1h)} 1h bars"
          f"{f' (trimmed to last {days_back:.0f}d)' if days_back else ''}\n", flush=True)

    grid = list(itertools.product(CYCLE_HOURS, TP_COMBOS, SL_PCTS, IV_RV_MULTS))
    # Work per config varies ~7x between cycle_h=24 (many cycles) and cycle_h=168
    # (few) — itertools.product groups same-cycle_h configs contiguously, so a
    # naive chunked pool.map starves some of the 8 workers near the end. Shuffle
    # + chunksize=1 keeps all cores saturated for the whole run.
    random.shuffle(grid)
    n_workers = mp.cpu_count()
    print(f"BTC unconditional short-straddle sweep — {len(grid)} configs, "
          f"{n_workers} cores (fork start method — data loaded once, shared "
          f"copy-on-write, NOT duplicated per worker)\n")

    # 'fork' (not the macOS default 'spawn') so child processes inherit _K5/_K1H
    # via copy-on-write instead of each re-loading+re-parsing the full dataset —
    # spawn would otherwise multiply memory use by n_workers and can OOM-kill on
    # multi-year history (the original run on 6y BTC data died silently this way).
    ctx = mp.get_context("fork")
    with ctx.Pool(processes=n_workers) as pool:
        results = pool.map(run_one, grid, chunksize=1)

    scored = [(p, r) for p, r in results if r is not None and r["train"]["n"] >= 10]
    scored.sort(key=lambda pr: pr[1]["train"]["sharpe"], reverse=True)

    print(f"{'cycle_h':>7} {'tp1/tp2':>9} {'sl':>5} {'mult':>5}   {'TRAIN':<28}   {'HOLDOUT':<28}")
    for p, r in scored[:15]:
        cycle_h, (tp1, tp2), sl, mult = p
        print(f"{cycle_h:>7.0f} {tp1:>4.2f}/{tp2:<4.2f} {sl:>5.2f} {mult:>5.2f}   {fmt(r['train'])}   {fmt(r['holdout'])}")

    if not scored:
        print("\nno config produced enough trades")
        return

    best_params, best = scored[0]
    cycle_h, (tp1, tp2), sl, mult = best_params
    print(f"\n=== BEST BY TRAIN SHARPE (selected without looking at holdout) ===")
    print(f"cycle_h={cycle_h} tp1={tp1} tp2={tp2} sl={sl} iv_rv_mult={mult}")
    print(f"  TRAIN:   {fmt(best['train'])}")
    print(f"  HOLDOUT: {fmt(best['holdout'])}   <-- the real answer")


if __name__ == "__main__":
    main()

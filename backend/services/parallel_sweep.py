"""Parallel cd/vol/hold sweep on the bull=None Put winner — max-profit search.

Uses multiprocessing.Pool to load all available CPU cores. Each worker reuses
a shared signal cache for the cd × vol unique gen-configs; per-cell sims
(different hold_h) are cheap.

Grid (54 cells):
  cooldown_bars ∈ {3, 4, 5, 6, 8, 12}
  vol_threshold ∈ {0.45, 0.50, 0.55}
  hold_h        ∈ {48, 72, 96}
  bull=None, side=P, mtf=up, regime=range, tp/sl from current LIVE_EXIT.

Each cell evaluated on holdout (proper, last-90d, never seen by signal-gen).
Ranking by monthly $ on \$400 base = (holdout n / 3 months) × avg × \$8/trade.
"""
from __future__ import annotations

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
from services.local_optimizer import find_data_dir, load_local
from services.strategy_config import LIVE_EXIT
from services.strategy_registry import gen_sell_premium_iv_high


_K5 = None
_K15 = None
_K1H = None
_SIG_CACHE: dict = {}


def _gen_key(cd: int, vol: float) -> str:
    return f"cd{cd}_vol{vol}"


def _init_worker(k5, k15, k1h):
    global _K5, _K15, _K1H
    _K5, _K15, _K1H = k5, k15, k1h


def _eval_cell(args: tuple) -> dict:
    cd, vol, hold_h = args
    gen = {
        "vol_threshold": vol,
        "regime_filter": ["range"],
        "side": "P",
        "adx_max": None,
        "mtf_direction_filter": "up",
        "bull_market_ratio_max": None,
        "cooldown_bars": cd,
    }
    key = _gen_key(cd, vol)
    if key not in _SIG_CACHE:
        _SIG_CACHE[key] = gen_sell_premium_iv_high(_K5, _K15, _K1H, **gen)
    sigs = _SIG_CACHE[key]
    cutoff = holdout_cutoff_ms(_K5)
    train_pool, holdout = split_signals_by_holdout(sigs, cutoff)
    if len(holdout) < 10:
        return {"cd": cd, "vol": vol, "hold_h": hold_h, "skip": "few-holdout", "n": len(holdout)}

    sims = simulate_signal_set(
        holdout, _K5,
        sigma=0.6, expiry_hours=168.0,
        tp1_pct=LIVE_EXIT["tp1_pct"], tp2_pct=LIVE_EXIT["tp2_pct"],
        sl_pct=LIVE_EXIT["sl_pct"], option_horizon_h=hold_h,
        spread_pct=2.0,
    )
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return {"cd": cd, "vol": vol, "hold_h": hold_h, "skip": "no-pnl"}

    stdev = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    avg = statistics.mean(pnls)
    # Monthly $ on $400 base, ~$8 credit per trade (1 lot ATM ETH 7d Put)
    monthly_dollar = (len(pnls) / 3.0) * (avg / 100.0) * 8.0
    return {
        "cd": cd, "vol": vol, "hold_h": hold_h,
        "n_train": len(train_pool), "n_holdout": len(pnls),
        "wr": round(wr, 3), "avg_pct": round(avg, 2),
        "stdev": round(stdev, 2),
        "sharpe": round(avg / stdev, 3) if stdev > 0 else None,
        "total_pct": round(sum(pnls), 1),
        "monthly_dollar_400": round(monthly_dollar, 2),
        "monthly_pct_400": round(monthly_dollar / 400 * 100, 2),
    }


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    out_path = repo / "sweep_results" / "parallel_cd_vol_hold.json"

    cds = [3, 4, 5, 6, 8, 12]
    vols = [0.45, 0.50, 0.55]
    holds = [48, 72, 96]
    cells = list(product(cds, vols, holds))

    print(f"=== Parallel sweep — {len(cells)} cells "
          f"(cd × vol × hold), bull=None, side=P, mtf=up ===", flush=True)
    print(f"  HOLDOUT_DAYS={HOLDOUT_DAYS}, sigma=0.6, spread=2%", flush=True)

    t0 = time.time()
    k5, k15, k1h = load_local(find_data_dir(None))
    print(f"  klines: 5m={len(k5):,} bars  cutoff={holdout_cutoff_ms(k5)}", flush=True)

    workers = mp.cpu_count() - 1
    print(f"\nRunning across {workers} workers...", flush=True)
    results = []
    with mp.Pool(workers, initializer=_init_worker, initargs=(k5, k15, k1h)) as pool:
        for i, row in enumerate(pool.imap_unordered(_eval_cell, cells), 1):
            results.append(row)
            tag = f"cd{row['cd']}/v{row['vol']}/h{row['hold_h']}"
            if "skip" in row:
                print(f"  [{i:>2}/{len(cells)}] {tag:<22} SKIP ({row['skip']})", flush=True)
            else:
                print(
                    f"  [{i:>2}/{len(cells)}] {tag:<22} "
                    f"n={row['n_holdout']:>3} avg={row['avg_pct']:>+6.2f}% "
                    f"sharpe={row['sharpe'] if row['sharpe'] is not None else 0:>+5.3f} "
                    f"\\${row['monthly_dollar_400']:>+7.2f}/mo (+{row['monthly_pct_400']}%)",
                    flush=True,
                )

    valid = [r for r in results if "monthly_dollar_400" in r]
    by_dollar = sorted(valid, key=lambda r: r["monthly_dollar_400"], reverse=True)
    by_sharpe = sorted(valid, key=lambda r: r["sharpe"] or 0, reverse=True)

    print("\n=== TOP 10 by monthly $/400 ===", flush=True)
    for r in by_dollar[:10]:
        tag = f"cd{r['cd']}/v{r['vol']}/h{r['hold_h']}"
        print(f"  {tag:<22} n={r['n_holdout']:>3} avg={r['avg_pct']:>+6.2f}% "
              f"sharpe={r['sharpe']:>+5.3f} ${r['monthly_dollar_400']:>+7.2f}/mo "
              f"(+{r['monthly_pct_400']}%)", flush=True)

    print("\n=== TOP 10 by per-trade Sharpe ===", flush=True)
    for r in by_sharpe[:10]:
        tag = f"cd{r['cd']}/v{r['vol']}/h{r['hold_h']}"
        print(f"  {tag:<22} sharpe={r['sharpe']:>+5.3f} avg={r['avg_pct']:>+6.2f}% "
              f"${r['monthly_dollar_400']:>+7.2f}/mo", flush=True)

    elapsed = round(time.time() - t0, 1)
    payload = {
        "grid": {"cds": cds, "vols": vols, "holds": holds},
        "base": {"side": "P", "mtf": "up", "regime": ["range"],
                 "bull": None, "exit_tp1": LIVE_EXIT["tp1_pct"],
                 "exit_tp2": LIVE_EXIT["tp2_pct"], "exit_sl": LIVE_EXIT["sl_pct"]},
        "elapsed_s": elapsed,
        "ranking_by_dollar": by_dollar,
        "ranking_by_sharpe": by_sharpe,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nSaved {out_path}  ({elapsed}s)", flush=True)


if __name__ == "__main__":
    main()

"""Phase 4: profit-max experiments on the verified winner.

Reads the latest finalize_best winner from sweep_results/final_validation.json
and probes three orthogonal levers:

  1. **Hybrid strategy**  — sell Put when MTF-up; sell Call when MTF-down.
     Doubles signal frequency if Call-side has its own (smaller) edge with
     wide exits. Generates two signal sets, merges, dedupes by ts_ms.

  2. **Expiry sensitivity** — re-run winner at 72h / 120h / 168h / 240h
     option expiries (hold_h tracks expiry). Shorter expiry = more theta
     per day; longer = bigger credit but slower decay.

  3. **IV-skew calibration** — re-price with sigma in {0.50, 0.60, 0.70, 0.80}.
     Real ETH Put 7d IV runs 0.65-0.85 vs BS sigma=0.60 baseline; this maps
     the BS-simulated P&L to a realistic range.

All experiments are evaluated on the holdout window (90d unseen) using the
shared protocol from holdout_split.py. The winner-config from
final_validation.json is the base; only the lever under test changes.

Run:
    cd backend
    PYTHONPATH=. python3 services/profit_experiments.py \\
        --winner ../sweep_results/final_validation.json \\
        --out    ../sweep_results/profit_experiments.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from copy import deepcopy
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.holdout_split import HOLDOUT_DAYS, holdout_cutoff_ms, split_signals_by_holdout
from services.local_optimizer import find_data_dir, get_full_signals, load_local
from services.strategy_registry import gen_sell_premium_iv_high


def _exit_sim(ex: dict) -> dict:
    if "tp1_pct" in ex:
        return {"tp1": ex["tp1_pct"], "tp2": ex["tp2_pct"], "sl": ex["sl_pct"], "hold_h": ex["hold_h"]}
    return ex


def _eval_on_holdout(k5, k15, k1h, signals, exit_kw, *, sigma, spread, expiry_hours) -> dict:
    """Run simulate on holdout slice only."""
    cutoff = holdout_cutoff_ms(k5)
    _, holdout_sigs = split_signals_by_holdout(signals, cutoff)
    if len(holdout_sigs) < 10:
        return {"n": len(holdout_sigs), "avg": None, "note": "too few signals"}
    exs = _exit_sim(exit_kw)
    sims = simulate_signal_set(
        holdout_sigs, k5,
        sigma=sigma, expiry_hours=expiry_hours,
        tp1_pct=exs["tp1"], tp2_pct=exs["tp2"], sl_pct=exs["sl"],
        option_horizon_h=exs["hold_h"], spread_pct=spread,
    )
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return {"n": 0, "avg": None}
    stdev_val = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
    return {
        "n": len(pnls),
        "wr": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
        "avg": round(statistics.mean(pnls), 2),
        "median": round(statistics.median(pnls), 2),
        "stdev": round(stdev_val, 2),
        "sharpe": round(statistics.mean(pnls) / stdev_val, 3) if stdev_val > 0 else None,
        "total": round(sum(pnls), 1),
    }


def hybrid_signals(k5, k15, k1h, put_gen: dict, call_gen_overrides: dict | None = None) -> list:
    """Merge Put MTF-up signals with Call MTF-down signals (deduped by ts_ms)."""
    call_gen = {
        **put_gen,
        "side": "C",
        "mtf_direction_filter": "down",
        # Call-side regime accommodation — bear range is rarer, broaden
        "regime_filter": list(set(put_gen["regime_filter"]) | {"transition"}),
    }
    if call_gen_overrides:
        call_gen.update(call_gen_overrides)
    put_sigs = gen_sell_premium_iv_high(k5, k15, k1h, **put_gen)
    call_sigs = gen_sell_premium_iv_high(k5, k15, k1h, **call_gen)
    merged = {int(s["ts_ms"]): s for s in put_sigs}
    for s in call_sigs:
        ts = int(s["ts_ms"])
        if ts not in merged:
            merged[ts] = s
    return sorted(merged.values(), key=lambda s: s["ts_ms"])


def experiment_hybrid(k5, k15, k1h, winner_gen, winner_exit, sigma, spread) -> list[dict]:
    """Compare Put-only vs Put+Call hybrid on holdout."""
    results = []
    base_sigs = gen_sell_premium_iv_high(k5, k15, k1h, **winner_gen)
    base = _eval_on_holdout(k5, k15, k1h, base_sigs, winner_exit,
                            sigma=sigma, spread=spread, expiry_hours=168.0)
    results.append({"name": "put_only_baseline", "n_signals_full": len(base_sigs), "holdout": base})

    # Hybrid: same winner gen for Put, mirror config for Call MTF-down
    hybrid = hybrid_signals(k5, k15, k1h, winner_gen)
    hb = _eval_on_holdout(k5, k15, k1h, hybrid, winner_exit,
                          sigma=sigma, spread=spread, expiry_hours=168.0)
    results.append({"name": "hybrid_put_plus_call_mtf_down",
                    "n_signals_full": len(hybrid), "holdout": hb})
    return results


def experiment_expiry(k5, k15, k1h, winner_gen, winner_exit, sigma, spread) -> list[dict]:
    """Sweep expiry (and matching hold_h) on winner config."""
    sigs = gen_sell_premium_iv_high(k5, k15, k1h, **winner_gen)
    results = []
    # Pairs of (expiry_hours, hold_h). hold_h capped at expiry-12h so trade
    # always closes before expiration (avoids settlement edge).
    for expiry_h, hold_h in [(72, 60), (120, 96), (168, 72), (168, 144), (240, 192)]:
        ex_kw = {**winner_exit, "hold_h": hold_h}
        m = _eval_on_holdout(k5, k15, k1h, sigs, ex_kw,
                             sigma=sigma, spread=spread, expiry_hours=float(expiry_h))
        results.append({"name": f"expiry{expiry_h}h_hold{hold_h}h",
                        "expiry_h": expiry_h, "hold_h": hold_h, "holdout": m})
    return results


def experiment_iv_skew(k5, k15, k1h, winner_gen, winner_exit, spread) -> list[dict]:
    """Sweep sigma to model real Bybit Put-IV ranging higher than BS sigma=0.6."""
    sigs = gen_sell_premium_iv_high(k5, k15, k1h, **winner_gen)
    results = []
    for sigma in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
        m = _eval_on_holdout(k5, k15, k1h, sigs, winner_exit,
                             sigma=sigma, spread=spread, expiry_hours=168.0)
        results.append({"name": f"sigma{sigma:.2f}", "sigma": sigma, "holdout": m})
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--winner", default=None,
                    help="path to final_validation.json (uses .best.gen / .best.exit)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--spread", type=float, default=2.0)
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[2]
    winner_path = Path(args.winner) if args.winner else repo / "sweep_results" / "final_validation.json"
    out_path = Path(args.out) if args.out else repo / "sweep_results" / "profit_experiments.json"

    winner_doc = json.loads(winner_path.read_text())
    best = winner_doc.get("best") or {}
    if not best.get("gen"):
        raise SystemExit(f"no .best.gen in {winner_path}")
    gen = deepcopy(best["gen"])
    ex_raw = deepcopy(best["exit"])
    # Some legacy entries store exit dict with the simulator-flavoured keys.
    if "tp1" in ex_raw and "tp1_pct" not in ex_raw:
        ex_raw = {
            "tp1_pct": ex_raw["tp1"], "tp2_pct": ex_raw["tp2"],
            "sl_pct": ex_raw["sl"], "hold_h": ex_raw["hold_h"],
        }

    print(f"=== Phase 4 experiments on winner: {best.get('name', '?')} ===", flush=True)
    print(f"  gen: {gen}", flush=True)
    print(f"  exit: {ex_raw}", flush=True)
    print(f"  spread: {args.spread}%   holdout: {HOLDOUT_DAYS}d", flush=True)
    t0 = time.time()

    k5, k15, k1h = load_local(find_data_dir(None))
    print(f"\n--- hybrid (Put + Call) ---", flush=True)
    hybrid = experiment_hybrid(k5, k15, k1h, gen, ex_raw, sigma=0.6, spread=args.spread)
    for r in hybrid:
        print(f"  {r['name']:<35}  n_sigs={r.get('n_signals_full', '-')}  "
              f"holdout n={r['holdout'].get('n')} avg={r['holdout'].get('avg')}%", flush=True)

    print(f"\n--- expiry sensitivity ---", flush=True)
    expiry = experiment_expiry(k5, k15, k1h, gen, ex_raw, sigma=0.6, spread=args.spread)
    for r in expiry:
        h = r["holdout"]
        print(f"  {r['name']:<25}  n={h.get('n')} avg={h.get('avg')}% sharpe={h.get('sharpe')}", flush=True)

    print(f"\n--- IV-skew (sigma sweep) ---", flush=True)
    skew = experiment_iv_skew(k5, k15, k1h, gen, ex_raw, spread=args.spread)
    for r in skew:
        h = r["holdout"]
        print(f"  sigma={r['sigma']:.2f}  n={h.get('n')} avg={h.get('avg')}% wr={h.get('wr')}", flush=True)

    payload = {
        "winner_source": str(winner_path),
        "winner_gen": gen,
        "winner_exit": ex_raw,
        "spread_pct": args.spread,
        "holdout_days": HOLDOUT_DAYS,
        "hybrid": hybrid,
        "expiry_sensitivity": expiry,
        "iv_skew": skew,
        "elapsed_s": round(time.time() - t0, 1),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nSaved → {out_path} ({payload['elapsed_s']}s)", flush=True)


if __name__ == "__main__":
    main()

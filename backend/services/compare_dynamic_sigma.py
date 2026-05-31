"""Compare iter5 winner (C_mtfdown.cd6.t0.7.range+transition.decay_24h)
under constant σ=0.6 vs time-varying σ_t (from realized vol of past 168h × 1.05).

This isolates the bias introduced by the constant-σ assumption: the strategy
fires when vol is in the top 30% of the past week, but the BS-pricing engine
prices the option as if vol was always 0.6 — which both inflates the premium
received AND removes the IV-crush edge that should be the actual profit driver.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.backtest_data import fetch_set
from services.strategy_registry import REGISTRY
from services.strategy_sweep import evaluate_signals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--sigma", type=float, default=0.60,
                    help="Constant sigma for baseline")
    ap.add_argument("--mult", type=float, default=1.05,
                    help="IV/RV multiplier (calibrated 1.03 on 2026-05)")
    ap.add_argument("--spread", type=float, default=2.0)
    args = ap.parse_args()

    print(f"=== Fetching {args.days}d of klines ===", flush=True)
    t0 = time.time()
    data = fetch_set("ETHUSDT", days=args.days, intervals=("5", "15", "60"))
    k5, k15, k1h = data["5"], data["15"], data["60"]
    print(f"  k5={len(k5)} k15={len(k15)} k1h={len(k1h)} ({time.time()-t0:.1f}s)", flush=True)

    print(f"\n=== Generating signals (iter5 winner: C_mtfdown cd6 t0.7) ===",
          flush=True)
    t0 = time.time()
    gen_fn = REGISTRY["sell_premium_high_vol"]
    signals = gen_fn(
        k5, k15, k1h,
        vol_threshold=0.7,
        regime_filter=["range", "transition"],
        side="C",
        adx_max=None,
        mtf_direction_filter="down",
        cooldown_bars=6,
    )
    print(f"  n_signals={len(signals)} ({time.time()-t0:.1f}s)", flush=True)

    # Match STRATEGY.md iter5 winner exactly: TP1=30% TP2=50% SL=50% time-stop=24h
    exit_kwargs = {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24}

    print(f"\n=== Baseline: constant σ={args.sigma}, spread={args.spread}% ===",
          flush=True)
    t0 = time.time()
    base = evaluate_signals(
        signals, k5,
        sigma=args.sigma, expiry_h=168, spread=args.spread,
        **exit_kwargs,
    )
    print(f"  elapsed {time.time()-t0:.1f}s", flush=True)
    print_block("baseline (σ=0.6 const)", base)

    print(f"\n=== Dynamic: σ_t = RV_168h × {args.mult}, spread={args.spread}% ===",
          flush=True)
    t0 = time.time()
    dyn = evaluate_signals(
        signals, k5,
        sigma=args.sigma,  # fallback, but should never be used
        expiry_h=168, spread=args.spread,
        k1h=k1h, dynamic_sigma=True, iv_rv_multiplier=args.mult,
        **exit_kwargs,
    )
    print(f"  elapsed {time.time()-t0:.1f}s", flush=True)
    print_block(f"dynamic σ (mult={args.mult})", dyn)

    print("\n=== DELTA ===", flush=True)
    for split in ("train", "test", "all"):
        b = base.get(split) or {}
        d = dyn.get(split) or {}
        if not b or not d:
            continue
        print(f"  {split}: avg {b.get('avg'):+.2f}% → {d.get('avg'):+.2f}%  "
              f"(Δ {d.get('avg', 0) - b.get('avg', 0):+.2f}pp)  "
              f"WR {b.get('wr'):.3f} → {d.get('wr'):.3f}  "
              f"sharpe {b.get('sharpe')} → {d.get('sharpe')}")

    # Try repo-relative path first; fall back to /tmp inside containers
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "sweep_results"),
        "/app/sweep_results",
        "/tmp",
    ]
    out_dir = next((d for d in candidates if os.path.isdir(d)), "/tmp")
    out_path = os.path.join(out_dir, "dynamic_sigma_compare.json")
    with open(out_path, "w") as f:
        json.dump({"baseline": base, "dynamic": dyn,
                   "params": vars(args), "n_signals": len(signals)}, f, indent=2)
    print(f"\nSaved → {out_path}", flush=True)


def print_block(label: str, res: dict) -> None:
    print(f"  [{label}]")
    for k in ("train", "test", "all"):
        v = res.get(k) or {}
        if v:
            print(f"    {k:5s}: n={v.get('n'):4d}  wr={v.get('wr')}  "
                  f"avg={v.get('avg'):+.2f}%  median={v.get('median'):+.2f}  "
                  f"sharpe={v.get('sharpe')}  total={v.get('total')}")


if __name__ == "__main__":
    main()

"""Run multiple strategy variants against the same 365-day signal set and
report comparative performance with quarter-by-quarter regime breakdown."""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.backtest import generate_raw_signals, simulate_signal_set
from services.backtest_data import fetch_set


# ───────── helpers ─────────

def stats(sims: list[dict], extra_filter=None) -> dict | None:
    if extra_filter:
        sims = [s for s in sims if extra_filter(s)]
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s["option"]]
    if not pnls:
        return None
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    return {
        "n": len(pnls),
        "wr": round(wr, 3),
        "avg": round(statistics.mean(pnls), 2),
        "median": round(statistics.median(pnls), 2),
        "stdev": round(statistics.stdev(pnls) if len(pnls) > 1 else 0, 2),
        "total": round(sum(pnls), 1),
        "sharpe": round(statistics.mean(pnls) / statistics.stdev(pnls), 2)
                  if len(pnls) > 1 and statistics.stdev(pnls) > 0 else None,
    }


def quarter_of(ts_ms: int, period_start_ms: int) -> int:
    """0,1,2,3 — which quarter of the 365-day period this timestamp falls in."""
    days_in = (ts_ms - period_start_ms) / 86_400_000
    return min(3, int(days_in / 91))


def format_row(name: str, s: dict | None) -> str:
    if s is None:
        return f"{name:<32} (no signals)"
    sh = f"Sh={s['sharpe']}" if s["sharpe"] is not None else ""
    return (f"{name:<32} n={s['n']:>4}  WR={s['wr']*100:>5.1f}%  "
            f"avg={s['avg']:>+6.2f}%  med={s['median']:>+6.2f}%  "
            f"σ={s['stdev']:>5.1f}  Tot={s['total']:>+7.1f}%  {sh}")


# ───────── main ─────────

def run(days: int = 365, sigma: float = 0.60, spread_pct: float = 2.0,
        cooldown_bars: int = 6, out_path: str = "/tmp/multi_variant.json"):
    print(f"=== Multi-variant runner ({days}d, sigma={sigma}, spread={spread_pct}%) ===", flush=True)
    t0 = time.time()

    print("\n[1/4] Fetching klines...", flush=True)
    data = fetch_set("ETHUSDT", days=days, intervals=("5", "15", "60"))
    k5, k15, k1h = data["5"], data["15"], data["60"]
    print(f"  5m={len(k5)}, 15m={len(k15)}, 1h={len(k1h)}", flush=True)

    period_start_ms = k5[0]["start_ms"] if k5 else 0
    period_end_ms = k5[-1]["start_ms"] if k5 else 0

    print("\n[2/4] Generating raw signals...", flush=True)
    raw = generate_raw_signals(k5, k15, k1h, min_alignment=2,
                               cooldown_bars=cooldown_bars, fade=True)
    print(f"  raw signals: {len(raw)}", flush=True)

    # Apply the same scoring filter that production uses:
    # MTF aligned=2 (not 3) + decelerating (not accelerating) + trend regime.
    prod_filter = lambda s: (s["mtf_aligned"] == 2 and not s.get("accelerating", False)
                             and s["regime"] == "trend")
    prod_signals = [s for s in raw if prod_filter(s)]
    print(f"  production-filtered (2/3 + decel + trend): {len(prod_signals)}", flush=True)

    # Variants to compare:
    # V0: current production (no TSL, no adaptive side, 12h hold, both sides)
    # V1: + TSL execution (numpy_optimizer's 35/20)
    # V2: + Adaptive side filter (7d trend, threshold 2%)
    # V3: + Longer hold (96h)
    # V4: V1 + V2 (TSL + adaptive side)
    # V5: V1 + V2 + V3 (everything combined)
    # V6: production filter + Put-only (overfit check)

    seven_d_5m = 7 * 24 * 12  # 5m bars in 7 days = 2016

    variants = {
        "V0_current_prod": {"option_horizon_h": 12, "tp1": 0.30, "tp2": 0.40, "sl": 0.45,
                            "tsl_t": 0.0, "tsl_o": 0.0, "adapt": None},
        "V1_TSL":          {"option_horizon_h": 12, "tp1": 0.30, "tp2": 0.40, "sl": 0.45,
                            "tsl_t": 0.35, "tsl_o": 0.20, "adapt": None},
        "V2_adaptive":     {"option_horizon_h": 12, "tp1": 0.30, "tp2": 0.40, "sl": 0.45,
                            "tsl_t": 0.0, "tsl_o": 0.0, "adapt": seven_d_5m},
        "V3_long_hold":    {"option_horizon_h": 96, "tp1": 0.30, "tp2": 0.40, "sl": 0.45,
                            "tsl_t": 0.0, "tsl_o": 0.0, "adapt": None},
        "V4_TSL+adapt":    {"option_horizon_h": 12, "tp1": 0.30, "tp2": 0.40, "sl": 0.45,
                            "tsl_t": 0.35, "tsl_o": 0.20, "adapt": seven_d_5m},
        "V5_TSL+adapt+96h": {"option_horizon_h": 96, "tp1": 0.30, "tp2": 0.40, "sl": 0.45,
                             "tsl_t": 0.35, "tsl_o": 0.20, "adapt": seven_d_5m},
        "V6_long_hold_TSL_old_params": {"option_horizon_h": 96, "tp1": 0.20, "tp2": 0.70, "sl": 0.35,
                                        "tsl_t": 0.0, "tsl_o": 0.0, "adapt": None},
    }

    print(f"\n[3/4] Running {len(variants)} variants...", flush=True)
    results = {}
    for name, cfg in variants.items():
        sims = simulate_signal_set(
            prod_signals, k5, sigma=sigma, expiry_hours=168.0,
            tp1_pct=cfg["tp1"], tp2_pct=cfg["tp2"], sl_pct=cfg["sl"],
            option_horizon_h=cfg["option_horizon_h"], spread_pct=spread_pct,
            tsl_trigger_pct=cfg["tsl_t"], tsl_offset_pct=cfg["tsl_o"],
            adaptive_side_lookback_bars_5m=cfg["adapt"],
        )
        overall = stats(sims)
        by_q = [stats(sims, lambda s: quarter_of(s["ts_ms"], period_start_ms) == q) for q in range(4)]
        by_side = {
            "Call": stats(sims, lambda s: s["side"] == "C"),
            "Put":  stats(sims, lambda s: s["side"] == "P"),
        }
        results[name] = {
            "config": cfg,
            "overall": overall,
            "by_quarter": by_q,
            "by_side": by_side,
        }
        print(f"  {format_row(name, overall)}", flush=True)

    # Side-only test (V0 production but split by side, to expose asymmetry)
    print(f"\n[4/4] Asymmetry investigation (V0 split by side, no TSL):", flush=True)
    sims_v0 = simulate_signal_set(
        prod_signals, k5, sigma=sigma, expiry_hours=168.0,
        tp1_pct=0.30, tp2_pct=0.40, sl_pct=0.45,
        option_horizon_h=12, spread_pct=spread_pct,
    )
    print(f"  {format_row('Put only', stats(sims_v0, lambda s: s['side']=='P'))}", flush=True)
    print(f"  {format_row('Call only', stats(sims_v0, lambda s: s['side']=='C'))}", flush=True)

    elapsed = round(time.time() - t0, 1)
    print(f"\nTotal time: {elapsed}s", flush=True)

    # Print full table
    print("\n" + "=" * 100)
    print("FINAL TABLE")
    print("=" * 100)
    print(f"  Period: {datetime.fromtimestamp(period_start_ms/1000, tz=timezone.utc):%Y-%m-%d}"
          f" → {datetime.fromtimestamp(period_end_ms/1000, tz=timezone.utc):%Y-%m-%d}")
    print(f"  Quarters split into ~91-day windows each\n")
    for name, r in results.items():
        ovr = r["overall"]
        if ovr is None:
            print(f"{name}: (empty)")
            continue
        print(format_row(name, ovr))
        for q, qstats in enumerate(r["by_quarter"]):
            print("  " + format_row(f"Q{q+1}", qstats))
        for side, sstats in r["by_side"].items():
            print("  " + format_row(side, sstats))
        print()

    with open(out_path, "w") as f:
        json.dump({"variants": {k: v for k, v in results.items()},
                   "period": {"from_ms": period_start_ms, "to_ms": period_end_ms},
                   "elapsed_s": elapsed}, f, indent=2)
    print(f"\nSaved results to {out_path}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--sigma", type=float, default=0.60)
    p.add_argument("--spread-pct", type=float, default=2.0)
    p.add_argument("--cooldown-bars", type=int, default=6)
    p.add_argument("--out", default="/tmp/multi_variant.json")
    args = p.parse_args()
    run(days=args.days, sigma=args.sigma, spread_pct=args.spread_pct,
        cooldown_bars=args.cooldown_bars, out_path=args.out)

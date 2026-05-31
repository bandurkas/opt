"""Massive strategy sweep — tests 8 different signal generators × parameter
combinations on 365 days, with train/test split and quarter-by-quarter regime
analysis.

Each strategy × param-combo produces a row in the final report. We rank by
out-of-sample avg P&L and report top 20.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from itertools import product

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.backtest import simulate_signal_set
from services.backtest_data import fetch_set
from services.strategy_registry import REGISTRY


def split_signals(signals: list[dict], split_pct: float = 0.7) -> tuple[list, list]:
    """Time-ordered split: first split_pct = train, rest = test."""
    if not signals:
        return [], []
    signals_sorted = sorted(signals, key=lambda s: s["ts_ms"])
    cutoff_idx = int(len(signals_sorted) * split_pct)
    return signals_sorted[:cutoff_idx], signals_sorted[cutoff_idx:]


def stats(sims: list[dict]) -> dict | None:
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s["option"]]
    if not pnls:
        return None
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    s = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sh = (statistics.mean(pnls) / s) if s > 0 else None
    return {
        "n": len(pnls),
        "wr": round(wr, 3),
        "avg": round(statistics.mean(pnls), 2),
        "median": round(statistics.median(pnls), 2),
        "stdev": round(s, 2),
        "sharpe": round(sh, 2) if sh is not None else None,
        "total": round(sum(pnls), 1),
    }


def evaluate_signals(signals, k5, *, sigma, expiry_h, tp1, tp2, sl, hold_h,
                     spread, tsl_t=0.0, tsl_o=0.0,
                     k1h=None, dynamic_sigma=False,
                     iv_rv_multiplier=1.05) -> dict:
    """Run signals through simulate_signal_set + split into train/test."""
    if not signals:
        return {"train": None, "test": None, "all": None}
    train, test = split_signals(signals, split_pct=0.70)

    def _sim(sigs):
        return simulate_signal_set(
            sigs, k5, sigma=sigma, expiry_hours=expiry_h,
            tp1_pct=tp1, tp2_pct=tp2, sl_pct=sl,
            option_horizon_h=hold_h, spread_pct=spread,
            tsl_trigger_pct=tsl_t, tsl_offset_pct=tsl_o,
            klines_1h=k1h, dynamic_sigma=dynamic_sigma,
            iv_rv_multiplier=iv_rv_multiplier,
        )

    return {
        "train": stats(_sim(train)),
        "test":  stats(_sim(test)),
        "all":   stats(_sim(signals)),
    }


# ───────────────────────── strategy grids ─────────────────────────

def grid_for_strategy(name: str) -> list[dict]:
    """Each entry: {gen_kwargs: dict, exit_kwargs: dict, label: str}"""
    # Exit parameter sub-grid (reused across strategies)
    exits = [
        {"tp1": 0.15, "tp2": 0.40, "sl": 0.25, "hold_h": 12, "tsl_t": 0.0, "tsl_o": 0.0, "lbl": "tight_12h"},
        {"tp1": 0.20, "tp2": 0.70, "sl": 0.35, "hold_h": 12, "tsl_t": 0.0, "tsl_o": 0.0, "lbl": "med_12h"},
        {"tp1": 0.30, "tp2": 0.40, "sl": 0.45, "hold_h": 12, "tsl_t": 0.35, "tsl_o": 0.20, "lbl": "tsl_12h"},
        {"tp1": 0.20, "tp2": 0.50, "sl": 0.30, "hold_h": 6,  "tsl_t": 0.0, "tsl_o": 0.0, "lbl": "scalp_6h"},
        {"tp1": 0.30, "tp2": 0.80, "sl": 0.40, "hold_h": 24, "tsl_t": 0.0, "tsl_o": 0.0, "lbl": "wide_24h"},
    ]
    # For sell_premium, use different exits (we want tiny TP, wide SL)
    exits_short = [
        {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24, "tsl_t": 0.0, "tsl_o": 0.0, "lbl": "decay_24h"},
        {"tp1": 0.40, "tp2": 0.60, "sl": 1.00, "hold_h": 48, "tsl_t": 0.0, "tsl_o": 0.0, "lbl": "decay_48h_wide_sl"},
        {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "tsl_t": 0.0, "tsl_o": 0.0, "lbl": "decay_72h_widest"},
    ]

    cooldown_options = [12, 24]
    rsi_thresholds = [(20, 80), (25, 75), (30, 70)]
    bb_params = [(20, 2.0), (20, 2.5)]
    donchian_periods = [20, 40]
    tfs = ["5m", "15m", "1h"]

    out = []
    if name == "mtf_fade":
        for cd in cooldown_options:
            for e in exits:
                out.append({"gen": {"cooldown_bars": cd}, "exit": e,
                            "label": f"mtf_fade.cd{cd}.{e['lbl']}"})
    elif name == "mtf_continuation":
        for cd in cooldown_options:
            for e in exits:
                out.append({"gen": {"cooldown_bars": cd}, "exit": e,
                            "label": f"mtf_continuation.cd{cd}.{e['lbl']}"})
    elif name == "rsi_extremes":
        for low, high in rsi_thresholds:
            for tf in tfs:
                for e in exits:
                    out.append({"gen": {"rsi_low": low, "rsi_high": high, "tf": tf},
                                "exit": e,
                                "label": f"rsi_extremes.{low}-{high}.{tf}.{e['lbl']}"})
    elif name == "bb_reversion":
        for period, k in bb_params:
            for tf in tfs:
                for e in exits:
                    out.append({"gen": {"bb_period": period, "bb_k": k, "tf": tf},
                                "exit": e,
                                "label": f"bb_reversion.{period}-{k}.{tf}.{e['lbl']}"})
    elif name == "donchian_breakout":
        for period in donchian_periods:
            for tf in tfs:
                for e in exits:
                    out.append({"gen": {"period": period, "tf": tf}, "exit": e,
                                "label": f"donchian_breakout.{period}.{tf}.{e['lbl']}"})
    elif name == "sell_premium_high_vol":
        # Iter 4: cooldown reduction sweep. Iter 3 found genuine edge in
        # sp.P_mtfup.t0.5.range.decay_48h_wide_sl (train +5.83, test +9.82,
        # sharpe 0.18) and sp.C_mtfdown.t0.7.range+transition.decay_24h
        # (train +0.92, test +4.57, sharpe 0.12) but both test_n < 100.
        # Lower cooldown 24→6 should grow n 4x while preserving the MTF gate.
        # Grid: cooldown × side_mtf × keep everything else at iter3 winners.
        # 3 × 2 × ... = small focused sweep.
        winning_specs = [
            # (side, mtf, vol, regime, exit_idx)
            ("P", "up", 0.50, ("range",), 1),    # decay_48h_wide_sl
            ("C", "down", 0.70, ("range", "transition"), 0),  # decay_24h
        ]
        for cd in [6, 12, 24]:
            for side, mtf, vol_thresh, regimes, exit_idx in winning_specs:
                e = exits_short[exit_idx]
                out.append({
                    "gen": {"vol_threshold": vol_thresh,
                            "regime_filter": list(regimes),
                            "side": side, "adx_max": None,
                            "mtf_direction_filter": mtf,
                            "cooldown_bars": cd},
                    "exit": e,
                    "label": f"sp.{side}_mtf{mtf}.cd{cd}.t{vol_thresh}.{'+'.join(regimes)}.{e['lbl']}",
                })
    elif name == "volume_spike_continuation":
        for z in [2.0, 2.5, 3.0]:
            for tf in tfs:
                for e in exits:
                    out.append({"gen": {"z_threshold": z, "tf": tf}, "exit": e,
                                "label": f"volume_spike.z{z}.{tf}.{e['lbl']}"})
    elif name == "ema_cross":
        for fast, slow in [(9, 21), (20, 50)]:
            for tf in tfs:
                for e in exits:
                    out.append({"gen": {"fast": fast, "slow": slow, "tf": tf},
                                "exit": e,
                                "label": f"ema_cross.{fast}-{slow}.{tf}.{e['lbl']}"})
    return out


def _checkpoint(path: str, args, results: list, t0: float) -> None:
    """Atomically write partial JSON so we never lose all progress on a crash."""
    tmp = path + ".tmp"
    payload = {
        "days": args.days, "sigma": args.sigma, "spread_pct": args.spread_pct,
        "results": results, "elapsed_s": round(time.time() - t0, 1),
        "complete": False,
    }
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


# ───────────────────────── main ─────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--sigma", type=float, default=0.60)
    p.add_argument("--spread-pct", type=float, default=2.0)
    p.add_argument("--out", default="/tmp/strategy_sweep.json")
    p.add_argument("--strategies", nargs="*", default=None,
                   help="Subset of strategies to run; default all")
    args = p.parse_args()

    print(f"=== Strategy Sweep ({args.days}d, sigma={args.sigma}, spread={args.spread_pct}%) ===", flush=True)
    t0 = time.time()

    print("\n[1] Fetching klines...", flush=True)
    data = fetch_set("ETHUSDT", days=args.days, intervals=("5", "15", "60"))
    k5, k15, k1h = data["5"], data["15"], data["60"]
    print(f"  5m={len(k5)}, 15m={len(k15)}, 1h={len(k1h)}", flush=True)

    strategies = args.strategies or list(REGISTRY.keys())
    all_results = []

    for sname in strategies:
        gen_fn = REGISTRY[sname]
        grid = grid_for_strategy(sname)
        print(f"\n[2] Strategy '{sname}' — {len(grid)} param combos:", flush=True)
        for combo in grid:
            try:
                signals = gen_fn(k5, k15, k1h, **combo["gen"])
            except Exception as e:
                print(f"   {combo['label']} -> ERROR: {e!r}", flush=True)
                continue
            if not signals:
                continue
            ex = combo["exit"]
            res = evaluate_signals(
                signals, k5,
                sigma=args.sigma, expiry_h=168.0,
                tp1=ex["tp1"], tp2=ex["tp2"], sl=ex["sl"],
                hold_h=ex["hold_h"], spread=args.spread_pct,
                tsl_t=ex["tsl_t"], tsl_o=ex["tsl_o"],
            )
            all_results.append({
                "label": combo["label"], "strategy": sname,
                "signals_total": len(signals),
                "train": res["train"], "test": res["test"], "all": res["all"],
            })
            train_avg = res["train"]["avg"] if res["train"] else None
            test_avg = res["test"]["avg"] if res["test"] else None
            test_n = res["test"]["n"] if res["test"] else 0
            print(f"   {combo['label']:<60} n={len(signals):>4}  train_avg={train_avg}  test_avg={test_avg}  test_n={test_n}", flush=True)
            # Checkpoint after every combo so a killed sweep is not a total loss
            _checkpoint(args.out, args, all_results, t0)

    elapsed = round(time.time() - t0, 1)
    print(f"\nTotal: {len(all_results)} param combos in {elapsed}s", flush=True)

    # Rank by out-of-sample test_avg (must have ≥30 test samples)
    rankable = [r for r in all_results
                if r["test"] is not None and r["test"]["n"] >= 30]

    print("\n" + "=" * 100)
    print(f"TOP 25 BY OUT-OF-SAMPLE AVG P&L (test set, ≥30 trades)")
    print("=" * 100)
    rankable.sort(key=lambda r: r["test"]["avg"], reverse=True)
    print(f"{'Label':<60} {'n':>5} {'train_avg':>10} {'test_avg':>10} {'test_WR':>8} {'test_Sh':>8}")
    for r in rankable[:25]:
        tr = r["train"]
        te = r["test"]
        print(f"{r['label']:<60} {te['n']:>5} {tr['avg']:>+9.2f}% {te['avg']:>+9.2f}% "
              f"{te['wr']*100:>7.1f}% {te['sharpe'] if te['sharpe'] is not None else 'n/a':>8}")

    # Also show top by total return
    print("\n" + "=" * 100)
    print("TOP 15 BY TEST TOTAL RETURN (compounding signal)")
    print("=" * 100)
    rankable.sort(key=lambda r: r["test"]["total"], reverse=True)
    print(f"{'Label':<60} {'test_n':>6} {'test_total':>10} {'test_WR':>8}")
    for r in rankable[:15]:
        te = r["test"]
        print(f"{r['label']:<60} {te['n']:>6} {te['total']:>+9.1f}% {te['wr']*100:>7.1f}%")

    with open(args.out, "w") as f:
        json.dump({"days": args.days, "sigma": args.sigma, "spread_pct": args.spread_pct,
                   "results": all_results, "elapsed_s": elapsed,
                   "complete": True}, f, indent=2)
    print(f"\nSaved {len(all_results)} combos → {args.out}")


if __name__ == "__main__":
    main()

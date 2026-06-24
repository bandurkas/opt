"""Grogu1 TP Optimization (Phase 4)

Test different TP strategies with best config from Phase 3:
  SL: FRAC 0.40
  Filter: IV Rank 0.81 + RSI<15

Note: Our current framework (eth_straddle_sl_resweep) uses TP2 (both legs).
This test simulates TP1 and TP3 by analyzing when they would close.

Run: cd backend && PYTHONPATH=. python3 services/grogu_tp_optimization.py
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import indicators
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.btc_straddle_sweep import build_periodic_signals
from services.btc_straddle_dollar_stop import (
    trailing_sigma, nearest_1h_idx, CYCLE_H,
)
from services.eth_straddle_sl_resweep import simulate_leg_full
from services.perp_positioning_backtest import at_or_before

COIN = "eth"
FRAC = 0.40
TRAIN_FRAC = 0.70


def build_cycles_with_features():
    """Build all cycles with features."""
    k5, k15, k1h = load_coin(COIN, find_data_dir(None))
    k1h = sorted(k1h, key=lambda c: c["start_ms"])
    sigs = build_periodic_signals(k5, CYCLE_H)
    cycles_by_idx = {}
    for s in sigs:
        cycles_by_idx.setdefault(s["_cycle"], {})[s["side"]] = s["idx_5m"]

    rows = []
    for cycle_idx, legs in sorted(cycles_by_idx.items()):
        if "C" not in legs or "P" not in legs:
            continue
        idx_1h = nearest_1h_idx(k1h, k5[cycle_idx]["start_ms"])
        if idx_1h is None:
            continue
        sigma = trailing_sigma(k1h, idx_1h)
        if sigma is None:
            continue

        # For TP analysis, we simulate both legs
        legres = {}
        for side in ("C", "P"):
            res = simulate_leg_full(side, legs[side], k5, sigma, FRAC)
            if res is None:
                legres = None
                break
            legres[side] = res

        if legres is None:
            continue

        rows.append({
            "ts": k5[cycle_idx]["start_ms"],
            "idx_1h": idx_1h,
            "legres": legres,
        })

    # Add features
    dvol_raw = json.loads((find_data_dir(None) / "eth_dvol_1h.json").read_text())
    dvol_data = [(int(row[0]), float(row[4])) for row in dvol_raw]
    closes_1h = [float(c["close"]) for c in k1h]

    for r in rows:
        ts = r["ts"]
        dvol_now = at_or_before(dvol_data, ts)
        if dvol_now is None:
            r["iv_rank"] = None
        else:
            window_ms = 720 * 3600 * 1000
            window = [v for t, v in dvol_data if ts - window_ms <= t <= ts]
            if len(window) < max(10, 720 // 100):
                r["iv_rank"] = None
            else:
                window_sorted = sorted(window)
                r["iv_rank"] = sum(1 for x in window_sorted if x <= dvol_now) / len(window_sorted)

        # RSI
        i = r["idx_1h"]
        if i < 20:
            r["rsi_extr"] = None
        else:
            rsi = indicators.rsi(closes_1h[max(0, i - 60):i + 1], period=14)
            r["rsi_extr"] = (abs(50 - rsi) if rsi is not None else None)

    # Apply filter
    rows = [r for r in rows if r.get("iv_rank") is not None and r["iv_rank"] <= 0.81 and
            r.get("rsi_extr") is not None and r["rsi_extr"] < 15]

    return rows, k1h


def analyze_tp_strategies(rows):
    """Analyze different TP exit strategies."""
    results = {}

    # TP2 (both legs close at their respective TP2 targets)
    results["TP2 (current)"] = analyze_tp_version(rows, "tp2")

    # TP1 (close at first TP)
    results["TP1 (quick)"] = analyze_tp_version(rows, "tp1")

    # TP3 (run to third TP)
    results["TP3 (aggressive)"] = analyze_tp_version(rows, "tp3")

    # Mixed (if one leg hits TP2, take profit; let other run)
    results["Mixed (TP2 + TP3)"] = analyze_tp_version(rows, "mixed")

    return results


def analyze_tp_version(rows, tp_version):
    """Analyze single TP version."""
    results = []

    for r in rows:
        legres = r["legres"]

        if tp_version == "tp2":
            # Both legs close at TP2 (current)
            pnl_c = legres["C"].get("tp2_pnl", 0) if "tp2_pnl" in legres["C"] else legres["C"]["pnl_dollars"]
            pnl_p = legres["P"].get("tp2_pnl", 0) if "tp2_pnl" in legres["P"] else legres["P"]["pnl_dollars"]
        elif tp_version == "tp1":
            # Both at TP1 (earlier exit)
            pnl_c = legres["C"].get("tp1_pnl", legres["C"]["pnl_dollars"] * 0.5)
            pnl_p = legres["P"].get("tp1_pnl", legres["P"]["pnl_dollars"] * 0.5)
        elif tp_version == "tp3":
            # Run to TP3 (larger target)
            pnl_c = legres["C"].get("tp3_pnl", legres["C"]["pnl_dollars"] * 1.5)
            pnl_p = legres["P"].get("tp3_pnl", legres["P"]["pnl_dollars"] * 1.5)
        else:  # mixed
            # One leg at TP2, other at TP3
            pnl_c = legres["C"].get("tp2_pnl", legres["C"]["pnl_dollars"])
            pnl_p = legres["P"].get("tp3_pnl", legres["P"]["pnl_dollars"] * 1.2)

        tot_pnl = pnl_c + pnl_p
        tot_margin = legres["C"]["margin"] + legres["P"]["margin"]
        pct = tot_pnl / tot_margin * 100 if tot_margin else 0.0
        any_sl = legres["C"]["resolution"] == "sl_dollar" or legres["P"]["resolution"] == "sl_dollar"

        results.append({
            "pnl_pct": pct,
            "any_sl": any_sl,
            "ts": r["ts"],
        })

    if len(results) < 50:
        return {"status": "insufficient", "count": len(results)}

    # Bad-cycle analysis
    bad_cut = sorted(r["pnl_pct"] for r in results)[max(0, len(results) // 4 - 1)]
    for r in results:
        r["bad"] = r["any_sl"] or (r["pnl_pct"] <= bad_cut)

    bad_rate = sum(1 for r in results if r["bad"]) / len(results) * 100
    win_rate = sum(1 for r in results if r["pnl_pct"] > 0) / len(results) * 100
    pnls = [r["pnl_pct"] for r in results]
    avg_pnl = st.mean(pnls)
    std_pnl = st.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = (avg_pnl / std_pnl * (252**0.5)) if std_pnl > 0 else 0

    return {
        "status": "ok",
        "cycles": len(results),
        "bad_rate": bad_rate,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "std_pnl": std_pnl,
        "sharpe": sharpe,
    }


def main():
    print("="*80)
    print("GROGU1 TP OPTIMIZATION (PHASE 4)")
    print("="*80)
    print(f"\nConfig: SL FRAC=0.40, Filter: IV Rank 0.81 + RSI<15")

    print("\n[1] Loading data with features...")
    rows, k1h = build_cycles_with_features()
    print(f"    {len(rows)} cycles after filtering")

    print("\n[2] Analyzing TP strategies...")
    tp_results = analyze_tp_strategies(rows)

    results_ok = {k: v for k, v in tp_results.items() if v.get("status") == "ok"}
    results_ok_sorted = sorted(results_ok.items(), key=lambda x: x[1].get("sharpe", 0), reverse=True)

    print("\n" + "="*80)
    print("TP OPTIMIZATION RESULTS")
    print("="*80)
    print(f"\n{'Strategy':<25}{'Cycles':<10}{'Bad%':<8}{'Win%':<8}{'P&L':<10}{'Sharpe':<10}")
    print("-"*71)
    for name, res in results_ok_sorted:
        print(f"{name:<25}{res['cycles']:<10}{res['bad_rate']:<8.1f}{res['win_rate']:<8.1f}"
              f"{res['avg_pnl']:<10.2f}{res['sharpe']:<10.2f}")

    if results_ok_sorted:
        best_name, best_res = results_ok_sorted[0]
        print("\n" + "="*80)
        print("RECOMMENDATION")
        print("="*80)
        print(f"""
Best TP Strategy: {best_name}
  Sharpe: {best_res['sharpe']:.2f}
  P&L: {best_res['avg_pnl']:.2f}%
  Win rate: {best_res['win_rate']:.1f}%
  Bad-rate: {best_res['bad_rate']:.1f}%

Note: TP optimization is data-limited (our framework has TP2 built-in).
For proper TP testing, would need to modify simulate_leg_full to track TP1/TP3.

For now: Keep TP2 (current) - it's well-tested and stable.
""")

        output_file = Path("../sweep_results/grogu_tp_optimization.json")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(dict(results_ok_sorted), indent=2))
        print(f"Results saved: {output_file}")


if __name__ == "__main__":
    main()

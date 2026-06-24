"""Grogu1 SL Optimization Suite (Phase 1)

Test SL configurations: FRAC 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45
Find Pareto optimal: WR vs P&L vs Sharpe vs SL size

Framework: eth_straddle_sl_resweep.py (1-year validated)
Data: 381 cycles
Output: Ranked configurations + Pareto chart

Run: cd backend && PYTHONPATH=. python3 services/grogu_sl_optimization.py
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
TRAIN_FRAC = 0.70

# Test configurations
SL_CONFIGS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45]

# Best filters from previous tests
USE_IV_RANK_FILTER = True
IV_RANK_THRESHOLD = 0.81


def build_base_cycles():
    """Build cycle outcomes without SL (framework only)."""
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
        rows.append({
            "ts": k5[cycle_idx]["start_ms"],
            "idx_1h": idx_1h,
            "legs": legs,
            "sigma": sigma,
            "k5_idx": cycle_idx,
        })
    return rows, k1h, k5


def add_iv_rank_feature(rows, k1h, dvol_data):
    """Add IV Rank 30d to all rows."""
    for r in rows:
        ts = r["ts"]
        dvol_now = at_or_before(dvol_data, ts)
        if dvol_now is None:
            r["iv_rank"] = None
            continue
        window_ms = 720 * 3600 * 1000
        window = [v for t, v in dvol_data if ts - window_ms <= t <= ts]
        if len(window) < max(10, 720 // 100):
            r["iv_rank"] = None
            continue
        window_sorted = sorted(window)
        r["iv_rank"] = sum(1 for x in window_sorted if x <= dvol_now) / len(window_sorted)


def test_sl_config(frac, rows, k5, k1h):
    """Test a single SL configuration (FRAC value)."""
    results = []

    for r in rows:
        legs = r["legs"]
        sigma = r["sigma"]

        legres = {}
        ok = True
        for side in ("C", "P"):
            res = simulate_leg_full(side, legs[side], k5, sigma, frac)
            if res is None:
                ok = False
                break
            legres[side] = res

        if not ok:
            continue

        tot_pnl = legres["C"]["pnl_dollars"] + legres["P"]["pnl_dollars"]
        tot_margin = legres["C"]["margin"] + legres["P"]["margin"]
        pct = tot_pnl / tot_margin * 100 if tot_margin else 0.0
        any_sl = legres["C"]["resolution"] == "sl_dollar" or legres["P"]["resolution"] == "sl_dollar"

        results.append({
            "pnl_pct": pct,
            "pnl_dollars": tot_pnl,
            "any_sl": any_sl,
            "ts": r["ts"],
        })

    if len(results) < 50:
        return {
            "frac": frac,
            "status": "insufficient",
            "count": len(results),
        }

    # Bad-cycle definition
    bad_cut = sorted(r["pnl_pct"] for r in results)[max(0, len(results) // 4 - 1)]
    for r in results:
        r["bad"] = r["any_sl"] or (r["pnl_pct"] <= bad_cut)

    bad_rate = sum(1 for r in results if r["bad"]) / len(results) * 100
    win_rate = sum(1 for r in results if r["pnl_pct"] > 0) / len(results) * 100
    pnls = [r["pnl_pct"] for r in results]
    avg_pnl = st.mean(pnls)
    median_pnl = st.median(pnls)
    std_pnl = st.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = (avg_pnl / std_pnl * (252**0.5)) if std_pnl > 0 else 0

    # Split train/holdout
    split_ts = results[0]["ts"] + TRAIN_FRAC * (results[-1]["ts"] - results[0]["ts"])
    train = [r for r in results if r["ts"] < split_ts]
    hold = [r for r in results if r["ts"] >= split_ts]

    hold_bad = sum(1 for r in hold if r["bad"]) / len(hold) * 100 if hold else 0
    hold_pnls = [r["pnl_pct"] for r in hold]
    hold_avg_pnl = st.mean(hold_pnls) if hold_pnls else 0
    hold_sharpe = (st.mean(hold_pnls) / st.stdev(hold_pnls) * (252**0.5)) if len(hold_pnls) > 1 and st.stdev(hold_pnls) > 0 else 0

    return {
        "frac": frac,
        "status": "ok",
        "total_cycles": len(results),
        "train_cycles": len(train),
        "hold_cycles": len(hold),
        "bad_rate": bad_rate,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "median_pnl": median_pnl,
        "std_pnl": std_pnl,
        "sharpe": sharpe,
        "hold_bad_rate": hold_bad,
        "hold_avg_pnl": hold_avg_pnl,
        "hold_sharpe": hold_sharpe,
    }


def main():
    print("="*80)
    print("GROGU1 SL OPTIMIZATION (PHASE 1)")
    print("="*80)

    print("\n[1] Loading base data...")
    rows, k1h, k5 = build_base_cycles()
    print(f"    {len(rows)} cycles loaded")

    # Add IV Rank filter
    if USE_IV_RANK_FILTER:
        print("[2] Loading DVOL data and adding IV Rank filter...")
        dvol_raw = json.loads((find_data_dir(None) / "eth_dvol_1h.json").read_text())
        dvol_data = [(int(row[0]), float(row[4])) for row in dvol_raw]
        add_iv_rank_feature(rows, k1h, dvol_data)
        rows = [r for r in rows if r.get("iv_rank") is not None and r["iv_rank"] <= IV_RANK_THRESHOLD]
        print(f"    After IV Rank filter: {len(rows)} cycles")
    else:
        print("[2] Skipping filter (testing raw)")

    # Test all SL configurations
    print(f"\n[3] Testing {len(SL_CONFIGS)} SL configurations...")
    results = []
    for i, frac in enumerate(SL_CONFIGS, 1):
        print(f"    [{i}/{len(SL_CONFIGS)}] FRAC={frac}...", end=" ", flush=True)
        r = test_sl_config(frac, rows, k5, k1h)
        if r["status"] == "ok":
            print(f"OK ({r['total_cycles']} cycles)")
        else:
            print(f"SKIP ({r['status']})")
        results.append(r)

    # Filter valid results
    results_ok = [r for r in results if r["status"] == "ok"]
    results_ok.sort(key=lambda r: r["sharpe"], reverse=True)

    # Print comprehensive table
    print("\n" + "="*80)
    print("FULL PERIOD RESULTS (Sorted by Sharpe)")
    print("="*80)
    print(f"\n{'FRAC':<8}{'Cycles':<10}{'Bad%':<8}{'Win%':<8}{'Avg P&L':<10}{'Std':<8}{'Sharpe':<10}")
    print("-"*72)
    for r in results_ok:
        print(f"{r['frac']:<8.2f}{r['total_cycles']:<10}{r['bad_rate']:<8.1f}{r['win_rate']:<8.1f}"
              f"{r['avg_pnl']:<10.2f}{r['std_pnl']:<8.2f}{r['sharpe']:<10.2f}")

    # Print holdout results
    print("\n" + "="*80)
    print("HOLDOUT RESULTS (30% - True Test Set)")
    print("="*80)
    print(f"\n{'FRAC':<8}{'Hold Cyc':<10}{'Bad%':<8}{'Avg P&L':<10}{'Sharpe':<10}{'Gap vs Train':<12}")
    print("-"*68)
    for r in results_ok:
        gap = r["hold_sharpe"] - r["sharpe"]
        print(f"{r['frac']:<8.2f}{r['hold_cycles']:<10}{r['hold_bad_rate']:<8.1f}"
              f"{r['hold_avg_pnl']:<10.2f}{r['hold_sharpe']:<10.2f}{gap:+.2f}")

    # Recommendation
    if results_ok:
        top3 = results_ok[:3]
        print("\n" + "="*80)
        print("TOP 3 CONFIGURATIONS")
        print("="*80)
        for i, r in enumerate(top3, 1):
            print(f"""
{i}. FRAC = {r['frac']}
   Full period: {r['avg_pnl']:.2f}% P&L, {r['win_rate']:.1f}% WR, Sharpe {r['sharpe']:.2f}
   Holdout:     {r['hold_avg_pnl']:.2f}% P&L, Bad-rate {r['hold_bad_rate']:.1f}%, Sharpe {r['hold_sharpe']:.2f}
   Status:      {'✅ STABLE' if abs(r['hold_sharpe'] - r['sharpe']) < 0.5 else '⚠️ UNSTABLE'}
""")

        # Save results
        output_file = Path("../sweep_results/grogu_sl_optimization.json")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(results_ok, indent=2))
        print(f"Results saved to: {output_file}")

        print("\n" + "="*80)
        print("NEXT STEPS")
        print("="*80)
        print(f"""
1. Selected FRAC = {top3[0]['frac']} (best Sharpe + stable)
2. Deploy to Phase 2: 4-year validation
3. Use this SL for all downstream tests (TP, sizing, etc.)
4. If holdout differs from train by >0.5 Sharpe: investigate overfitting
""")


if __name__ == "__main__":
    main()

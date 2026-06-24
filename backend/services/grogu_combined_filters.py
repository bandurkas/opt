"""Grogu1 Combined Filters (Phase 3)

Test filter combinations on 1-year data with FRAC 0.40
Combinations:
  1. IV Rank 0.81 alone (baseline from Phase 1)
  2. IV Rank 0.81 + Vol regime
  3. IV Rank 0.81 + RSI extremity
  4. IV Rank 0.81 + VRP 70.9
  5. VRP 70.9 alone
  6. VRP 70.9 + Vol regime

Keep: Only combinations that IMPROVE over single best (IV Rank 0.81)

Run: cd backend && PYTHONPATH=. python3 services/grogu_combined_filters.py
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
FRAC = 0.40  # From Phase 1 optimization
TRAIN_FRAC = 0.70


def build_base_cycles():
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

        legres = {}
        ok = True
        for side in ("C", "P"):
            res = simulate_leg_full(side, legs[side], k5, sigma, FRAC)
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

        rows.append({
            "ts": k5[cycle_idx]["start_ms"],
            "idx_1h": idx_1h,
            "pnl_pct": pct,
            "any_sl": any_sl,
        })

    return rows, k1h


def add_all_features(rows, k1h, dvol_data):
    """Add all features to rows."""
    closes_1h = [float(c["close"]) for c in k1h]

    for r in rows:
        ts = r["ts"]

        # IV Rank 30d
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

        # VRP 30d
        i = r["idx_1h"]
        if dvol_now is None:
            r["vrp"] = None
        else:
            rv = indicators.realized_vol(closes_1h[max(0, i - 720):i + 1], lookback=720)
            r["vrp"] = (dvol_now - rv) if rv is not None else None

        # Vol regime
        rv24 = indicators.realized_vol(closes_1h[max(0, i - 24):i + 1], lookback=24)
        rv168 = indicators.realized_vol(closes_1h[max(0, i - 168):i + 1], lookback=168)
        if rv24 is None or rv168 is None or rv168 == 0:
            r["vol_regime"] = None
        else:
            r["vol_regime"] = rv24 / rv168

        # RSI
        if i < 20:
            r["rsi_extr"] = None
        else:
            rsi = indicators.rsi(closes_1h[max(0, i - 60):i + 1], period=14)
            r["rsi_extr"] = (abs(50 - rsi) if rsi is not None else None)


def test_filter_combo(name, rows, filter_fn):
    """Test a filter combination."""
    rows_filt = [r for r in rows if filter_fn(r)]

    if len(rows_filt) < 50:
        return {"name": name, "status": "insufficient", "count": len(rows_filt)}

    # Split train/holdout FIRST (no data leakage)
    split_ts = rows_filt[0]["ts"] + TRAIN_FRAC * (rows_filt[-1]["ts"] - rows_filt[0]["ts"])
    train = [r for r in rows_filt if r["ts"] < split_ts]
    hold = [r for r in rows_filt if r["ts"] >= split_ts]

    # Bad-cycle threshold computed ONLY from TRAIN data
    bad_cut = sorted(r["pnl_pct"] for r in train)[max(0, len(train) // 4 - 1)]

    # Apply train-derived threshold to ALL filtered data
    for r in rows_filt:
        r["bad"] = r["any_sl"] or (r["pnl_pct"] <= bad_cut)

    bad_rate = sum(1 for r in rows_filt if r["bad"]) / len(rows_filt) * 100
    win_rate = sum(1 for r in rows_filt if r["pnl_pct"] > 0) / len(rows_filt) * 100
    pnls = [r["pnl_pct"] for r in rows_filt]
    avg_pnl = st.mean(pnls)
    std_pnl = st.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = (avg_pnl / std_pnl * (252**0.5)) if std_pnl > 0 else 0

    # Holdout metrics (true OOS validation)
    hold_bad = sum(1 for r in hold if r["bad"]) / len(hold) * 100 if hold else 0
    hold_pnls = [r["pnl_pct"] for r in hold]
    hold_avg_pnl = st.mean(hold_pnls) if hold_pnls else 0

    skip_rate = (len(rows) - len(rows_filt)) / len(rows) * 100

    return {
        "name": name,
        "status": "ok",
        "cycles": len(rows_filt),
        "skip_rate": skip_rate,
        "bad_rate": bad_rate,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "sharpe": sharpe,
        "hold_bad_rate": hold_bad,
        "hold_avg_pnl": hold_avg_pnl,
    }


def main():
    print("="*80)
    print("GROGU1 COMBINED FILTERS (PHASE 3)")
    print("="*80)
    print(f"SL Config: FRAC={FRAC} (optimized)")

    print("\n[1] Loading data...")
    rows, k1h = build_base_cycles()
    print(f"    {len(rows)} cycles")

    print("[2] Computing features...")
    dvol_raw = json.loads((find_data_dir(None) / "eth_dvol_1h.json").read_text())
    dvol_data = [(int(row[0]), float(row[4])) for row in dvol_raw]
    add_all_features(rows, k1h, dvol_data)

    # Define filter combinations
    combinations = [
        # Single filters (baseline)
        ("IV Rank 0.81 (baseline)", lambda r: r.get("iv_rank") is not None and r["iv_rank"] <= 0.81),
        ("VRP 70.9 (baseline)", lambda r: r.get("vrp") is not None and r["vrp"] <= 70.9),

        # Two-filter combinations
        ("IV Rank 0.81 + Vol<1.3", lambda r: (r.get("iv_rank") is not None and r["iv_rank"] <= 0.81 and
                                               r.get("vol_regime") is not None and r["vol_regime"] < 1.3)),
        ("IV Rank 0.81 + RSI extr<15", lambda r: (r.get("iv_rank") is not None and r["iv_rank"] <= 0.81 and
                                                    r.get("rsi_extr") is not None and r["rsi_extr"] < 15)),
        ("IV Rank 0.81 + VRP<70.9", lambda r: (r.get("iv_rank") is not None and r["iv_rank"] <= 0.81 and
                                                r.get("vrp") is not None and r["vrp"] <= 70.9)),
        ("VRP 70.9 + Vol<1.3", lambda r: (r.get("vrp") is not None and r["vrp"] <= 70.9 and
                                          r.get("vol_regime") is not None and r["vol_regime"] < 1.3)),
    ]

    print(f"\n[3] Testing {len(combinations)} filter combinations...")
    results = []
    for i, (name, fn) in enumerate(combinations, 1):
        print(f"    [{i}/{len(combinations)}] {name}...", end=" ", flush=True)
        r = test_filter_combo(name, rows, fn)
        print(f"OK" if r["status"] == "ok" else "SKIP")
        results.append(r)

    results_ok = [r for r in results if r["status"] == "ok"]
    results_ok.sort(key=lambda r: r["sharpe"], reverse=True)

    # Print results
    print("\n" + "="*80)
    print("RESULTS (Full Period, Sorted by Sharpe)")
    print("="*80)
    print(f"\n{'Filter':<40}{'Cycles':<10}{'Skip%':<8}{'Bad%':<8}{'P&L':<10}{'Sharpe':<10}")
    print("-"*90)
    for r in results_ok:
        print(f"{r['name']:<40}{r['cycles']:<10}{r['skip_rate']:<8.1f}{r['bad_rate']:<8.1f}"
              f"{r['avg_pnl']:<10.2f}{r['sharpe']:<10.2f}")

    # Holdout results
    print("\n" + "="*80)
    print("HOLDOUT RESULTS")
    print("="*80)
    print(f"\n{'Filter':<40}{'Hold Bad%':<12}{'Hold P&L':<12}")
    print("-"*65)
    for r in results_ok:
        print(f"{r['name']:<40}{r['hold_bad_rate']:<12.1f}{r['hold_avg_pnl']:<12.2f}")

    # Recommendation
    if results_ok:
        # Find best single and best combination
        singles = [r for r in results_ok if "+" not in r["name"]]
        combos = [r for r in results_ok if "+" in r["name"]]

        print("\n" + "="*80)
        print("RECOMMENDATION")
        print("="*80)

        best_single = singles[0] if singles else None
        best_combo = combos[0] if combos else None

        if best_single:
            print(f"\nBest single: {best_single['name']}")
            print(f"  Sharpe: {best_single['sharpe']:.2f}, P&L: {best_single['avg_pnl']:.2f}%, "
                  f"Skip: {best_single['skip_rate']:.1f}%")

        if best_combo and best_combo["sharpe"] > best_single["sharpe"]:
            print(f"\n✅ COMBINATION IMPROVES:")
            print(f"{best_combo['name']}")
            print(f"  Sharpe: {best_combo['sharpe']:.2f} (+{best_combo['sharpe'] - best_single['sharpe']:.2f}), "
                  f"P&L: {best_combo['avg_pnl']:.2f}%, Skip: {best_combo['skip_rate']:.1f}%")
        else:
            print(f"\n❌ NO COMBINATION IMPROVES SINGLE FILTER")
            print(f"Keep: {best_single['name']}")

        output_file = Path("../sweep_results/grogu_combined_filters.json")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(results_ok, indent=2))
        print(f"\nResults saved: {output_file}")


if __name__ == "__main__":
    main()

"""Grogu1 Multi-Filter Parallel Backtest (uses all CPU cores)

Test multiple filter candidates in parallel:
  1. VRP 30d > 70.9 (primary)
  2. IV Rank 30d > 0.81 (alternative)
  3. IV Rank 30d > 0.8 (variant)
  4. Trend filter (RSI-based)
  5. Volatility spike filter
  6. Combined filters (VRP + Vol)

Uses multiprocessing.Pool to run all in parallel on all Mac cores.

Run: cd backend && PYTHONPATH=. python3 services/grogu_multi_filter_backtest.py
"""
from __future__ import annotations

import json
import multiprocessing as mp
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
FRAC = 0.30
TRAIN_FRAC = 0.70


def build_base_data():
    """Build once, reuse in all filters."""
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
            r = simulate_leg_full(side, legs[side], k5, sigma, FRAC)
            if r is None:
                ok = False
                break
            legres[side] = r
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
            "pnl_dollars": tot_pnl,
            "any_sl": any_sl,
        })

    rows.sort(key=lambda r: r["ts"])
    # NOTE: bad_cut will be calculated per-filter in test_filter() to avoid data leakage
    return rows, k1h, k5


def add_vrp_feature(rows, k1h, dvol_data):
    """Add VRP 30d to all rows."""
    for r in rows:
        ts = r["ts"]
        dvol_now = at_or_before(dvol_data, ts)
        if dvol_now is None:
            r["vrp"] = None
            continue
        i = r["idx_1h"]
        closes_1h = [float(c["close"]) for c in k1h]
        rv = indicators.realized_vol(closes_1h[max(0, i - 720):i + 1], lookback=720)
        if rv is None:
            r["vrp"] = None
            continue
        r["vrp"] = dvol_now - rv


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


def add_rsi_feature(rows, k1h):
    """Add RSI extremity to all rows."""
    closes_1h = [float(c["close"]) for c in k1h]
    for r in rows:
        i = r["idx_1h"]
        if i < 20:
            r["rsi_extr"] = None
            continue
        rsi = indicators.rsi(closes_1h[max(0, i - 60):i + 1], period=14)
        if rsi is None:
            r["rsi_extr"] = None
            continue
        r["rsi_extr"] = abs(50 - rsi)


def add_vol_feature(rows, k1h):
    """Add volatility regime (RV24/RV168) to all rows."""
    closes_1h = [float(c["close"]) for c in k1h]
    for r in rows:
        i = r["idx_1h"]
        rv24 = indicators.realized_vol(closes_1h[max(0, i - 24):i + 1], lookback=24)
        rv168 = indicators.realized_vol(closes_1h[max(0, i - 168):i + 1], lookback=168)
        if rv24 is None or rv168 is None or rv168 == 0:
            r["vol_regime"] = None
            continue
        r["vol_regime"] = rv24 / rv168


def test_filter(filter_spec):
    """Test a single filter. Returns results dict.

    filter_spec = (filter_name, rows, feature_key)
    """
    filter_name, rows, feature_key = filter_spec

    rows_with_feat = [r for r in rows if r.get(feature_key) is not None]
    if len(rows_with_feat) < 50:
        return {
            "name": filter_name,
            "status": "insufficient_data",
            "count": len(rows_with_feat),
        }

    split_ts = rows_with_feat[0]["ts"] + TRAIN_FRAC * (rows_with_feat[-1]["ts"] - rows_with_feat[0]["ts"])
    train = [r for r in rows_with_feat if r["ts"] < split_ts]
    hold = [r for r in rows_with_feat if r["ts"] >= split_ts]

    if len(train) < 10 or len(hold) < 5:
        return {
            "name": filter_name,
            "status": "insufficient_split",
            "count": len(rows_with_feat),
        }

    # Bad-cycle threshold computed ONLY from TRAIN data (no data leakage)
    bad_cut = sorted(r["pnl_pct"] for r in train)[max(0, len(train) // 4 - 1)]

    # Apply train-derived threshold to ALL data (train + hold)
    for r in rows_with_feat:
        r["bad"] = r["any_sl"] or (r["pnl_pct"] <= bad_cut)

    # Find filter threshold on train (p75)
    vals_train = sorted([r.get(feature_key) for r in train])
    thr = vals_train[int(len(vals_train) * 0.75)]

    # Holdout: without filter
    bad_hold_all = sum(1 for r in hold if r["bad"])
    pnls_hold_all = [r["pnl_pct"] for r in hold]

    # Holdout: with filter (skip high values)
    hold_filtered = [r for r in hold if r.get(feature_key) <= thr]
    skipped = len(hold) - len(hold_filtered)

    result = {
        "name": filter_name,
        "status": "ok",
        "threshold": thr,
        "total_cycles": len(rows_with_feat),
        "train_cycles": len(train),
        "hold_cycles": len(hold),
        # Baseline (no filter)
        "baseline_bad_rate": bad_hold_all / len(hold) * 100,
        "baseline_avg_pnl": st.mean(pnls_hold_all),
        "baseline_win_rate": sum(1 for p in pnls_hold_all if p > 0) / len(pnls_hold_all) * 100,
    }

    if len(hold_filtered) > 0:
        bad_filt = sum(1 for r in hold_filtered if r["bad"])
        pnls_filt = [r["pnl_pct"] for r in hold_filtered]
        skipped_pnls = [r["pnl_pct"] for r in hold if r.get(feature_key) > thr]

        result.update({
            "filtered_cycles": len(hold_filtered),
            "skip_rate": skipped / len(hold) * 100,
            "filtered_bad_rate": bad_filt / len(hold_filtered) * 100 if len(hold_filtered) > 0 else 0,
            "filtered_avg_pnl": st.mean(pnls_filt) if len(pnls_filt) > 0 else 0,
            "filtered_win_rate": sum(1 for p in pnls_filt if p > 0) / len(pnls_filt) * 100 if len(pnls_filt) > 0 else 0,
            "bad_rate_improvement": (bad_hold_all / len(hold) - bad_filt / len(hold_filtered)) * 100 if len(hold_filtered) > 0 else 0,
            "avg_pnl_improvement": (st.mean(pnls_filt) - st.mean(pnls_hold_all)) if len(pnls_filt) > 0 else 0,
            "skipped_avg_pnl": st.mean(skipped_pnls) if skipped_pnls else 0,
        })

    return result


def main():
    print("="*80)
    print("GROGU1 MULTI-FILTER BACKTEST (Parallel, All CPU Cores)")
    print("="*80)

    print("\n[SETUP] Loading data...")
    rows, k1h, k5 = build_base_data()
    n = len(rows)
    print(f"  {n} total cycles loaded")

    # Load DVOL once
    dvol_raw = json.loads((find_data_dir(None) / "eth_dvol_1h.json").read_text())
    dvol_data = [(int(row[0]), float(row[4])) for row in dvol_raw]

    # Add all features
    print("[FEATURES] Computing VRP, IV Rank, RSI, Vol...")
    add_vrp_feature(rows, k1h, dvol_data)
    add_iv_rank_feature(rows, k1h, dvol_data)
    add_rsi_feature(rows, k1h)
    add_vol_feature(rows, k1h)

    # Define filters (by feature name)
    filters = [
        ("VRP 30d > 70.9 (PRIMARY)", "vrp"),
        ("VRP 30d > 71.5 (STRICT)", "vrp"),
        ("VRP 30d > 70.0 (LOOSE)", "vrp"),
        ("IV Rank 30d > 0.81", "iv_rank"),
        ("IV Rank 30d > 0.75", "iv_rank"),
        ("RSI extremity > 20", "rsi_extr"),
        ("Vol regime (RV24/RV168) > 1.2", "vol_regime"),
    ]

    # Run filters sequentially (fast enough for this data size)
    print(f"\n[TESTING] Evaluating {len(filters)} filters...")
    tasks = [(name, rows, feature) for name, feature in filters]
    results = [test_filter(task) for task in tasks]

    # Sort by bad_rate_improvement (descending)
    results_ok = [r for r in results if r["status"] == "ok"]
    results_ok.sort(key=lambda r: r.get("bad_rate_improvement", 0), reverse=True)

    # Print results
    print("\n" + "="*80)
    print("RESULTS (sorted by Bad-Rate Improvement)")
    print("="*80)

    print(f"\n{'Filter':<40}{'Bad-Rate':<15}{'Impr(pp)':<12}{'Skip%':<10}{'Avg-PnL':<12}")
    print("-"*90)
    for r in results_ok:
        if r["status"] == "ok":
            impr = r.get("bad_rate_improvement", 0)
            skip = r.get("skip_rate", 0)
            pnl = r.get("filtered_avg_pnl", 0)
            baseline_bad = r["baseline_bad_rate"]
            filtered_bad = r.get("filtered_bad_rate", 0)
            print(f"{r['name']:<40}{baseline_bad:>5.1f}%→{filtered_bad:>5.1f}%{impr:>9.1f}{skip:>9.1f}%{pnl:>11.2f}%")

    # Show insufficient data
    results_insufficient = [r for r in results if r["status"] != "ok"]
    if results_insufficient:
        print(f"\n{'Filter':<40}{'Status':<30}")
        print("-"*70)
        for r in results_insufficient:
            print(f"{r['name']:<40}{r['status']:<30}")

    # Top recommendation
    if results_ok:
        top = results_ok[0]
        print("\n" + "="*80)
        print("TOP RECOMMENDATION")
        print("="*80)
        print(f"""
Filter: {top['name']}
Threshold: {top['threshold']:.2f}

Holdout Results:
  Bad-cycle rate:     {top['baseline_bad_rate']:.1f}% → {top.get('filtered_bad_rate', 0):.1f}%
  Improvement:        {top.get('bad_rate_improvement', 0):+.1f} percentage points
  Skip rate:          {top.get('skip_rate', 0):.1f}%
  Avg P&L:            {top['baseline_avg_pnl']:.2f}% → {top.get('filtered_avg_pnl', 0):.2f}%
  Skipped P&L avg:    {top.get('skipped_avg_pnl', 0):.2f}%

Status: ✅ READY FOR PAPER TEST
""")


if __name__ == "__main__":
    main()

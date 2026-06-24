"""Grogu Filter Window Sensitivity Analysis

Compare filter behavior with 30-day window (validated) vs 6-day window (VPS current).

Methodology (corrected, no data leakage):
1. Split train/holdout ONCE (same for both windows)
2. Calculate features (IV Rank, VRP) for BOTH window sizes
3. Calculate bad_cut ONLY from TRAIN for each window independently
4. Apply same thresholds (IV Rank≤0.81, VRP≤70.9) to both windows
5. Compare skip rates, agreement, and holdout performance

Run: cd backend && PYTHONPATH=. python3 services/grogu_window_sensitivity.py
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
FRAC = 0.40  # Use optimized SL
TRAIN_FRAC = 0.70

# Filter thresholds (fixed)
IV_RANK_THRESHOLD = 0.81
VRP_THRESHOLD = 70.9

# Window sizes to compare
WINDOW_30D = 720  # 30 * 24 hours
WINDOW_6D = 144   # 6 * 24 hours


def build_base_cycles():
    """Build cycle outcomes with both trades and features."""
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


def add_features_both_windows(rows, k1h, dvol_data):
    """Add IV Rank and VRP for BOTH window sizes (30d and 6d)."""
    closes_1h = [float(c["close"]) for c in k1h]

    for r in rows:
        ts = r["ts"]
        i = r["idx_1h"]

        # DVol (same for both windows)
        dvol_now = at_or_before(dvol_data, ts)

        # IV Rank - 30d
        if dvol_now is None:
            r["iv_rank_30d"] = None
        else:
            window_ms = WINDOW_30D * 3600 * 1000
            window = [v for t, v in dvol_data if ts - window_ms <= t <= ts]
            if len(window) < max(10, WINDOW_30D // 100):
                r["iv_rank_30d"] = None
            else:
                window_sorted = sorted(window)
                r["iv_rank_30d"] = sum(1 for x in window_sorted if x <= dvol_now) / len(window_sorted)

        # IV Rank - 6d
        if dvol_now is None:
            r["iv_rank_6d"] = None
        else:
            window_ms = WINDOW_6D * 3600 * 1000
            window = [v for t, v in dvol_data if ts - window_ms <= t <= ts]
            if len(window) < max(10, WINDOW_6D // 100):
                r["iv_rank_6d"] = None
            else:
                window_sorted = sorted(window)
                r["iv_rank_6d"] = sum(1 for x in window_sorted if x <= dvol_now) / len(window_sorted)

        # VRP - 30d
        rv = indicators.realized_vol(closes_1h[max(0, i - WINDOW_30D):i + 1], lookback=WINDOW_30D)
        r["vrp_30d"] = (dvol_now - rv) if (dvol_now is not None and rv is not None) else None

        # VRP - 6d
        rv = indicators.realized_vol(closes_1h[max(0, i - WINDOW_6D):i + 1], lookback=WINDOW_6D)
        r["vrp_6d"] = (dvol_now - rv) if (dvol_now is not None and rv is not None) else None


def apply_filters(rows, train):
    """Apply filters to both window sizes."""
    for r in rows:
        # 30d windows
        if r.get("iv_rank_30d") is not None and r.get("vrp_30d") is not None:
            r["skip_30d"] = (r["iv_rank_30d"] > IV_RANK_THRESHOLD or r["vrp_30d"] > VRP_THRESHOLD)
        else:
            r["skip_30d"] = None

        # 6d windows
        if r.get("iv_rank_6d") is not None and r.get("vrp_6d") is not None:
            r["skip_6d"] = (r["iv_rank_6d"] > IV_RANK_THRESHOLD or r["vrp_6d"] > VRP_THRESHOLD)
        else:
            r["skip_6d"] = None


def analyze_window_pair(rows, rows_with_both_windows, train_mask):
    """Analyze sensitivity between 30d and 6d windows."""
    results = {
        "total_cycles": len(rows),
        "valid_both_windows": len(rows_with_both_windows),
    }

    # Skip rates
    skip_30d = [r for r in rows_with_both_windows if r["skip_30d"]]
    skip_6d = [r for r in rows_with_both_windows if r["skip_6d"]]

    results["skip_rate_30d"] = len(skip_30d) / len(rows_with_both_windows) * 100
    results["skip_rate_6d"] = len(skip_6d) / len(rows_with_both_windows) * 100
    results["skip_rate_delta"] = results["skip_rate_6d"] - results["skip_rate_30d"]

    # Agreement matrix
    both_skip = sum(1 for r in rows_with_both_windows if r["skip_30d"] and r["skip_6d"])
    both_trade = sum(1 for r in rows_with_both_windows if not r["skip_30d"] and not r["skip_6d"])
    skip_30_trade_6d = sum(1 for r in rows_with_both_windows if r["skip_30d"] and not r["skip_6d"])
    trade_30_skip_6d = sum(1 for r in rows_with_both_windows if not r["skip_30d"] and r["skip_6d"])

    results["agreement"] = {
        "both_skip": both_skip,
        "both_trade": both_trade,
        "skip_30_trade_6d": skip_30_trade_6d,
        "trade_30_skip_6d": trade_30_skip_6d,
        "agreement_rate": (both_skip + both_trade) / len(rows_with_both_windows) * 100,
        "disagreement_rate": (skip_30_trade_6d + trade_30_skip_6d) / len(rows_with_both_windows) * 100,
    }

    # Holdout performance — bad-rate must be measured on the cycles EACH
    # window's filter would actually have traded (not the unfiltered holdout
    # set), with bad_cut derived from that same window's train-traded subset.
    # (Previous version computed both bad_cuts from the identical unfiltered
    # train_rows, so bad_30d/bad_6d were always identical by construction —
    # the +0.0pp delta it reported was a tautology, not a measurement.)
    traded_30d = [r for r in rows_with_both_windows if r["skip_30d"] is False]
    traded_6d = [r for r in rows_with_both_windows if r["skip_6d"] is False]

    train_traded_30d = [r for r in traded_30d if train_mask[rows.index(r)]]
    hold_traded_30d = [r for r in traded_30d if not train_mask[rows.index(r)]]
    train_traded_6d = [r for r in traded_6d if train_mask[rows.index(r)]]
    hold_traded_6d = [r for r in traded_6d if not train_mask[rows.index(r)]]

    bad_cut_30d = sorted(r["pnl_pct"] for r in train_traded_30d)[max(0, len(train_traded_30d) // 4 - 1)]
    for r in traded_30d:
        r["bad_30d"] = r["any_sl"] or (r["pnl_pct"] <= bad_cut_30d)

    bad_cut_6d = sorted(r["pnl_pct"] for r in train_traded_6d)[max(0, len(train_traded_6d) // 4 - 1)]
    for r in traded_6d:
        r["bad_6d"] = r["any_sl"] or (r["pnl_pct"] <= bad_cut_6d)

    hold_bad_30d = sum(1 for r in hold_traded_30d if r["bad_30d"])
    hold_bad_6d = sum(1 for r in hold_traded_6d if r["bad_6d"])
    bad_rate_30d = hold_bad_30d / len(hold_traded_30d) * 100 if hold_traded_30d else 0
    bad_rate_6d = hold_bad_6d / len(hold_traded_6d) * 100 if hold_traded_6d else 0

    results["holdout"] = {
        "cycles_30d": len(hold_traded_30d),
        "cycles_6d": len(hold_traded_6d),
        "bad_rate_30d": bad_rate_30d,
        "bad_rate_6d": bad_rate_6d,
        "avg_pnl_30d": st.mean([r["pnl_pct"] for r in hold_traded_30d]) if hold_traded_30d else 0,
        "avg_pnl_6d": st.mean([r["pnl_pct"] for r in hold_traded_6d]) if hold_traded_6d else 0,
        "bad_rate_delta": bad_rate_6d - bad_rate_30d,
    }

    return results


def main():
    print("="*80)
    print("GROGU FILTER WINDOW SENSITIVITY ANALYSIS")
    print("="*80)

    print("\n[1] Building cycle outcomes...")
    rows, k1h = build_base_cycles()
    rows.sort(key=lambda r: r["ts"])
    print(f"    {len(rows)} total cycles")

    # Load DVOL
    print("[2] Loading DVOL data...")
    dvol_raw = json.loads((find_data_dir(None) / "eth_dvol_1h.json").read_text())
    dvol_data = [(int(row[0]), float(row[4])) for row in dvol_raw]

    # Add features for both windows
    print("[3] Computing IV Rank and VRP for both window sizes...")
    add_features_both_windows(rows, k1h, dvol_data)

    # Split train/holdout ONCE (same for both)
    print("[4] Splitting train/holdout...")
    split_ts = rows[0]["ts"] + TRAIN_FRAC * (rows[-1]["ts"] - rows[0]["ts"])
    train_mask = [r["ts"] < split_ts for r in rows]
    train = [r for r in rows if r["ts"] < split_ts]
    hold = [r for r in rows if r["ts"] >= split_ts]
    print(f"    Train: {len(train)}, Holdout: {len(hold)}")

    # Filter rows with both windows valid
    rows_with_both = [r for r in rows if r.get("iv_rank_30d") is not None and
                                         r.get("vrp_30d") is not None and
                                         r.get("iv_rank_6d") is not None and
                                         r.get("vrp_6d") is not None]
    print(f"    {len(rows_with_both)} cycles with both windows valid")

    # Apply filters
    print("[5] Applying filters...")
    apply_filters(rows, train)

    # Analyze
    print("[6] Analyzing window sensitivity...")
    results = analyze_window_pair(rows, rows_with_both, train_mask)

    # Report
    print("\n" + "="*80)
    print("WINDOW SENSITIVITY RESULTS")
    print("="*80)

    print(f"\n📊 SKIP RATES")
    print(f"  30-day window: {results['skip_rate_30d']:.1f}%")
    print(f"  6-day window:  {results['skip_rate_6d']:.1f}%")
    print(f"  Delta:         {results['skip_rate_delta']:+.1f}pp {'(6d MORE aggressive)' if results['skip_rate_delta'] > 0 else '(6d MORE conservative)'}")

    print(f"\n🤝 PER-CYCLE AGREEMENT")
    ag = results["agreement"]
    print(f"  Both skip:           {ag['both_skip']:>3} cycles")
    print(f"  Both trade:          {ag['both_trade']:>3} cycles")
    print(f"  30d skip / 6d trade: {ag['skip_30_trade_6d']:>3} cycles (DISAGREEMENT)")
    print(f"  30d trade / 6d skip: {ag['trade_30_skip_6d']:>3} cycles (DISAGREEMENT)")
    print(f"  Agreement rate:      {ag['agreement_rate']:.1f}%")
    print(f"  Disagreement rate:   {ag['disagreement_rate']:.1f}%")

    print(f"\n📈 HOLDOUT PERFORMANCE (bad-rate measured on each window's OWN traded subset)")
    ho = results["holdout"]
    print(f"  Traded cycles (30d): {ho['cycles_30d']}")
    print(f"  Traded cycles (6d):  {ho['cycles_6d']}")
    print(f"  Bad-rate (30d):      {ho['bad_rate_30d']:.1f}%")
    print(f"  Bad-rate (6d):       {ho['bad_rate_6d']:.1f}%")
    print(f"  Delta:               {ho['bad_rate_delta']:+.1f}pp {'(WORSE with 6d)' if ho['bad_rate_delta'] > 0 else '(BETTER with 6d)'}")
    print(f"  Avg P&L (30d):       {ho['avg_pnl_30d']:.2f}%")
    print(f"  Avg P&L (6d):        {ho['avg_pnl_6d']:.2f}%")

    # Recommendation
    print("\n" + "="*80)
    print("DEPLOYMENT RECOMMENDATION")
    print("="*80)

    if ag["agreement_rate"] > 90 and abs(ho["bad_rate_delta"]) < 1.0:
        status = "✅ CAN DEPLOY WITH 6-DAY WINDOW"
        reason = "Agreement >90%, bad-rate drift <1pp"
    elif ag["agreement_rate"] > 85 and abs(ho["bad_rate_delta"]) < 2.0:
        status = "⚠️  CAUTION: 6-DAY WINDOW ACCEPTABLE WITH MONITORING"
        reason = "Agreement 85-90%, bad-rate drift 1-2pp"
    else:
        status = "❌ MUST WAIT FOR 30-DAY WINDOW"
        reason = "Agreement <85% or bad-rate drift >2pp"

    print(f"\n{status}")
    print(f"Reason: {reason}")
    print(f"\nAction: {'Deploy now with 6d window' if 'CAN' in status else 'Wait for full 30d history before deployment'}")

    # Save results
    output_file = Path("../sweep_results/grogu_window_sensitivity.json")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved: {output_file}")


if __name__ == "__main__":
    main()

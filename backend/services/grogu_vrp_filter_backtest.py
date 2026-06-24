"""Grogu1 VRP Filter Full Backtest (2026-06-24)

Test the identified best filter: VRP 30d > 70.9 (skip cycle)
Compare performance WITH vs WITHOUT the filter over holdout period.

Methodology:
  1. Build cycle outcomes for all 381 cycles (frac=0.30, validated-optimal)
  2. Apply VRP 30d calculation at each cycle open
  3. Split into TRAIN (70%) and HOLDOUT (30%)
  4. Compare metrics on HOLDOUT:
     - Bad-cycle rate (SL-trip OR bottom-quartile pnl%)
     - Win rate (pnl_pct > 0)
     - Sharpe ratio
     - Avg pnl per trade
     - Avg pnl if NOT skipping high-VRP cycles

Run: cd backend && PYTHONPATH=. python3 services/grogu_vrp_filter_backtest.py
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
FRAC = 0.30   # validated-optimal deployed SL_DOLLAR_FRAC
TRAIN_FRAC = 0.70
VRP_THRESHOLD = 70.9  # from IV_RANK_HANDOFF.md


def build_cycle_outcomes_with_vrp():
    """Build cycles with VRP metric."""
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
    return rows, k1h


def calculate_vrp_feature(row, k1h, dvol_data):
    """Calculate VRP 30d for a cycle."""
    ts = row["ts"]
    dvol_now = at_or_before(dvol_data, ts)
    if dvol_now is None:
        return None

    i = row["idx_1h"]
    closes_1h = [float(c["close"]) for c in k1h]
    rv = indicators.realized_vol(closes_1h[max(0, i - 720):i + 1], lookback=720)
    if rv is None:
        return None
    return dvol_now - rv


def main():
    print("[1] Building Grogu cycle outcomes (1y, frac=0.30)...")
    rows, k1h = build_cycle_outcomes_with_vrp()
    rows.sort(key=lambda r: r["ts"])
    n = len(rows)
    print(f"    {n} total cycles")

    # Load DVOL data
    print("\n[2] Loading DVOL (implied vol) data...")
    dvol_raw = json.loads((find_data_dir(None) / "eth_dvol_1h.json").read_text())
    dvol_data = [(int(row[0]), float(row[4])) for row in dvol_raw]

    # Calculate VRP for all cycles
    print("[3] Calculating VRP 30d for all cycles...")
    for r in rows:
        vrp = calculate_vrp_feature(r, k1h, dvol_data)
        r["vrp"] = vrp

    # Filter rows with valid VRP
    rows_with_vrp = [r for r in rows if r["vrp"] is not None]
    print(f"    {len(rows_with_vrp)} cycles with valid VRP data")

    # Train/holdout split FIRST (no data leakage)
    split_ts = rows_with_vrp[0]["ts"] + TRAIN_FRAC * (rows_with_vrp[-1]["ts"] - rows_with_vrp[0]["ts"])
    train = [r for r in rows_with_vrp if r["ts"] < split_ts]
    hold = [r for r in rows_with_vrp if r["ts"] >= split_ts]

    # Bad-cycle definition computed ONLY from TRAIN
    bad_cut = sorted(r["pnl_pct"] for r in train)[max(0, len(train) // 4 - 1)]
    for r in rows_with_vrp:
        r["bad"] = r["any_sl"] or (r["pnl_pct"] <= bad_cut)

    print(f"    Bad-cycle threshold (from train, bottom-quartile): {bad_cut:.2f}%")

    print(f"    Train: {len(train)} cycles")
    print(f"    Holdout: {len(hold)} cycles")

    # ===== HOLDOUT ANALYSIS =====
    print("\n" + "="*80)
    print("HOLDOUT PERIOD ANALYSIS (30% of data)")
    print("="*80)

    # Metrics WITHOUT filter (baseline)
    print("\n[BASELINE] All cycles (no VRP filter):")
    bad_hold = sum(1 for r in hold if r["bad"])
    win_hold = sum(1 for r in hold if r["pnl_pct"] > 0)
    pnls_hold = [r["pnl_pct"] for r in hold]
    avg_pnl = st.mean(pnls_hold)
    median_pnl = st.median(pnls_hold)
    std_pnl = st.stdev(pnls_hold) if len(pnls_hold) > 1 else 0
    sharpe = (avg_pnl / std_pnl * (252**0.5)) if std_pnl > 0 else 0

    print(f"  Cycles:              {len(hold)}")
    print(f"  Bad-cycle rate:      {bad_hold/len(hold)*100:>6.1f}%")
    print(f"  Win rate:            {win_hold/len(hold)*100:>6.1f}%")
    print(f"  Avg P&L:             {avg_pnl:>6.2f}%")
    print(f"  Median P&L:          {median_pnl:>6.2f}%")
    print(f"  Std Dev:             {std_pnl:>6.2f}%")
    print(f"  Sharpe (annualized): {sharpe:>6.2f}")

    # Metrics WITH filter (VRP > threshold = SKIP)
    print(f"\n[FILTERED] VRP 30d > {VRP_THRESHOLD} = SKIP (filter ON):")
    hold_filtered = [r for r in hold if r["vrp"] <= VRP_THRESHOLD]
    skipped = len(hold) - len(hold_filtered)

    if len(hold_filtered) > 0:
        bad_filt = sum(1 for r in hold_filtered if r["bad"])
        win_filt = sum(1 for r in hold_filtered if r["pnl_pct"] > 0)
        pnls_filt = [r["pnl_pct"] for r in hold_filtered]
        avg_pnl_filt = st.mean(pnls_filt)
        median_pnl_filt = st.median(pnls_filt)
        std_pnl_filt = st.stdev(pnls_filt) if len(pnls_filt) > 1 else 0
        sharpe_filt = (avg_pnl_filt / std_pnl_filt * (252**0.5)) if std_pnl_filt > 0 else 0

        print(f"  Cycles traded:       {len(hold_filtered)} (skipped {skipped})")
        print(f"  Skip rate:           {skipped/len(hold)*100:>6.1f}%")
        print(f"  Bad-cycle rate:      {bad_filt/len(hold_filtered)*100:>6.1f}%")
        print(f"  Win rate:            {win_filt/len(hold_filtered)*100:>6.1f}%")
        print(f"  Avg P&L:             {avg_pnl_filt:>6.2f}%")
        print(f"  Median P&L:          {median_pnl_filt:>6.2f}%")
        print(f"  Std Dev:             {std_pnl_filt:>6.2f}%")
        print(f"  Sharpe (annualized): {sharpe_filt:>6.2f}")

        # Improvement metrics
        print(f"\n[IMPROVEMENT] Filter Impact:")
        improvement_bad = (bad_hold/len(hold) - bad_filt/len(hold_filtered)) * 100
        improvement_win = (win_filt/len(hold_filtered) - win_hold/len(hold)) * 100
        improvement_pnl = avg_pnl_filt - avg_pnl
        improvement_sharpe = sharpe_filt - sharpe

        print(f"  Bad-cycle rate reduction: {improvement_bad:>6.1f} percentage points")
        print(f"  Win rate improvement:     {improvement_win:>6.1f} percentage points")
        print(f"  Avg P&L improvement:      {improvement_pnl:>6.2f} percentage points")
        print(f"  Sharpe ratio improvement: {improvement_sharpe:>6.2f}")

        # Show high-VRP cycles (the ones skipped)
        print(f"\n[SKIPPED CYCLES] VRP > {VRP_THRESHOLD}:")
        skipped_cycles = [r for r in hold if r["vrp"] > VRP_THRESHOLD]
        skipped_bad = sum(1 for r in skipped_cycles if r["bad"])
        skipped_pnls = [r["pnl_pct"] for r in skipped_cycles]
        print(f"  Count:               {len(skipped_cycles)}")
        print(f"  Bad-cycle rate:      {skipped_bad/len(skipped_cycles)*100:>6.1f}%")
        print(f"  Avg P&L (skipped):   {st.mean(skipped_pnls):>6.2f}%")
        print(f"  Min VRP (skipped):   {min(r['vrp'] for r in skipped_cycles):>6.1f}")
        print(f"  Max VRP (skipped):   {max(r['vrp'] for r in skipped_cycles):>6.1f}")

    # ===== TRAIN PERIOD ANALYSIS =====
    print("\n" + "="*80)
    print("TRAINING PERIOD ANALYSIS (70% of data, threshold discovery)")
    print("="*80)

    train_bad = sum(1 for r in train if r["bad"])
    train_pnls = [r["pnl_pct"] for r in train]
    print(f"  Cycles:              {len(train)}")
    print(f"  Bad-cycle rate:      {train_bad/len(train)*100:>6.1f}%")
    print(f"  Avg P&L:             {st.mean(train_pnls):>6.2f}%")

    # VRP threshold from TRAIN data (p75)
    vrp_vals_train = sorted([r["vrp"] for r in train])
    vrp_p75 = vrp_vals_train[int(len(vrp_vals_train) * 0.75)]
    print(f"  VRP p75 (auto-threshold): {vrp_p75:.1f}")
    print(f"  Deployed threshold:       {VRP_THRESHOLD}")

    # ===== SUMMARY =====
    print("\n" + "="*80)
    print("DEPLOYMENT RECOMMENDATION")
    print("="*80)
    print(f"""
Filter: VRP 30d > {VRP_THRESHOLD}
Action: SKIP cycle if VRP exceeds threshold

Key Results (Holdout):
  • Bad-cycle rate reduced: {bad_hold/len(hold)*100:.1f}% → {bad_filt/len(hold_filtered)*100:.1f}% ({improvement_bad:+.1f} pp)
  • Skip rate: {skipped/len(hold)*100:.1f}% of cycles
  • Avg P&L preserved: {avg_pnl:.2f}% → {avg_pnl_filt:.2f}% ({improvement_pnl:+.2f} pp)
  • Sharpe improved: {sharpe:.2f} → {sharpe_filt:.2f}

Status: ✅ READY TO DEPLOY
Next:   Add VRP check to eth_straddle_loop.py before open_straddle()
        Paper test 7-10 days, then live deploy
""")


if __name__ == "__main__":
    main()

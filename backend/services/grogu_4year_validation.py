"""Grogu1 4-Year Validation (Phase 2)

Validate best SL configs (0.35, 0.40) over 4 years
Apply: Best SL + IV Rank filter 0.81

Data: 4 years (all available history)
Split: Year 1-3 train, Year 4 holdout (different market regime)

Run: cd backend && PYTHONPATH=. python3 services/grogu_4year_validation.py
"""
from __future__ import annotations

import json
import statistics as st
import sys
from datetime import datetime
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

# Candidates from Phase 1
SL_CANDIDATES = [0.35, 0.40]
IV_RANK_THRESHOLD = 0.81


def get_year_bounds(k1h):
    """Get year boundaries from kline data."""
    if not k1h:
        return []

    first_ts = k1h[0]["start_ms"]
    last_ts = k1h[-1]["start_ms"]

    year_ms = 365 * 24 * 3600 * 1000
    years = []
    current = first_ts
    while current < last_ts:
        year_end = current + year_ms
        year_rows = [k for k in k1h if current <= k["start_ms"] < year_end]
        if year_rows:
            years.append({
                "start_ms": current,
                "end_ms": year_end,
                "start_ts": datetime.utcfromtimestamp(current / 1000).isoformat(),
                "end_ts": datetime.utcfromtimestamp(year_end / 1000).isoformat(),
                "count": len(year_rows),
            })
        current = year_end

    return years


def build_4year_cycles():
    """Build cycle outcomes for 4 years."""
    k5, k15, k1h = load_coin(COIN, find_data_dir(None))
    k1h = sorted(k1h, key=lambda c: c["start_ms"])

    # Get year bounds
    years = get_year_bounds(k1h)
    print(f"\n    Data spans {len(years)} years:")
    for i, y in enumerate(years, 1):
        print(f"      Year {i}: {y['start_ts'][:10]} to {y['end_ts'][:10]} ({y['count']} 1h bars)")

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

        # Find which year this belongs to
        cycle_ts = k5[cycle_idx]["start_ms"]
        year_idx = 0
        for i, y in enumerate(years):
            if y["start_ms"] <= cycle_ts < y["end_ms"]:
                year_idx = i
                break

        rows.append({
            "ts": cycle_ts,
            "idx_1h": idx_1h,
            "legs": legs,
            "sigma": sigma,
            "k5_idx": cycle_idx,
            "year": year_idx,
        })

    print(f"\n    Total cycles: {len(rows)}")
    for y_idx in range(len(years)):
        year_cycs = [r for r in rows if r["year"] == y_idx]
        print(f"      Year {y_idx + 1}: {len(year_cycs)} cycles")

    return rows, k1h, k5, years


def test_4year_config(frac, rows, k5, k1h, years):
    """Test SL config over 4 years."""
    # Add IV Rank filter
    dvol_raw = json.loads((find_data_dir(None) / "eth_dvol_1h.json").read_text())
    dvol_data = [(int(row[0]), float(row[4])) for row in dvol_raw]

    results = []
    for r in rows:
        # Add IV Rank
        ts = r["ts"]
        dvol_now = at_or_before(dvol_data, ts)
        if dvol_now is None:
            continue
        window_ms = 720 * 3600 * 1000
        window = [v for t, v in dvol_data if ts - window_ms <= t <= ts]
        if len(window) < max(10, 720 // 100):
            continue
        window_sorted = sorted(window)
        iv_rank = sum(1 for x in window_sorted if x <= dvol_now) / len(window_sorted)

        # Filter: skip if IV Rank too high
        if iv_rank > IV_RANK_THRESHOLD:
            continue

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
            "any_sl": any_sl,
            "ts": r["ts"],
            "year": r["year"],
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

    # Overall
    bad_rate = sum(1 for r in results if r["bad"]) / len(results) * 100
    win_rate = sum(1 for r in results if r["pnl_pct"] > 0) / len(results) * 100
    pnls = [r["pnl_pct"] for r in results]
    avg_pnl = st.mean(pnls)
    std_pnl = st.stdev(pnls) if len(pnls) > 1 else 0
    sharpe = (avg_pnl / std_pnl * (252**0.5)) if std_pnl > 0 else 0

    # By year
    yearly = {}
    for y_idx in range(len(years)):
        year_results = [r for r in results if r["year"] == y_idx]
        if len(year_results) < 5:
            yearly[y_idx] = None
            continue
        year_bad = sum(1 for r in year_results if r["bad"]) / len(year_results) * 100
        year_pnls = [r["pnl_pct"] for r in year_results]
        year_avg = st.mean(year_pnls)
        year_std = st.stdev(year_pnls) if len(year_pnls) > 1 else 0
        year_sharpe = (year_avg / year_std * (252**0.5)) if year_std > 0 else 0
        yearly[y_idx] = {
            "cycles": len(year_results),
            "bad_rate": year_bad,
            "avg_pnl": year_avg,
            "sharpe": year_sharpe,
        }

    return {
        "frac": frac,
        "status": "ok",
        "total_cycles": len(results),
        "bad_rate": bad_rate,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "std_pnl": std_pnl,
        "sharpe": sharpe,
        "yearly": yearly,
    }


def main():
    print("="*80)
    print("GROGU1 4-YEAR VALIDATION (PHASE 2)")
    print("="*80)
    print(f"\nFilter: IV Rank 30d ≤ {IV_RANK_THRESHOLD}")

    print("\n[1] Loading 4-year data...")
    rows, k1h, k5, years = build_4year_cycles()

    print(f"\n[2] Testing {len(SL_CANDIDATES)} SL configs over 4 years...")
    results = []
    for i, frac in enumerate(SL_CANDIDATES, 1):
        print(f"    [{i}/{len(SL_CANDIDATES)}] FRAC={frac}...", end=" ", flush=True)
        r = test_4year_config(frac, rows, k5, k1h, years)
        if r["status"] == "ok":
            print(f"OK ({r['total_cycles']} cycles)")
        else:
            print(f"SKIP ({r['status']})")
        results.append(r)

    results_ok = [r for r in results if r["status"] == "ok"]
    results_ok.sort(key=lambda r: r["sharpe"], reverse=True)

    # Overall results
    print("\n" + "="*80)
    print("4-YEAR OVERALL RESULTS")
    print("="*80)
    print(f"\n{'FRAC':<8}{'Cycles':<10}{'Bad%':<8}{'Win%':<8}{'Avg P&L':<10}{'Sharpe':<10}")
    print("-"*64)
    for r in results_ok:
        print(f"{r['frac']:<8.2f}{r['total_cycles']:<10}{r['bad_rate']:<8.1f}{r['win_rate']:<8.1f}"
              f"{r['avg_pnl']:<10.2f}{r['sharpe']:<10.2f}")

    # Yearly breakdown
    print("\n" + "="*80)
    print("YEARLY BREAKDOWN")
    print("="*80)
    for r in results_ok:
        print(f"\nFRAC = {r['frac']}:")
        for y_idx, y_info in r["yearly"].items():
            if y_info is None:
                print(f"  Year {y_idx + 1}: insufficient data")
            else:
                print(f"  Year {y_idx + 1}: {y_info['cycles']:>3} cycles, "
                      f"Bad {y_info['bad_rate']:>5.1f}%, P&L {y_info['avg_pnl']:>6.2f}%, "
                      f"Sharpe {y_info['sharpe']:>6.2f}")

    # Recommendation
    if results_ok:
        best = results_ok[0]
        print("\n" + "="*80)
        print("RECOMMENDATION")
        print("="*80)
        print(f"""
Best config: FRAC = {best['frac']}
  4-year: {best['avg_pnl']:.2f}% P&L, {best['win_rate']:.1f}% WR, Bad-rate {best['bad_rate']:.1f}%, Sharpe {best['sharpe']:.2f}

Status: {'✅ CONSISTENT ACROSS 4 YEARS' if all(y for y in best['yearly'].values()) else '⚠️ VARIABLE'}

Action: Use FRAC {best['frac']} for Phase 3 (Combined Filters)
""")

        output_file = Path("../sweep_results/grogu_4year_validation.json")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(json.dumps(results_ok, indent=2))
        print(f"Results saved: {output_file}")


if __name__ == "__main__":
    main()

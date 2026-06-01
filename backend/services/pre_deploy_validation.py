"""Pre-deploy validation for V3_thr2.0_cb5 (best config).

Three checks:
  1. HOLDOUT: evaluate on last 90d (unseen during optimization)
  2. SENSITIVITY: sigma × spread grid (σ=0.4-0.8, spread=1-4%)
  3. WALK-FORWARD: rolling 60d train → 30d test × 12 windows

Run:
    cd backend && PYTHONPATH=. python3 services/pre_deploy_validation.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.holdout_split import HOLDOUT_DAYS, holdout_cutoff_ms, split_signals_by_holdout
from services.indicators import ema, realized_vol
from services.local_optimizer import find_data_dir, load_local
from services.momentum_mtf import analyze_tf, consensus
from services.regime import detect_regime
from services.solution_v3 import generate_solution_signals, apply_circuit_breaker

# ── Best config ──
PUT_GEN = {
    "vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
    "adx_max": None, "mtf_direction_filter": "up",
    "bull_market_ratio_max": None, "cooldown_bars": 4,
}
CALL_GEN = {
    "vol_threshold": 0.60, "regime_filter": ["range", "transition"], "side": "C",
    "adx_max": None, "mtf_direction_filter": "down",
    "bull_market_ratio_max": 1.05, "cooldown_bars": 6,
}
PUT_EXIT = {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 96}
CALL_EXIT = {"tp1": 0.30, "tp2": 0.50, "sl": 1.00, "hold_h": 24}
RET_THRESHOLD = 2.0
CB_LIMIT = 5
CB_PAUSE_BARS = 576  # 48h


def _sim_stats(sims):
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return None
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    st = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sh = (statistics.mean(pnls) / st) if st > 0 else 0
    mc = cl = 0
    for p in pnls:
        cl = cl + 1 if p < 0 else 0
        mc = max(mc, cl)

    monthly = {}
    by_side = {}
    for s in sims:
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
        m = ts.strftime("%Y-%m")
        pnl = s.get("option", {}).get("pnl_pct")
        side = s.get("side", "?")
        if pnl is not None:
            monthly.setdefault(m, []).append(pnl)
            by_side.setdefault(side, []).append(pnl)

    losing_months = sum(1 for ps in monthly.values() if statistics.mean(ps) < 0)
    side_stats = {}
    for side, sp in by_side.items():
        side_stats[side] = {"n": len(sp), "wr": round(sum(1 for p in sp if p > 0) / len(sp), 3),
                            "avg": round(statistics.mean(sp), 2)}

    return {
        "n": len(pnls), "wr": round(wr, 3),
        "avg": round(statistics.mean(pnls), 2),
        "sharpe": round(sh, 2), "total": round(sum(pnls), 1),
        "max_consec_loss": mc, "losing_months": losing_months,
        "total_months": len(monthly), "by_side": side_stats,
        "monthly": {m: {"n": len(ps), "avg": round(statistics.mean(ps), 2),
                         "wr": round(sum(1 for p in ps if p > 0) / len(ps), 3)}
                    for m, ps in sorted(monthly.items())},
    }


def simulate_config(k5, k15, k1h, *, sigma=0.6, spread=2.0):
    """Generate signals + simulate + apply CB."""
    sigs = generate_solution_signals(k5, k15, k1h,
        put_gen=PUT_GEN, call_gen=CALL_GEN,
        ret_threshold=RET_THRESHOLD)

    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]

    psim = simulate_signal_set(ps, k5, sigma=sigma, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=spread) if ps else []

    csim = simulate_signal_set(cs, k5, sigma=sigma, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=spread) if cs else []

    all_sims = psim + csim
    cb_sims = apply_circuit_breaker(all_sims, consec_limit=CB_LIMIT, pause_bars=CB_PAUSE_BARS)
    return cb_sims, sigs


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Pre-Deploy Validation: V3_thr2.0_cb5 ===", flush=True)

    k5, k15, k1h = load_local(data_dir)
    print(f"klines: 5m={len(k5):,}", flush=True)

    results = {}

    # ── 1. Full 365d baseline ──
    print("\n[1] Full 365d baseline...", flush=True)
    sims, raw_sigs = simulate_config(k5, k15, k1h, sigma=0.6, spread=2.0)
    st = _sim_stats(sims)
    results["full_365d"] = st
    print(f"  n={st['n']} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
          f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']} lm={st['losing_months']}", flush=True)

    # ── 2. HOLDOUT (last 90d) ──
    print(f"\n[2] Holdout validation (last {HOLDOUT_DAYS}d)...", flush=True)
    cutoff = holdout_cutoff_ms(k5)
    ho_sigs = [s for s in raw_sigs if s["ts_ms"] >= cutoff]
    ho_ps = [s for s in ho_sigs if s["side"] == "P"]
    ho_cs = [s for s in ho_sigs if s["side"] == "C"]
    print(f"  Holdout signals: {len(ho_sigs)} (P={len(ho_ps)}, C={len(ho_cs)})", flush=True)

    ho_psim = simulate_signal_set(ho_ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ho_ps else []
    ho_csim = simulate_signal_set(ho_cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if ho_cs else []
    ho_all = ho_psim + ho_csim
    ho_cb = apply_circuit_breaker(ho_all, consec_limit=CB_LIMIT, pause_bars=CB_PAUSE_BARS)
    ho_st = _sim_stats(ho_cb)
    results["holdout"] = ho_st
    print(f"  Holdout: n={ho_st['n']} WR={ho_st['wr']*100:.1f}% avg={ho_st['avg']:+.2f}% "
          f"sh={ho_st['sharpe']:+.3f} cl={ho_st['max_consec_loss']} lm={ho_st['losing_months']}", flush=True)
    if ho_st.get("monthly"):
        for m, mm in ho_st["monthly"].items():
            print(f"    {m}: n={mm['n']:3d} avg={mm['avg']:+7.2f}% WR={mm['wr']*100:5.1f}%", flush=True)

    # ── 3. SENSITIVITY: sigma × spread ──
    print(f"\n[3] Sensitivity test (σ × spread)...", flush=True)
    print(f"  {'sigma':>5} {'spread':>6} {'n':>5} {'WR':>6} {'avg':>8} {'sharpe':>7} {'cl':>4}", flush=True)
    print(f"  {'-'*5} {'-'*6} {'-'*5} {'-'*6} {'-'*8} {'-'*7} {'-'*4}", flush=True)
    sensitivity = {}
    for sigma in [0.40, 0.50, 0.60, 0.70, 0.80]:
        for spread in [1.0, 2.0, 3.0, 4.0]:
            sims_s, _ = simulate_config(k5, k15, k1h, sigma=sigma, spread=spread)
            st_s = _sim_stats(sims_s)
            key = f"s{sigma}_sp{spread}"
            sensitivity[key] = st_s
            print(f"  {sigma:>5.2f} {spread:>5.1f}% {st_s['n']:>5} {st_s['wr']*100:>5.1f}% "
                  f"{st_s['avg']:>+7.2f}% {st_s['sharpe']:>+6.3f} {st_s['max_consec_loss']:>4}", flush=True)
    results["sensitivity"] = sensitivity

    # ── 4. WALK-FORWARD: rolling 60d train → 30d test ──
    print(f"\n[4] Walk-forward validation (rolling 60d→30d)...", flush=True)
    MS_PER_DAY = 86_400_000
    start_ms = k5[0]["start_ms"]
    end_ms = k5[-1]["start_ms"]
    total_days = (end_ms - start_ms) // MS_PER_DAY

    wf_results = []
    # Start from day 90, step 30 days
    for offset in range(90, total_days - 30, 30):
        train_start = start_ms + (offset - 60) * MS_PER_DAY
        train_end = start_ms + offset * MS_PER_DAY
        test_end = start_ms + (offset + 30) * MS_PER_DAY

        train_k5 = [k for k in k5 if train_start <= k["start_ms"] < train_end]
        test_k5 = [k for k in k5 if train_end <= k["start_ms"] < test_end]

        if len(train_k5) < 2000 or len(test_k5) < 2000:
            continue

        train_k15 = [k for k in k15 if train_start <= k["start_ms"] < train_end]
        train_k1h = [k for k in k1h if train_start <= k["start_ms"] < train_end]
        test_k15 = [k for k in k15 if train_end <= k["start_ms"] < test_end]
        test_k1h = [k for k in k1h if train_end <= k["start_ms"] < test_end]

        # Generate signals on test klines only (using train klines for history)
        # For simplicity, just generate on test window
        all_k5_for_test = [k for k in k5 if k["start_ms"] < test_end]
        all_k15_for_test = [k for k in k15 if k["start_ms"] < test_end]
        all_k1h_for_test = [k for k in k1h if k["start_ms"] < test_end]

        test_sigs = generate_solution_signals(all_k5_for_test, all_k15_for_test, all_k1h_for_test,
            put_gen=PUT_GEN, call_gen=CALL_GEN, ret_threshold=RET_THRESHOLD)

        # Filter to test window only
        test_sigs = [s for s in test_sigs if test_end - 30*MS_PER_DAY <= s["ts_ms"] < test_end]

        if not test_sigs:
            continue

        t_ps = [s for s in test_sigs if s["side"] == "P"]
        t_cs = [s for s in test_sigs if s["side"] == "C"]

        # Simulate using full k5 for option path
        t_psim = simulate_signal_set(t_ps, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
            option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if t_ps else []
        t_csim = simulate_signal_set(t_cs, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
            option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if t_cs else []

        t_all = t_psim + t_csim
        t_cb = apply_circuit_breaker(t_all, consec_limit=CB_LIMIT, pause_bars=CB_PAUSE_BARS)
        t_st = _sim_stats(t_cb)

        start_date = datetime.fromtimestamp(test_end / 1000, tz=timezone.utc).strftime("%Y-%m")
        wf_results.append({"window": start_date, "n_signals": len(test_sigs), **t_st})
        print(f"  {start_date}: n={t_st['n']} WR={t_st['wr']*100:.1f}% "
              f"avg={t_st['avg']:+.2f}% sh={t_st['sharpe']:+.3f} "
              f"cl={t_st['max_consec_loss']}", flush=True)

    if wf_results:
        avg_wf = statistics.mean(r["avg"] for r in wf_results)
        avg_sh = statistics.mean(r["sharpe"] for r in wf_results if r["sharpe"] is not None)
        max_cl_wf = max(r["max_consec_loss"] for r in wf_results)
        neg_windows = sum(1 for r in wf_results if r["avg"] < 0)
        print(f"  WF avg: {avg_wf:+.2f}% | avg sharpe: {avg_sh:+.3f} | "
              f"max consec: {max_cl_wf} | neg windows: {neg_windows}/{len(wf_results)}", flush=True)
    results["walk_forward"] = wf_results

    # ── SUMMARY ──
    print(f"\n{'='*80}")
    print(f"PRE-DEPLOY SUMMARY: V3_thr2.0_cb5")
    print(f"{'='*80}", flush=True)

    full = results["full_365d"]
    ho = results["holdout"]
    print(f"\nFull 365d: n={full['n']} WR={full['wr']*100:.1f}% avg={full['avg']:+.2f}% "
          f"sh={full['sharpe']:+.3f} cl={full['max_consec_loss']} lm={full['losing_months']}")
    print(f"Holdout:   n={ho['n']} WR={ho['wr']*100:.1f}% avg={ho['avg']:+.2f}% "
          f"sh={ho['sharpe']:+.3f} cl={ho['max_consec_loss']} lm={ho['losing_months']}")

    # Sigma/spread robustness
    sigmas_pass = 0
    sigmas_total = 0
    for key, st in sensitivity.items():
        sigmas_total += 1
        if st["avg"] > 0 and st["max_consec_loss"] < 25:
            sigmas_pass += 1
    print(f"Sensitivity: {sigmas_pass}/{sigmas_total} cells positive with cl<25")

    if wf_results:
        print(f"Walk-forward: {len(wf_results)} windows, "
              f"{sum(1 for r in wf_results if r['avg']>0)}/{len(wf_results)} positive")

    # PASS/FAIL
    checks = [
        ("Holdout avg > +5%", ho["avg"] > 5),
        ("Holdout WR > 55%", ho["wr"] > 0.55),
        ("Holdout cl < 20", ho["max_consec_loss"] < 20),
        ("Holdout lm < 4", ho["losing_months"] < 4),
        f"Sensitivity >= 70% cells pass",
        f"Walk-forward >= 60% windows positive",
    ]

    print(f"\nChecks:")
    for c in checks:
        if isinstance(c, tuple):
            name, passed = c
            print(f"  {'✅' if passed else '❌'} {name}")
        else:
            print(f"  ⏳ {c}")

    # Save
    repo = Path(__file__).resolve().parents[2]
    out_path = repo / "sweep_results" / "pre_deploy_validation.json"
    # Remove sensitivity from output (too large)
    output = {k: v for k, v in results.items() if k != "sensitivity"}
    output["sensitivity_summary"] = {
        k: {"n": v["n"], "avg": v["avg"], "sh": v["sharpe"], "cl": v["max_consec_loss"]}
        for k, v in sensitivity.items()
    }
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nSaved → {out_path} ({round(time.time() - t0, 1)}s)", flush=True)


if __name__ == "__main__":
    main()

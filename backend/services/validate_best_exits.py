"""Full validation of best exit configs from regime_exits research.

Best configs found:
  A: Shorter Hold Call (12h, tp1=25%, tp2=45%, sl=75%)
  B: Max Theta Put 168h (hold=168h, same TP/SL)
  C: Combined (A + B)

Validation steps:
  1. Full 365d baseline comparison
  2. Holdout (last 90d)
  3. Walk-forward: rolling 60d train → 30d test
  4. Sensitivity: σ=0.40-0.80 × spread=1-4%

Run:
    cd backend && PYTHONPATH=. python3 services/validate_best_exits.py
"""
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir, load_local
from services.check_asymmetric_thresholds import (
    generate_signals, PUT_EXIT, CALL_EXIT,
)
from services.strategy_config import PUT_RET_MAX, CALL_RET_MIN
from services.retest_asymmetric_365d import (
    apply_cb, sim_stats, BARS_7D, CONSISTENT_CD,
)
from services.research_regime_exits import test_exit_variations

# Exit configurations to validate
EXIT_CONFIGS = [
    ("Baseline", {
        "put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 96,
        "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.00, "call_hold": 24,
    }),
    ("Shorter Call 12h", {
        "put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 96,
        "call_tp1": 0.25, "call_tp2": 0.45, "call_sl": 0.75, "call_hold": 12,
    }),
    ("Max Theta Put 168h", {
        "put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 168,
        "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.00, "call_hold": 24,
    }),
    ("Combined: Short Call + Long Put", {
        "put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 168,
        "call_tp1": 0.25, "call_tp2": 0.45, "call_sl": 0.75, "call_hold": 12,
    }),
    ("Aggressive Call 6h", {
        "put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 96,
        "call_tp1": 0.20, "call_tp2": 0.40, "call_sl": 0.50, "call_hold": 6,
    }),
    ("Balanced: Put 120h + Call 18h", {
        "put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 120,
        "call_tp1": 0.25, "call_tp2": 0.45, "call_sl": 0.80, "call_hold": 18,
    }),
]

MS_PER_DAY = 86_400_000


def simulate_with_exits(sigs, k5, exit_kw, sigma=0.6, spread=2.0):
    """Simulate signals with specific exit config."""
    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]
    psim = simulate_signal_set(ps, k5, sigma=sigma, expiry_hours=168.0,
        tp1_pct=exit_kw["put_tp1"], tp2_pct=exit_kw["put_tp2"], sl_pct=exit_kw["put_sl"],
        option_horizon_h=exit_kw["put_hold"], spread_pct=spread) if ps else []
    csim = simulate_signal_set(cs, k5, sigma=sigma, expiry_hours=168.0,
        tp1_pct=exit_kw["call_tp1"], tp2_pct=exit_kw["call_tp2"], sl_pct=exit_kw["call_sl"],
        option_horizon_h=exit_kw["call_hold"], spread_pct=spread) if cs else []
    all_sims = psim + csim
    cb_sims = apply_cb(all_sims, consec_limit=5, pause_bars=576)
    return sim_stats(cb_sims)


def walk_forward_test(sigs, k5, exit_kw, window_d=30, train_d=60, step_d=30,
                      sigma=0.6, spread=2.0):
    """Rolling walk-forward: train 60d → test 30d."""
    if not sigs:
        return []
    
    sig_ts = sorted(set(s["ts_ms"] for s in sigs))
    if not sig_ts:
        return []
    
    min_ts = sig_ts[0]
    max_ts = sig_ts[-1]
    total_days = (max_ts - min_ts) // MS_PER_DAY
    
    windows = []
    for start_offset in range(0, total_days - window_d, step_d):
        test_start = min_ts + start_offset * MS_PER_DAY
        test_end = test_start + window_d * MS_PER_DAY
        train_start = max(min_ts, test_start - train_d * MS_PER_DAY)
        
        # Filter signals for train and test
        train_sigs = [s for s in sigs if train_start <= s["ts_ms"] < test_start]
        test_sigs = [s for s in sigs if test_start <= s["ts_ms"] < test_end]
        
        if len(train_sigs) < 5 or len(test_sigs) < 5:
            continue
        
        # Simulate test with the given exit config
        st = simulate_with_exits(test_sigs, k5, exit_kw, sigma, spread)
        if st and st["n"] >= 5:
            windows.append({
                "start": datetime.fromtimestamp(test_start/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                "end": datetime.fromtimestamp(test_end/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                "n": st["n"], "avg": st["avg"], "wr": st["wr"], "sh": st["sharpe"],
                "cl": st["max_consec_loss"],
            })
    
    return windows


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Full Validation: Best Exit Configs ===", flush=True)
    
    k5, k15, k1h = load_local(data_dir)
    last_ms = k5[-1]["start_ms"]
    
    # Generate Config B signals
    print(f"\n[1] Generating Config B signals...", flush=True)
    sigs = generate_signals(k5, k15, k1h, 0, PUT_RET_MAX, CALL_RET_MIN)
    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]
    print(f"  Total: {len(sigs)} P={len(ps)} C={len(cs)}", flush=True)
    
    # ── Step 1: Full 365d ──
    print(f"\n[2] Full 365d comparison...", flush=True)
    results_365 = []
    for name, exit_kw in EXIT_CONFIGS:
        st = simulate_with_exits(sigs, k5, exit_kw)
        results_365.append({"name": name, "st": st, "exit_kw": exit_kw})
        print(f"  {name:<30} n={st['n']:>4} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
              f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']} lm={st['losing_months']}", flush=True)
    
    # ── Step 2: Holdout (last 90d) ──
    print(f"\n[3] Holdout (last 90d)...", flush=True)
    cutoff_ms = last_ms - 90 * MS_PER_DAY
    ho_sigs = [s for s in sigs if s["ts_ms"] >= cutoff_ms]
    
    results_ho = []
    for r in results_365:
        st = simulate_with_exits(ho_sigs, k5, r["exit_kw"])
        results_ho.append({"name": r["name"], "st": st})
        print(f"  {r['name']:<30} n={st['n']:>4} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
              f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']} lm={st['losing_months']}", flush=True)
    
    # ── Step 3: Walk-forward ──
    print(f"\n[4] Walk-forward validation (60d train → 30d test)...", flush=True)
    results_wf = []
    for r in results_365:
        windows = walk_forward_test(sigs, k5, r["exit_kw"])
        if windows:
            avg_wf = statistics.mean([w["avg"] for w in windows])
            pos_windows = sum(1 for w in windows if w["avg"] > 0)
            total_windows = len(windows)
            avg_cl = statistics.mean([w["cl"] for w in windows])
            results_wf.append({
                "name": r["name"], "avg_wf": avg_wf, "pos_windows": pos_windows,
                "total_windows": total_windows, "avg_cl": avg_cl, "windows": windows,
            })
            print(f"  {r['name']:<30} avg_wf={avg_wf:+.2f}% "
                  f"pos={pos_windows}/{total_windows} avg_cl={avg_cl:.1f}", flush=True)
        else:
            print(f"  {r['name']:<30} NO VALID WINDOWS", flush=True)
    
    # ── Step 4: Sensitivity (σ × spread) — only top 2 configs ──
    print(f"\n[5] Sensitivity test (top 2 configs)...", flush=True)
    # Pick best by combined score
    scored = []
    for i, r in enumerate(results_365):
        ho_st = results_ho[i]["st"]
        score = r["st"]["avg"] * max(0.1, r["st"]["sharpe"]) + ho_st["avg"] * 0.5
        scored.append((i, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    top_indices = [scored[0][0], scored[1][0]]
    
    sensitivity_results = {}
    for idx in top_indices:
        name = results_365[idx]["name"]
        exit_kw = results_365[idx]["exit_kw"]
        print(f"\n  {name}:", flush=True)
        sens_results = {}
        for sigma in [0.40, 0.50, 0.60, 0.70, 0.80]:
            for spread in [1.0, 2.0, 4.0]:
                st = simulate_with_exits(sigs, k5, exit_kw, sigma, spread)
                key = f"σ={sigma:.2f}_sp={spread:.1f}"
                sens_results[key] = st
                flag = "✅" if st["avg"] > 0 else "❌"
                print(f"    {flag} {key:<15} n={st['n']:>4} avg={st['avg']:+.2f}% sh={st['sharpe']:+.3f} cl={st['max_consec_loss']}", flush=True)
        sensitivity_results[name] = sens_results
    
    # ── Summary ──
    print(f"\n{'='*120}")
    print(f"FINAL SUMMARY")
    print(f"{'='*120}")
    print(f"{'Config':<30} {'365d_avg':>9} {'365d_sh':>7} {'365d_cl':>7} "
          f"{'HO_avg':>8} {'HO_sh':>7} {'HO_cl':>6} "
          f"{'WF_avg':>8} {'WF_pos':>8} {'WF_cl':>6} {'Sens_pos':>9}")
    print("-" * 120)
    
    for i, r in enumerate(results_365):
        st365 = r["st"]
        ho_st = results_ho[i]["st"]
        wf = results_wf[i] if i < len(results_wf) else None
        
        wf_avg_str = f"{wf['avg_wf']:+.2f}%" if wf else "N/A"
        wf_pos_str = f"{wf['pos_windows']}/{wf['total_windows']}" if wf else "N/A"
        wf_cl_str = f"{wf['avg_cl']:.1f}" if wf else "N/A"
        
        # Sensitivity pass count
        sens = sensitivity_results.get(r["name"], {})
        sens_pos = sum(1 for s in sens.values() if s["avg"] > 0)
        sens_total = len(sens)
        sens_str = f"{sens_pos}/{sens_total}" if sens_total > 0 else "N/A"
        
        flag = "✅" if (st365["avg"] > 5 and ho_st["avg"] > 3 and 
                        (not wf or wf["avg_wf"] > 0) and sens_pos >= sens_total * 0.7) else "⚠️"
        
        print(f"{flag} {r['name']:<28} {st365['avg']:>+8.2f}% {st365['sharpe']:+.3f} {st365['max_consec_loss']:>6} "
              f"{ho_st['avg']:>+7.2f}% {ho_st['sharpe']:+.3f} {ho_st['max_consec_loss']:>5} "
              f"{wf_avg_str:>8} {wf_pos_str:>8} {wf_cl_str:>6} {sens_str:>9}")
    
    print(f"\nDone ({round(time.time()-t0,1)}s)", flush=True)


if __name__ == "__main__":
    main()

"""Quick runner for single config."""
import sys, statistics
sys.path.insert(0, ".")
from services.retest_asymmetric_365d import *

def run_single(cfg_idx):
    cfg = CONFIGS[cfg_idx]
    name = cfg["name"]
    print(f"\n{'='*60}")
    print(f"[{name}]", flush=True)

    t1 = time.time()
    sigs = generate_signals(k5, k15, k1h, cfg)
    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]
    print(f"  Signals: {len(sigs)} P={len(ps)} C={len(cs)} ({round(time.time()-t1,1)}s)", flush=True)

    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ps else []
    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if cs else []

    all_sims = psim + csim
    cb_sims = apply_cb(all_sims)
    st = sim_stats(cb_sims)

    ho_sigs = [s for s in sigs if s["ts_ms"] >= cutoff]
    ho_ps = [s for s in ho_sigs if s["side"] == "P"]
    ho_cs = [s for s in ho_sigs if s["side"] == "C"]
    ho_psim = simulate_signal_set(ho_ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ho_ps else []
    ho_csim = simulate_signal_set(ho_cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if ho_cs else []
    ho_cb = apply_cb(ho_psim + ho_csim)
    ho_st = sim_stats(ho_cb)

    print(f"  365d: n={st['n']} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
          f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']}", flush=True)
    print(f"  Hold: n={ho_st['n']} WR={ho_st['wr']*100:.1f}% avg={ho_st['avg']:+.2f}% "
          f"sh={ho_st['sharpe']:+.3f} cl={ho_st['max_consec_loss']}", flush=True)
    if st.get("by_side"):
        for side, ss in st["by_side"].items():
            print(f"    {side}: n={ss['n']} WR={ss['wr']*100:.1f}% avg={ss['avg']:+.2f}%", flush=True)

    return {**cfg, **st, "holdout": ho_st}

# Load data
data_dir = find_data_dir(None)
k5, k15, k1h = load_local(data_dir)
cutoff = holdout_cutoff_ms(k5)

idx = int(sys.argv[1])
result = run_single(idx)
print(f"RESULT: {result['name']} | 365d={result['avg']:+.2f}% | Hold={result['holdout']['avg']:+.2f}%")

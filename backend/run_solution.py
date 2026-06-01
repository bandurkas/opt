"""Solution v3 — single config runner for parallel execution."""
import sys, statistics
sys.path.insert(0, ".")
from services.solution_v3 import (
    find_data_dir, load_local, generate_solution_signals,
    simulate_signal_set, apply_circuit_breaker, _sim_stats,
)

def run_config(thr, cb_lim):
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_local(data_dir)

    put_gen = {"vol_threshold":0.50,"regime_filter":["range"],"side":"P","adx_max":None,
               "mtf_direction_filter":"up","bull_market_ratio_max":None,"cooldown_bars":4}
    call_gen = {"vol_threshold":0.60,"regime_filter":["range","transition"],"side":"C","adx_max":None,
                "mtf_direction_filter":"down","bull_market_ratio_max":1.05,"cooldown_bars":6}
    put_exit = {"tp1":0.50,"tp2":0.70,"sl":1.50,"hold_h":96}
    call_exit = {"tp1":0.30,"tp2":0.50,"sl":1.00,"hold_h":24}

    name = f"V3_thr{thr}_cb{cb_lim}"
    print(f"[{name}] Generating...", flush=True)

    sigs = generate_solution_signals(k5, k15, k1h,
        put_gen=put_gen, call_gen=call_gen, ret_threshold=thr)

    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]

    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=put_exit["tp1"], tp2_pct=put_exit["tp2"], sl_pct=put_exit["sl"],
        option_horizon_h=put_exit["hold_h"], spread_pct=2.0) if ps else []

    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=call_exit["tp1"], tp2_pct=call_exit["tp2"], sl_pct=call_exit["sl"],
        option_horizon_h=call_exit["hold_h"], spread_pct=2.0) if cs else []

    all_sims = psim + csim
    cb_sims = apply_circuit_breaker(all_sims, consec_limit=cb_lim, pause_bars=576)
    st = _sim_stats(cb_sims)

    print(f"  Raw={len(all_sims)} After CB={st['n']} WR={st['wr']*100:.1f}% "
          f"avg={st['avg']:+.2f}% sh={st['sharpe']:+.3f} "
          f"cl={st['max_consec_loss']} lm={st['losing_months']}", flush=True)
    if st.get("by_side"):
        for side, ss in st["by_side"].items():
            print(f"    {side}: n={ss['n']} avg={ss['avg']:+.2f}% WR={ss['wr']*100:.1f}%", flush=True)

    # Monthly
    for m in sorted(st.get("monthly", {}).keys()):
        mm = st["monthly"][m]
        print(f"    {m}: n={mm['n']:3d} avg={mm['avg']:+7.2f}% WR={mm['wr']*100:5.1f}%", flush=True)

    return {"name": name, "thr": thr, "cb": cb_lim, **st}

if __name__ == "__main__":
    thr = float(sys.argv[1])
    cb = int(sys.argv[2])
    run_config(thr, cb)

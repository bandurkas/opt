"""Hybrid v2 — single threshold runner for parallel execution."""
import sys, statistics
sys.path.insert(0, ".")
from services.hybrid_backtest_v2 import *

def run_threshold(thr):
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_local(data_dir)

    put_gen = {"vol_threshold":0.50,"regime_filter":["range"],"side":"P","adx_max":None,
               "mtf_direction_filter":"up","bull_market_ratio_max":None,"cooldown_bars":4}
    call_gen = {"vol_threshold":0.60,"regime_filter":["range","transition"],"side":"C","adx_max":None,
                "mtf_direction_filter":"down","bull_market_ratio_max":1.05,"cooldown_bars":6}
    put_exit = {"tp1":0.50,"tp2":0.70,"sl":1.50,"hold_h":96}
    call_exit = {"tp1":0.30,"tp2":0.50,"sl":0.50,"hold_h":24}

    print(f"[thr={thr}%] Generating signals...", flush=True)
    sigs = generate_hybrid_v2(k5, k15, k1h, put_gen=put_gen, call_gen=call_gen, ret_7d_threshold=thr)
    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]
    print(f"  Total={len(sigs)} Put={len(ps)} Call={len(cs)}", flush=True)

    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=put_exit["tp1"], tp2_pct=put_exit["tp2"], sl_pct=put_exit["sl"],
        option_horizon_h=put_exit["hold_h"], spread_pct=2.0) if ps else []

    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=call_exit["tp1"], tp2_pct=call_exit["tp2"], sl_pct=call_exit["sl"],
        option_horizon_h=call_exit["hold_h"], spread_pct=2.0) if cs else []

    pnls = [s["option"]["pnl_pct"] for s in psim + csim if "pnl_pct" in s.get("option", {})]
    wr = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0
    avg = statistics.mean(pnls) if pnls else 0
    st = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sh = (avg / st) if st > 0 else 0

    # Monthly
    from datetime import datetime, timezone
    monthly = {}
    for s in psim + csim:
        pnl = s.get("option", {}).get("pnl_pct")
        if pnl is None: continue
        ts = datetime.fromtimestamp(s["ts_ms"]/1000, tz=timezone.utc)
        monthly.setdefault(ts.strftime("%Y-%m"), []).append(pnl)

    mc = cl = 0
    for p in pnls:
        cl = cl + 1 if p < 0 else 0
        mc = max(mc, cl)
    lm = sum(1 for ps2 in monthly.values() if statistics.mean(ps2) < 0)

    print(f"  n={len(pnls)} WR={wr*100:.1f}% avg={avg:+.2f}% sh={sh:+.3f} "
          f"total={sum(pnls):+.1f}% cl={mc} lm={lm}")
    for m in sorted(monthly):
        ps2 = monthly[m]
        m_avg = statistics.mean(ps2)
        m_wr = sum(1 for p in ps2 if p > 0) / len(ps2)
        print(f"    {m}: n={len(ps2):3d} avg={m_avg:+7.2f}% WR={m_wr*100:5.1f}%")

    return {"thr": thr, "n": len(pnls), "wr": wr, "avg": avg, "sh": sh, "total": sum(pnls), "cl": mc, "lm": lm}

if __name__ == "__main__":
    import sys
    thr = float(sys.argv[1]) if len(sys.argv) > 1 else 1.5
    run_threshold(thr)

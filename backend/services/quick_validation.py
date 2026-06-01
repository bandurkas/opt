"""Quick validation: generate signals ONCE, reuse for all tests."""
import json, statistics, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from services.backtest import simulate_signal_set
from services.holdout_split import HOLDOUT_DAYS, holdout_cutoff_ms
from services.local_optimizer import find_data_dir, load_local
from services.solution_v3 import generate_solution_signals, apply_circuit_breaker

PUT_GEN = {"vol_threshold":0.50,"regime_filter":["range"],"side":"P","adx_max":None,
           "mtf_direction_filter":"up","bull_market_ratio_max":None,"cooldown_bars":4}
CALL_GEN = {"vol_threshold":0.60,"regime_filter":["range","transition"],"side":"C","adx_max":None,
            "mtf_direction_filter":"down","bull_market_ratio_max":1.05,"cooldown_bars":6}
PUT_EXIT = {"tp1":0.50,"tp2":0.70,"sl":1.50,"hold_h":96}
CALL_EXIT = {"tp1":0.30,"tp2":0.50,"sl":1.00,"hold_h":24}

def simulate_sigs(sigs, k5, sigma=0.6, spread=2.0):
    ps = [s for s in sigs if s["side"]=="P"]
    cs = [s for s in sigs if s["side"]=="C"]
    psim = simulate_signal_set(ps,k5,sigma=sigma,expiry_hours=168.0,tp1_pct=PUT_EXIT["tp1"],tp2_pct=PUT_EXIT["tp2"],sl_pct=PUT_EXIT["sl"],option_horizon_h=PUT_EXIT["hold_h"],spread_pct=spread) if ps else []
    csim = simulate_signal_set(cs,k5,sigma=sigma,expiry_hours=168.0,tp1_pct=CALL_EXIT["tp1"],tp2_pct=CALL_EXIT["tp2"],sl_pct=CALL_EXIT["sl"],option_horizon_h=CALL_EXIT["hold_h"],spread_pct=spread) if cs else []
    cb = apply_circuit_breaker(psim+csim, consec_limit=5, pause_bars=576)
    pnls = [s["option"]["pnl_pct"] for s in cb if "pnl_pct" in s.get("option",{})]
    if not pnls: return None
    wr=sum(1 for p in pnls if p>0)/len(pnls)
    st=statistics.stdev(pnls) if len(pnls)>1 else 0
    sh=(statistics.mean(pnls)/st) if st>0 else 0
    mc=cl=0
    for p in pnls:
        if p<0: cl+=1; mc=max(mc,cl)
        else: cl=0
    return {"n":len(pnls),"wr":round(wr,3),"avg":round(statistics.mean(pnls),2),"sharpe":round(sh,2),"total":round(sum(pnls),1),"max_consec_loss":mc,"pnls":pnls}

t0=time.time()
data_dir=find_data_dir(None)
k5,k15,k1h = load_local(data_dir)
print(f"klines: 5m={len(k5):,}",flush=True)

print("\n[1/3] Generating signals (takes ~2 min)...",flush=True)
all_sigs = generate_solution_signals(k5,k15,k1h,put_gen=PUT_GEN,call_gen=CALL_GEN,ret_threshold=2.0)
print(f"  Raw signals: {len(all_sigs)} (P={sum(1 for s in all_sigs if s['side']=='P')}, C={sum(1 for s in all_sigs if s['side']=='C')})",flush=True)

print("\n[2/3] Full 365d...",flush=True)
full = simulate_sigs(all_sigs, k5)
print(f"  n={full['n']} WR={full['wr']*100:.1f}% avg={full['avg']:+.2f}% sh={full['sharpe']:+.3f} cl={full['max_consec_loss']}",flush=True)

print(f"\n[3/3] Sensitivity (σ × spread)...",flush=True)
print(f"  {'σ':>4} {'spread':>6} {'n':>5} {'WR':>6} {'avg':>8} {'sh':>6} {'cl':>4}",flush=True)
sens_results = {}
for sigma in [0.40,0.50,0.60,0.70,0.80]:
    for spread in [1.0,2.0,4.0]:
        r = simulate_sigs(all_sigs, k5, sigma=sigma, spread=spread)
        sens_results[f"s{sigma}_sp{spread}"] = r
        print(f"  {sigma:.2f} {spread:5.1f}% {r['n']:>5} {r['wr']*100:>5.1f}% {r['avg']:>+7.2f}% {r['sharpe']:>+5.2f} {r['max_consec_loss']:>4}",flush=True)

# Holdout - use same signals filtered by cutoff
print(f"\n[Holdout] (last {HOLDOUT_DAYS}d)...",flush=True)
cutoff = holdout_cutoff_ms(k5)
ho_sigs = [s for s in all_sigs if s["ts_ms"]>=cutoff]
ho = simulate_sigs(ho_sigs, k5) if ho_sigs else None
if ho:
    print(f"  n={ho['n']} WR={ho['wr']*100:.1f}% avg={ho['avg']:+.2f}% sh={ho['sharpe']:+.3f} cl={ho['max_consec_loss']}",flush=True)
    monthly={}
    for s in apply_circuit_breaker(
        [s for s in ho_sigs for _ in [0]] or [], consec_limit=5, pause_bars=576
    ):
        pass  # already have ho.pnls
    # Recompute monthly from sims
    ho_ps=[s for s in ho_sigs if s["side"]=="P"]
    ho_cs=[s for s in ho_sigs if s["side"]=="C"]
    ho_psim=simulate_signal_set(ho_ps,k5,sigma=0.6,expiry_hours=168.0,tp1_pct=PUT_EXIT["tp1"],tp2_pct=PUT_EXIT["tp2"],sl_pct=PUT_EXIT["sl"],option_horizon_h=PUT_EXIT["hold_h"],spread_pct=2.0) if ho_ps else []
    ho_csim=simulate_signal_set(ho_cs,k5,sigma=0.6,expiry_hours=168.0,tp1_pct=CALL_EXIT["tp1"],tp2_pct=CALL_EXIT["tp2"],sl_pct=CALL_EXIT["sl"],option_horizon_h=CALL_EXIT["hold_h"],spread_pct=2.0) if ho_cs else []
    ho_cb=apply_circuit_breaker(ho_psim+ho_csim,consec_limit=5,pause_bars=576)
    for s in ho_cb:
        pnl=s.get("option",{}).get("pnl_pct")
        if pnl is None: continue
        ts=datetime.fromtimestamp(s["ts_ms"]/1000,tz=timezone.utc)
        monthly.setdefault(ts.strftime("%Y-%m"),[]).append(pnl)
    for m in sorted(monthly):
        ps=monthly[m]; m_avg=statistics.mean(ps); m_wr=sum(1 for p in ps if p>0)/len(ps)
        print(f"    {m}: n={len(ps):3d} avg={m_avg:+7.2f}% WR={m_wr*100:5.1f}%",flush=True)

print(f"\n{'='*60}")
print(f"PASS/FAIL:",flush=True)
if ho:
    print(f"  {'✅' if ho['avg']>5 else '❌'} Holdout avg > +5%: {ho['avg']:+.2f}%",flush=True)
    print(f"  {'✅' if ho['max_consec_loss']<20 else '❌'} Holdout cl < 20: {ho['max_consec_loss']}",flush=True)
    print(f"  {'✅' if ho['wr']>0.55 else '❌'} Holdout WR > 55%: {ho['wr']*100:.1f}%",flush=True)
pos_cells=sum(1 for r in sens_results.values() if r['avg']>0)
print(f"  {'✅' if pos_cells>=14 else '❌'} Sensitivity: {pos_cells}/15 cells positive",flush=True)
print(f"{'='*60}")
print(f"Done ({round(time.time()-t0,1)}s)",flush=True)

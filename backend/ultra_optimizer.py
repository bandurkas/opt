import sys, os, itertools, time
from multiprocessing import Pool, cpu_count
from functools import partial

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services import backtest_bs as bs
from services.backtest import generate_raw_signals
from services.backtest_data import fetch_set

def process_chunk(combo_chunk, klines_data, signals):
    results = []
    for combo in combo_chunk:
        expiry, tp1, tp2, sl, tsl_t, tsl_d, sigma = combo
        pnls = []
        for s in signals:
            start_ms = s["ts_ms"]
            side = "P"
            spot = s["close"]
            strike = round(spot / 25) * 25
            
            T0 = expiry / (24.0 * 365.0)
            mid = bs.price(side, spot, strike, T0, sigma)
            if mid <= 0.01: continue
            
            hs = 2.0 / 200.0
            entry = mid * (1 + hs)
            tp1_p = entry * (1 + tp1) / (1 - hs)
            tp2_p = entry * (1 + tp2) / (1 - hs)
            tsl_floor = entry * (1 - sl) / (1 - hs)
            
            path = [c for c in klines_data if start_ms < c["start_ms"] <= start_ms + int(expiry * 3600000)]
            if not path: continue
            
            pos1, pos2 = True, True
            e1, e2 = 0.0, 0.0
            
            for i, c in enumerate(path):
                T = max(0.0, (expiry - (i+1)*5/60) / (24*365))
                hi_p = bs.price(side, c["low"],  strike, T, sigma)
                lo_p = bs.price(side, c["high"], strike, T, sigma)
                
                hi_pnl = (hi_p*(1-hs) - entry) / entry
                if tsl_t > 0 and hi_pnl >= tsl_t:
                    nf = entry*(1+hi_pnl-tsl_d)/(1-hs)
                    if nf > tsl_floor: tsl_floor = nf
                    
                if lo_p <= tsl_floor:
                    ep = (tsl_floor*(1-hs)-entry)/entry
                    if pos1: e1=ep
                    if pos2: e2=ep
                    break
                    
                if pos2 and hi_p >= tp2_p: e2=(tp2_p*(1-hs)-entry)/entry; pos2=False
                if pos1 and hi_p >= tp1_p: e1=(tp1_p*(1-hs)-entry)/entry; pos1=False
                if not pos1 and not pos2: break
                
            if pos1 or pos2:
                T_l = max(0,(expiry-len(path)*5/60)/(24*365))
                fp=(bs.price(side,path[-1]["close"],strike,T_l,sigma)*(1-hs)-entry)/entry
                if pos1: e1=fp
                if pos2: e2=fp
                
            pnls.append(((e1+e2)/2)*100)
            
        if len(pnls) >= 20:
            wr = sum(1 for p in pnls if p > 0) / len(pnls)
            avg = sum(pnls) / len(pnls)
            results.append({"combo": combo, "wr": wr, "avg": avg, "n": len(pnls)})
            
    return results

def main():
    print("Fetching data...", flush=True)
    data = fetch_set("ETHUSDT", days=60, intervals=("5","15","60"))
    k5, k15, k1h = data["5"], data["15"], data["60"]

    all_sigs = generate_raw_signals(k5, k15, k1h, min_alignment=2, cooldown_bars=6, fade=True)
    sigs = [s for s in all_sigs if s["side"]=="P" and s["regime"]=="trend" and s["mtf_aligned"]==2 and s["score"]<=8 and not s.get("accelerating", True)]
    
    print(f"Signals ready: {len(sigs)}. Generating combos...", flush=True)

    expiries = [24, 48, 72, 96, 120, 168]
    tp1s     = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40]
    tp2s     = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 1.0]
    sls      = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
    tsls     = [(0.0,0.0), (0.15,0.10), (0.20,0.15), (0.25,0.20), (0.30,0.15), (0.35,0.20)]
    sigmas   = [0.60, 0.65]

    all_combos = []
    for exp, tp1, tp2, sl, (tsl_t, tsl_d), sig in itertools.product(expiries, tp1s, tp2s, sls, tsls, sigmas):
        if tp2 > tp1:
            all_combos.append((exp, tp1, tp2, sl, tsl_t, tsl_d, sig))
            
    print(f"Total valid combinations: {len(all_combos)}", flush=True)
    
    cores = cpu_count()
    print(f"Using {cores} CPU cores for parallel processing.", flush=True)
    
    chunk_size = len(all_combos) // cores + 1
    chunks = [all_combos[i:i + chunk_size] for i in range(0, len(all_combos), chunk_size)]
    
    start_time = time.time()
    
    with Pool(cores) as p:
        func = partial(process_chunk, klines_data=k5, signals=sigs)
        results_list = p.map(func, chunks)
        
    print(f"Optimization finished in {time.time() - start_time:.2f} seconds.", flush=True)
    
    final_results = []
    for r_list in results_list:
        final_results.extend(r_list)
        
    top = sorted(final_results, key=lambda x: -x["avg"])[:15]
    
    print("\n" + "="*80)
    print("TOP 15 OVERALL (Massive Grid Search)")
    print("="*80)
    print("EXP  TP1   TP2   SL    TSL_T TSL_D SIGMA | N/day   WR     Avg P&L")
    for r in top:
        c = r["combo"]
        print(f"{c[0]:<4} {c[1]:.2f}  {c[2]:.2f}  {c[3]:.2f}  {c[4]:.2f}  {c[5]:.2f}  {c[6]:.2f}  | {r['n']/60:.1f}    {r['wr']*100:.1f}%  {r['avg']:+.2f}%")

if __name__=="__main__": main()

import sys, os, time, itertools
import numpy as np
from scipy.stats import norm

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.backtest import generate_raw_signals
from services.backtest_data import fetch_set

def bs_put_price(S, K, T, r, sigma):
    # Vectorized Black-Scholes Put pricing
    # S, K, T, r, sigma can be numpy arrays
    with np.errstate(divide='ignore'):
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
    
    put = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    # Handle expiration T=0
    put = np.where(T <= 0, np.maximum(K - S, 0), put)
    return put

def main():
    print("Fetching data...", flush=True)
    data = fetch_set("ETHUSDT", days=60, intervals=("5","15","60"))
    k5, k15, k1h = data["5"], data["15"], data["60"]

    all_sigs = generate_raw_signals(k5, k15, k1h, min_alignment=2, cooldown_bars=6, fade=True)
    sigs = [s for s in all_sigs if s["side"]=="P" and s["regime"]=="trend" and s["mtf_aligned"]==2 and s["score"]<=8 and not s.get("accelerating", True)]
    
    print(f"Signals ready: {len(sigs)}. Preparing NumPy vectors...", flush=True)
    
    # Grid
    expiries = [24.0, 48.0, 72.0, 96.0, 120.0, 168.0]
    tp1s     = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40]
    tp2s     = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 1.0]
    sls      = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
    tsls     = [(0.0,0.0), (0.15,0.10), (0.20,0.15), (0.25,0.20), (0.30,0.15), (0.35,0.20)]
    sigmas   = [0.60, 0.65]

    # Pre-extract all klines into a fast lookup array
    times = np.array([c["start_ms"] for c in k5])
    highs = np.array([c["high"] for c in k5])
    lows = np.array([c["low"] for c in k5])
    closes = np.array([c["close"] for c in k5])
    
    # We will compute the results for all combinations
    all_combos = []
    for exp, tp1, tp2, sl, (tsl_t, tsl_d), sig in itertools.product(expiries, tp1s, tp2s, sls, tsls, sigmas):
        if tp2 > tp1:
            all_combos.append((exp, tp1, tp2, sl, tsl_t, tsl_d, sig))
            
    print(f"Total combinations to evaluate: {len(all_combos)}", flush=True)
    start_time = time.time()
    
    # To optimize, we can group by (expiry, sigma) because the PnL path only depends on these.
    # For a given (expiry, sigma), we compute the PnL trajectory for each signal ONCE.
    # Then we apply the TP/SL logic using fast numpy operations.
    
    best_results = []
    
    grouped = {}
    for c in all_combos:
        key = (c[0], c[6]) # (expiry, sigma)
        if key not in grouped: grouped[key] = []
        grouped[key].append(c)
        
    for (exp, sig), combos in grouped.items():
        # Precompute PnL trajectories for all signals for this (exp, sig) pair
        # signals_pnl_high will be list of 1D arrays
        # signals_pnl_low will be list of 1D arrays
        
        trajectories = []
        
        for s in sigs:
            start_ms = s["ts_ms"]
            spot = s["close"]
            strike = round(spot / 25) * 25
            
            # Entry
            T0 = exp / (24.0 * 365.0)
            mid = bs_put_price(np.array([spot]), np.array([strike]), np.array([T0]), 0.05, sig)[0]
            if mid <= 0.01:
                trajectories.append(None)
                continue
                
            hs = 2.0 / 200.0
            entry = mid * (1 + hs)
            
            # Find path
            start_idx = np.searchsorted(times, start_ms, side='right')
            end_ms = start_ms + int(exp * 3600000)
            end_idx = np.searchsorted(times, end_ms, side='right')
            
            if start_idx == end_idx:
                trajectories.append(None)
                continue
                
            path_len = end_idx - start_idx
            # T decays by 5m each step
            elapsed_h = np.arange(1, path_len + 1) * 5.0 / 60.0
            T = np.maximum(0.0, (exp - elapsed_h) / (24.0 * 365.0))
            
            path_hi = highs[start_idx:end_idx]
            path_lo = lows[start_idx:end_idx]
            
            # For Puts, high premium comes from low spot
            hi_prem = bs_put_price(path_lo, strike, T, 0.05, sig)
            lo_prem = bs_put_price(path_hi, strike, T, 0.05, sig)
            
            hi_pnl = (hi_prem * (1 - hs) - entry) / entry
            lo_pnl = (lo_prem * (1 - hs) - entry) / entry
            
            # Final step evaluation (Time stop)
            T_last = max(0.0, (exp - path_len * 5.0 / 60.0) / (24.0 * 365.0))
            final_prem = bs_put_price(np.array([closes[end_idx-1]]), np.array([strike]), np.array([T_last]), 0.05, sig)[0]
            final_pnl = (final_prem * (1 - hs) - entry) / entry
            
            trajectories.append((hi_pnl, lo_pnl, final_pnl))
            
        # Now evaluate all combos for this (exp, sig) group
        for combo in combos:
            _, tp1, tp2, sl, tsl_t, tsl_d, _ = combo
            
            pnls = []
            for traj in trajectories:
                if traj is None: continue
                hi_pnl, lo_pnl, final_pnl = traj
                
                # Fast evaluation using numpy
                # We need to find the exact exit point. Since path length is small (e.g. 1152 for 96h),
                # a simple numba-like or standard loop in pure python is still fast enough if we don't call BS.
                # Actually, doing this in python per signal is very fast since arrays are already computed.
                
                pos1, pos2 = True, True
                e1, e2 = 0.0, 0.0
                tsl_floor = -sl
                
                # We can optimize this by finding the first index where condition met, but since TSL is dynamic,
                # we iterate.
                for i in range(len(hi_pnl)):
                    cur_hi = hi_pnl[i]
                    cur_lo = lo_pnl[i]
                    
                    if tsl_t > 0 and cur_hi >= tsl_t:
                        nf = cur_hi - tsl_d
                        if nf > tsl_floor: tsl_floor = nf
                        
                    if cur_lo <= tsl_floor:
                        if pos1: e1 = tsl_floor
                        if pos2: e2 = tsl_floor
                        pos1 = pos2 = False
                        break
                        
                    if pos2 and cur_hi >= tp2:
                        e2 = tp2; pos2 = False
                        
                    if pos1 and cur_hi >= tp1:
                        e1 = tp1; pos1 = False
                        
                    if not pos1 and not pos2:
                        break
                        
                if pos1 or pos2:
                    if pos1: e1 = final_pnl
                    if pos2: e2 = final_pnl
                    
                pnls.append(((e1+e2)/2.0)*100.0)
                
            if len(pnls) >= 20:
                wr = sum(1 for p in pnls if p > 0) / len(pnls)
                avg = sum(pnls) / len(pnls)
                best_results.append({
                    "combo": combo, "wr": wr, "avg": avg, "n": len(pnls)
                })

    print(f"NumPy Vectorized Engine Finished in {time.time() - start_time:.2f} seconds.", flush=True)
    
    top = sorted(best_results, key=lambda x: -x["avg"])[:15]
    
    print("\n" + "="*80)
    print("TOP 15 OVERALL (Massive Grid Search)")
    print("="*80)
    print("EXP  TP1   TP2   SL    TSL_T TSL_D SIGMA | N/day   WR     Avg P&L")
    for r in top:
        c = r["combo"]
        print(f"{c[0]:<4.0f} {c[1]:.2f}  {c[2]:.2f}  {c[3]:.2f}  {c[4]:.2f}  {c[5]:.2f}  {c[6]:.2f}  | {r['n']/60:.1f}    {r['wr']*100:.1f}%  {r['avg']:+.2f}%")

if __name__=="__main__": main()

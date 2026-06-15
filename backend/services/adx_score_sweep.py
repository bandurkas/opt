"""Sweep ADX score configs."""
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir, load_local
from services.strategy_config import CALL_EXIT, DEFAULT_SIGMA, PUT_EXIT, SPREAD_HALF_PCT
from services.strategy_registry import gen_sell_premium_iv_high

def calculate_stats(sims):
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return None
        
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    st = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
    sh = (statistics.mean(pnls) / st) if st > 0 else 0.0
    
    mc = cl = 0
    for p in pnls:
        cl = cl + 1 if p <= 0 else 0
        mc = max(mc, cl)
        
    monthly = {}
    equity = 1000.0
    peak = equity
    max_dd = 0.0
    
    for s in sims:
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m")
        pnl = s.get("option", {}).get("pnl_pct")
        if pnl is not None:
            monthly.setdefault(ts, []).append(pnl)
            
            # DD calculation ($100 per trade)
            equity += (pnl / 100.0) * 100.0
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100)
            
    lm = sum(1 for ps in monthly.values() if statistics.mean(ps) < 0)
    
    avg = statistics.mean(pnls)
    return {
        "n": len(pnls),
        "wr": wr,
        "avg": avg,
        "sharpe": sh,
        "total": sum(pnls),
        "mc": mc,
        "lm": lm,
        "tm": len(monthly),
        "max_dd": max_dd,
        "rank": avg * 0.35 + sh * 0.25 + wr * 100 * 0.20 - lm * 0.10 - max_dd * 0.10
    }

def main():
    print("Loading data...")
    k5, k15, k1h = load_local(find_data_dir(None))
    print(f"Loaded klines: 5m={len(k5)}, 15m={len(k15)}, 1h={len(k1h)}")
    
    adx_scores = [None, 3, 4, 5, 6, 7]
    vols = [0.40, 0.50, 0.60]
    mtfs = [1, 2]
    bulls = [None, 1.05, 1.10]
    
    print("Generating signal superset...")
    # Generate ALL signals with the lowest thresholds and no optional filters
    all_p_sigs = gen_sell_premium_iv_high(
        k5, k15, k1h, side="P",
        vol_threshold=0.40, adx_score_min=None,
        mtf_direction_filter=None, mtf_min_aligned=1, bull_market_ratio_max=None
    )
    all_c_sigs = gen_sell_premium_iv_high(
        k5, k15, k1h, side="C",
        vol_threshold=0.40, adx_score_min=None,
        mtf_direction_filter=None, mtf_min_aligned=1, bull_market_ratio_max=None
    )
    print(f"Generated {len(all_p_sigs)} potential P signals, {len(all_c_sigs)} potential C signals.")
    
    configs = list(product(adx_scores, vols, mtfs, bulls))
    print(f"Running {len(configs)} configurations...")
    
    results = []
    start_time = time.time()
    
    for idx, (adx_min, vol, mtf, bull) in enumerate(configs):
        if idx % 10 == 0:
            print(f"  progress: {idx}/{len(configs)}")
            
        # Filter P signals
        p_sigs = []
        for s in all_p_sigs:
            # Reapply filters
            if vol is not None:
                thr = s["vol_sorted"][int(len(s["vol_sorted"]) * vol)]
                if s["vol_current"] < thr: continue
            if adx_min is not None and s.get("adx_score", 0) < adx_min: continue
            if s.get("mtf_direction") != "down" or s.get("mtf_aligned", 0) < mtf: continue
            if bull is not None and s.get("bull_ratio") and s["bull_ratio"] > bull: continue
            p_sigs.append(s)
            
        # Filter C signals
        c_sigs = []
        for s in all_c_sigs:
            if vol is not None:
                thr = s["vol_sorted"][int(len(s["vol_sorted"]) * vol)]
                if s["vol_current"] < thr: continue
            if adx_min is not None and s.get("adx_score", 0) < adx_min: continue
            if s.get("mtf_direction") != "up" or s.get("mtf_aligned", 0) < mtf: continue
            c_sigs.append(s)
            
        sims = []
        if p_sigs:
            sims += simulate_signal_set(
                p_sigs, k5, sigma=DEFAULT_SIGMA, expiry_hours=168.0,
                tp1_pct=PUT_EXIT["tp1_pct"], tp2_pct=PUT_EXIT["tp2_pct"], sl_pct=PUT_EXIT["sl_pct"],
                option_horizon_h=PUT_EXIT["hold_h"], spread_pct=SPREAD_HALF_PCT * 2
            )
        if c_sigs:
            sims += simulate_signal_set(
                c_sigs, k5, sigma=DEFAULT_SIGMA, expiry_hours=168.0,
                tp1_pct=CALL_EXIT["tp1_pct"], tp2_pct=CALL_EXIT["tp2_pct"], sl_pct=CALL_EXIT["sl_pct"],
                option_horizon_h=CALL_EXIT["hold_h"], spread_pct=SPREAD_HALF_PCT * 2
            )
            
        st = calculate_stats(sims)
        if st:
            st["adx_min"] = adx_min
            st["vol"] = vol
            st["mtf"] = mtf
            st["bull"] = bull
            results.append(st)
            
    elapsed = time.time() - start_time
    print(f"Sweep complete in {elapsed:.1f}s")
    
    # Sort by rank
    results.sort(key=lambda x: x["rank"], reverse=True)
    
    # Output best
    print("=" * 90)
    print("ADX SCORE SWEEP — 365d backtest")
    print("=" * 90)
    print(f"{'#':>3}  {'adx':>5}  {'vol':>4}  {'mtf':>3}  {'bull':>5}  |  {'n':>5}  {'WR':>6}  {'avg':>6}  {'sh':>5}  {'tot':>6}  {'mc':>3}  {'lm':>2}  {'DD%':>5}")
    for i, r in enumerate(results[:20]):
        bull_str = str(r['bull']) if r['bull'] else "None"
        adx_str = str(r['adx_min']) if r['adx_min'] is not None else "base"
        print(f"{i+1:>3}  {adx_str:>5}  {r['vol']:.2f}  {r['mtf']:>3}  {bull_str:>5}  |  {r['n']:>5}  {r['wr']*100:>5.1f}%  {r['avg']:>+5.2f}  {r['sharpe']:>+5.2f}  {r['total']:>+6.1f}  {r['mc']:>3}  {r['lm']:>2}  {r['max_dd']:>4.1f}%")
        
    out_path = Path(__file__).resolve().parents[2] / "sweep_results" / "adx_score_sweep.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"results": results}, indent=2))
    print(f"Saved complete results to {out_path}")

if __name__ == "__main__":
    main()

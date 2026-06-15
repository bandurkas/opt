"""Sweep script to test hybrid ADX score approaches (Sizing and Soft filtering)."""
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir, load_local
from services.strategy_config import CALL_EXIT, DEFAULT_SIGMA, PUT_EXIT, SPREAD_HALF_PCT
from services.strategy_registry import gen_sell_premium_iv_high

def calculate_weighted_stats(sims, sizing_rules, filter_min_score=None):
    """
    sizing_rules: list of tuples (max_score_inclusive, multiplier)
    e.g. [(3, 0.0), (6, 0.5), (10, 1.5)]
    """
    pnls_pct = []
    pnl_dollars = []
    
    equity = 1000.0
    peak = equity
    max_dd = 0.0
    monthly = {}
    
    # Process chronologically
    sims.sort(key=lambda x: x["ts_ms"])
    
    trades_taken = 0
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt:
            continue
            
        score = s.get("adx_score", 0)
        
        # Soft filter
        if filter_min_score is not None and score < filter_min_score:
            continue
            
        # Determine size multiplier
        mult = 1.0
        if sizing_rules:
            for max_sc, m in sizing_rules:
                if score <= max_sc:
                    mult = m
                    break
                    
        if mult <= 0.0:
            continue
            
        pnl = opt["pnl_pct"]
        pnls_pct.append(pnl) # Raw pct for win rate
        
        dollar_pnl = (pnl / 100.0) * (100.0 * mult)
        pnl_dollars.append(dollar_pnl)
        
        trades_taken += 1
        
        # Drawdown and Equity
        equity += dollar_pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
            
        # Monthly
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m")
        monthly.setdefault(ts, []).append(dollar_pnl)
        
    if not pnl_dollars:
        return None
        
    wr = sum(1 for p in pnls_pct if p > 0) / len(pnls_pct)
    st = statistics.stdev(pnl_dollars) if len(pnl_dollars) > 1 else 0.0
    sh = (statistics.mean(pnl_dollars) / st) if st > 0 else 0.0
    
    lm = sum(1 for ps in monthly.values() if sum(ps) < 0)
    
    # Average PNL in % terms based on average size
    # So if we make $10 on a $100 trade, it's 10%
    # If we make $15 on a $150 trade, it's 10%
    # If average trade size was 100, then avg $ = avg %
    
    return {
        "n": trades_taken,
        "wr": wr,
        "avg_$": statistics.mean(pnl_dollars),
        "total_$": sum(pnl_dollars),
        "sharpe": sh,
        "lm": lm,
        "tm": len(monthly),
        "max_dd": max_dd
    }

def main():
    print("Loading data...")
    k5, k15, k1h = load_local(find_data_dir(None))
    
    print("Generating signal superset (no ADX filters, Vol=0.85 as baseline)...")
    
    # We use baseline parameters except we drop regime_filter to let ALL signals through,
    # then we score them.
    # The baseline variant_backtest used PUT_GEN_KWARGS vol_threshold (0.85).
    # We will use 0.85 to match baseline trade count.
    all_p_sigs = gen_sell_premium_iv_high(
        k5, k15, k1h, side="P",
        vol_threshold=0.50, regime_filter=None, adx_score_min=None,
        mtf_direction_filter="down", mtf_min_aligned=2, bull_market_ratio_max=None
    )
    all_c_sigs = gen_sell_premium_iv_high(
        k5, k15, k1h, side="C",
        vol_threshold=0.60, regime_filter=None, adx_score_min=None,
        mtf_direction_filter="up", mtf_min_aligned=2, bull_market_ratio_max=1.05
    )
    print(f"Generated {len(all_p_sigs)} P signals, {len(all_c_sigs)} C signals.")
    
    print("Simulating signals (this takes ~10 seconds)...")
    sims = []
    if all_p_sigs:
        sims += simulate_signal_set(
            all_p_sigs, k5, sigma=DEFAULT_SIGMA, expiry_hours=168.0,
            tp1_pct=PUT_EXIT["tp1_pct"], tp2_pct=PUT_EXIT["tp2_pct"], sl_pct=PUT_EXIT["sl_pct"],
            option_horizon_h=PUT_EXIT["hold_h"], spread_pct=SPREAD_HALF_PCT * 2
        )
    if all_c_sigs:
        sims += simulate_signal_set(
            all_c_sigs, k5, sigma=DEFAULT_SIGMA, expiry_hours=168.0,
            tp1_pct=CALL_EXIT["tp1_pct"], tp2_pct=CALL_EXIT["tp2_pct"], sl_pct=CALL_EXIT["sl_pct"],
            option_horizon_h=CALL_EXIT["hold_h"], spread_pct=SPREAD_HALF_PCT * 2
        )
        
    baseline_sims = []
    for s in sims:
        regime = s.get("regime", "unknown")
        side = s.get("side", "")
        if side == "P" and regime == "range":
            baseline_sims.append(s)
        elif side == "C" and regime in ["range", "transition"]:
            baseline_sims.append(s)

    variants = [
        {"name": "1. Baseline (Binary Filter)", "filter": None, "sizing": None, "use_sims": baseline_sims},
        {"name": "2. Soft Filter (Score >= 3)", "filter": 3, "sizing": None, "use_sims": sims},
        {"name": "3. Soft Filter (Score >= 4)", "filter": 4, "sizing": None, "use_sims": sims},
        {"name": "4. Hard Filter (Score >= 7)", "filter": 7, "sizing": None, "use_sims": sims},
        {"name": "5. Sizing A (0-4: 0.5x, 5-7: 1.0x, 8-10: 1.5x)", "filter": None, "sizing": [(4, 0.5), (7, 1.0), (10, 1.5)], "use_sims": sims},
        {"name": "6. Sizing B (0-3: 0.25x, 4-6: 1.0x, 7-10: 1.5x)", "filter": None, "sizing": [(3, 0.25), (6, 1.0), (10, 1.5)], "use_sims": sims},
        {"name": "7. Sizing C (0-4: Drop, 5-7: 1.0x, 8-10: 1.5x)", "filter": None, "sizing": [(4, 0.0), (7, 1.0), (10, 1.5)], "use_sims": sims},
        {"name": "8. Baseline + Sizing (Base Filter + 5-7: 1x, 8-10: 1.5x)", "filter": None, "sizing": [(7, 1.0), (10, 1.5)], "use_sims": baseline_sims},
    ]

    print("\n" + "=" * 105)
    print(f"{'Variant Name':<45} | {'n':>5} {'WR':>6} {'avg $':>7} {'sh':>5} {'tot $':>8} {'lm':>2} {'DD%':>5}")
    print("-" * 105)
    
    for v in variants:
        st = calculate_weighted_stats(v["use_sims"], v["sizing"], v["filter"])
        if not st:
            print(f"{v['name']:<45} | 0 trades")
            continue
            
        print(f"{v['name']:<45} | {st['n']:>5} {st['wr']*100:>5.1f}% {st['avg_$']:>+7.2f} {st['sharpe']:>+5.2f} {st['total_$']:>+8.1f} {st['lm']:>2} {st['max_dd']:>4.1f}%")

if __name__ == "__main__":
    main()

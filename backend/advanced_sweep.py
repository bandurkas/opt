import itertools
import sys
import json
import time
from services.backtest import generate_raw_signals, simulate_signal_set
from services.backtest_data import fetch_set

def main():
    symbol = "ETHUSDT"
    days = 60
    sigma = 0.60
    spread_pct = 2.0
    
    print(f"Fetching data for {days} days...", flush=True)
    data = fetch_set(symbol, days=days, intervals=("5", "15", "60"))
    klines_5m, klines_15m, klines_1h = data["5"], data["15"], data["60"]

    cooldowns = [4, 6, 8, 12]
    expiries = [48.0, 72.0, 96.0, 120.0]
    tp1s = [0.15, 0.20, 0.25, 0.30]
    tp2s = [0.40, 0.50, 0.60, 0.70]
    sls = [0.25, 0.30, 0.35, 0.40]

    best_results = []
    
    print("Starting grid search...", flush=True)

    for cooldown in cooldowns:
        signals = generate_raw_signals(klines_5m, klines_15m, klines_1h, min_alignment=2, cooldown_bars=cooldown, fade=True)
        # Filter for Trend, Puts, and exactly MTF=2/3
        signals = [s for s in signals if s["side"] == "P" and s["regime"] == "trend" and s["mtf_aligned"] == 2]

        print(f"Cooldown {cooldown}: Found {len(signals)} raw signals. Testing TP/SL combinations...", flush=True)

        for expiry, tp1, tp2, sl in itertools.product(expiries, tp1s, tp2s, sls):
            if tp2 <= tp1: continue
            
            sims = simulate_signal_set(signals, klines_5m, sigma, expiry, tp1, tp2, sl, option_horizon_h=12.0, spread_pct=spread_pct)
            
            pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s["option"]]
            if not pnls: continue
            
            wr = sum(1 for p in pnls if p > 0) / len(pnls)
            avg_pnl = sum(pnls) / len(pnls)
            count = len(pnls)
            
            # We want high frequency (e.g. 150+ signals over 60 days) and high profitability
            if count >= 150 and avg_pnl > 6.0:
                best_results.append({
                    "cooldown": cooldown, "expiry": expiry, "tp1": tp1, "tp2": tp2, "sl": sl,
                    "count": count, "wr": wr, "avg_pnl": avg_pnl
                })

    best_results.sort(key=lambda x: x["avg_pnl"], reverse=True)
    
    print("\n" + "="*80)
    print("TOP 25 COMBINATIONS (High Frequency + High P&L)")
    print("="*80)
    print(f"{'CD':>4} {'EXP':>5} {'TP1':>5} {'TP2':>5} {'SL':>5} | {'N':>4} {'WR':>6} {'Avg%':>7}")
    print("-" * 60)
    for r in best_results[:25]:
        print(f"{r['cooldown']:>4} {r['expiry']:>5.0f} {r['tp1']:>5.2f} {r['tp2']:>5.2f} {r['sl']:>5.2f} | {r['count']:>4} {r['wr']*100:>5.1f}% {r['avg_pnl']:>6.2f}%")

if __name__ == "__main__":
    main()

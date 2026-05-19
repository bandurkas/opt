import sys
import os
import itertools
from services import backtest_bs as bs
from services.backtest import generate_raw_signals
from services.backtest_data import fetch_set

def _simulate_option_trade_tsl(signal, klines_5m, sigma, expiry_h, tp1, tp2, sl, tsl_trigger, tsl_distance, spread_pct=2.0):
    start_ms = signal["ts_ms"]
    end_ms = start_ms + int(expiry_h * 3600 * 1000)
    side = signal["side"]
    entry_spot = signal["close"]
    strike = round(entry_spot / 25) * 25
    
    # Entry price
    T0 = expiry_h / (24.0 * 365.0)
    bs_mid = bs.price(side, entry_spot, strike, T0, sigma)
    if bs_mid <= 0.01:
        return None
        
    half_spread = spread_pct / 200.0
    entry_price = bs_mid * (1 + half_spread)
    
    tp1_price = entry_price * (1 + tp1) / (1 - half_spread) if half_spread < 1 else entry_price * (1 + tp1)
    tp2_price = entry_price * (1 + tp2) / (1 - half_spread) if half_spread < 1 else entry_price * (1 + tp2)
    sl_price = entry_price * (1 - sl) / (1 - half_spread) if half_spread < 1 else entry_price * (1 - sl)
    
    # Filter klines
    path = [c for c in klines_5m if start_ms < c["start_ms"] <= end_ms]
    if not path:
        return None
        
    pos1_active = True
    pos2_active = True
    current_sl = sl_price
    
    max_pnl = 0.0
    exit_pnl1 = 0.0
    exit_pnl2 = 0.0
    
    for i, c in enumerate(path):
        t_ms = c["start_ms"]
        rem_h = expiry_h - (i + 1) * 5 / 60.0
        T = max(0.0, rem_h / (24.0 * 365.0))
        
        hi_spot = c["high"]
        lo_spot = c["low"]
        
        if side == "C":
            hi_prem = bs.price(side, hi_spot, strike, T, sigma)
            lo_prem = bs.price(side, lo_spot, strike, T, sigma)
        else:
            hi_prem = bs.price(side, lo_spot, strike, T, sigma)
            lo_prem = bs.price(side, hi_spot, strike, T, sigma)
            
        pnl_pct_high = (hi_prem * (1 - half_spread) - entry_price) / entry_price
        
        # Update TSL based on high
        if tsl_trigger > 0 and pnl_pct_high >= tsl_trigger:
            new_sl_price = entry_price * (1 + (pnl_pct_high - tsl_distance)) / (1 - half_spread)
            if new_sl_price > current_sl:
                current_sl = new_sl_price
                
        # Low triggers SL
        if lo_prem <= current_sl:
            if pos1_active:
                exit_pnl1 = (current_sl * (1 - half_spread) - entry_price) / entry_price
                pos1_active = False
            if pos2_active:
                exit_pnl2 = (current_sl * (1 - half_spread) - entry_price) / entry_price
                pos2_active = False
            return ((exit_pnl1 + exit_pnl2) / 2.0) * 100.0
            
        # High triggers TP2
        if pos2_active and hi_prem >= tp2_price:
            exit_pnl2 = (tp2_price * (1 - half_spread) - entry_price) / entry_price
            pos2_active = False
            
        # High triggers TP1
        if pos1_active and hi_prem >= tp1_price:
            exit_pnl1 = (tp1_price * (1 - half_spread) - entry_price) / entry_price
            pos1_active = False
            
        if not pos1_active and not pos2_active:
            break
            
    # Time Expiry
    if pos1_active or pos2_active:
        last_spot = path[-1]["close"] if path else entry_spot
        T = 0
        final_mid = bs.price(side, last_spot, strike, T, sigma)
        final_received = final_mid * (1 - half_spread)
        pnl = (final_received - entry_price) / entry_price
        if pos1_active: exit_pnl1 = pnl
        if pos2_active: exit_pnl2 = pnl
        
    avg_pnl = (exit_pnl1 + exit_pnl2) / 2.0
    return avg_pnl * 100.0

def main():
    print("Fetching data...")
    data = fetch_set("ETHUSDT", days=60, intervals=("5", "15", "60"))
    
    cooldown = 6
    expiry = 120.0
    tp1, tp2, sl = 0.30, 0.50, 0.40
    
    signals = generate_raw_signals(data["5"], data["15"], data["60"], min_alignment=2, cooldown_bars=cooldown, fade=True)
    signals = [s for s in signals if s["side"] == "P" and s["regime"] == "trend" and s["mtf_aligned"] == 2]
    
    print(f"Testing Trailing Stop Loss on {len(signals)} signals...")
    
    tsls = [
        (0.0, 0.0),
        (0.20, 0.15),
        (0.25, 0.20),
        (0.30, 0.15),
        (0.35, 0.20),
        (0.15, 0.15)
    ]
    
    for trig, dist in tsls:
        pnls = []
        for s in signals:
            pnl = _simulate_option_trade_tsl(s, data["5"], 0.60, expiry, tp1, tp2, sl, trig, dist)
            if pnl is not None:
                pnls.append(pnl)
                
        if pnls:
            wr = sum(1 for p in pnls if p > 0) / len(pnls)
            avg = sum(pnls) / len(pnls)
            print(f"TSL Trig={trig*100:2.0f}% Dist={dist*100:2.0f}% -> WR: {wr*100:.1f}%, Avg P&L: {avg:+.2f}%")

if __name__ == "__main__":
    main()

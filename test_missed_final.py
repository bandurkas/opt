import sys
sys.path.insert(0, '/app')
from services.missed_signals import compute_missed_signals

res = compute_missed_signals(14, True)
print(f"Win rate: {res.get('win_rate')}")
print(f"Trades: {res.get('n_signals')}")
print(f"Avg PnL: {res.get('avg_pnl_pct_per_trade')}")
print(f"Total PnL USD: {res.get('total_pnl_usd')}")
print(f"Start Eq: {res.get('start_equity_usd')} -> Final Eq: {res.get('final_equity_usd')}")
print(f"Skipped CB: {res.get('n_skipped_by_cb')}, Busy: {res.get('n_skipped_by_busy')}, Margin: {res.get('n_skipped_by_margin')}")

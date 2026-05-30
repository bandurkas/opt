import sys, os, time, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.backtest import simulate_signal_set
from services.backtest_data import fetch_set
from services.strategy_registry import gen_sell_premium_iv_high

WINNER = {
    "gen_kwargs": {
        "vol_threshold": 0.6,
        "regime_filter": ["range", "transition"],
        "side": "C",
        "adx_max": None,
        "mtf_direction_filter": "down",
        "bull_market_ratio_max": 1.05,
        "cooldown_bars": 6,
    },
    "exit": {
        "tp1": 0.30, "tp2": 0.50, "sl": 0.50,
        "hold_h": 24, "tsl_t": 0.0, "tsl_o": 0.0,
    },
    "sigma": 0.6,
    "spread_pct": 2.5,
}

print("Fetching 15 days of data...", flush=True)
data = fetch_set("ETHUSDT", days=15, intervals=("5", "15", "60"))
k5, k15, k1h = data["5"], data["15"], data["60"]

if not k5:
    print("No data fetched.")
    sys.exit(0)

print("Generating signals...", flush=True)
signals = gen_sell_premium_iv_high(k5, k15, k1h, **WINNER["gen_kwargs"])

print(f"Simulating {len(signals)} signals...", flush=True)
sims = simulate_signal_set(
    signals, k5,
    sigma=WINNER["sigma"], expiry_hours=168.0,
    tp1_pct=WINNER["exit"]["tp1"], tp2_pct=WINNER["exit"]["tp2"], sl_pct=WINNER["exit"]["sl"],
    option_horizon_h=WINNER["exit"]["hold_h"], spread_pct=WINNER["spread_pct"],
)

pnls = []
for s in sims:
    opt = s.get("option", {})
    if "pnl_pct" in opt:
        pnls.append(opt["pnl_pct"] - 2.0)

if pnls:
    wins = [p for p in pnls if p > 0]
    print(f"=== 15-DAY VERIFICATION ===")
    print(f"Total Trades: {len(pnls)}")
    print(f"Win Rate: {len(wins)/len(pnls)*100:.1f}%")
    print(f"Avg PnL (Net of Fees & Spread): {statistics.mean(pnls):+.2f}%")
else:
    print("No trades generated.")

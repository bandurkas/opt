"""Monthly P&L estimate with realistic sizing and friction.

Calculates expected $/month for $400 deposit accounting for:
  - Bybit margin requirements (IM rate ~10% of strike-notional)
  - 0.1 ETH min lot size
  - Real IV (σ=0.40 vs backtest σ=0.60)
  - 2% spread + 0.03% taker fee
  - Circuit breaker (skips trades during cooldown)
  - Portfolio margin cap (80% of equity)

Run:
    cd backend && PYTHONPATH=. python3 services/monthly_profit_estimate.py
"""
import statistics, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from services.backtest import simulate_signal_set
from services.backtest_bs import price as bs_price
from services.holdout_split import holdout_cutoff_ms
from services.local_optimizer import find_data_dir, load_local
from services.solution_v3 import generate_solution_signals, apply_circuit_breaker

PUT_GEN = {"vol_threshold":0.50,"regime_filter":["range"],"side":"P","adx_max":None,
           "mtf_direction_filter":"up","bull_market_ratio_max":None,"cooldown_bars":4}
CALL_GEN = {"vol_threshold":0.60,"regime_filter":["range","transition"],"side":"C","adx_max":None,
            "mtf_direction_filter":"down","bull_market_ratio_max":1.05,"cooldown_bars":6}
PUT_EXIT = {"tp1":0.50,"tp2":0.70,"sl":1.50,"hold_h":96}
CALL_EXIT = {"tp1":0.30,"tp2":0.50,"sl":1.00,"hold_h":24}

# Realistic sizing params (from paper_strategy.py)
START_EQUITY = 400.0
MARGIN_PCT_PER_TRADE = 0.15  # 15% of equity per trade
LOT_SIZE = 0.1  # ETH
IM_RATE = 0.10  # Bybit cross-margin IM rate
MAX_PORTFOLIO_MARGIN = 0.80  # 80% of equity

def estimate_monthly(k5, k15, k1h, sigma=0.6, real_iv=False):
    """Estimate monthly $ P&L with realistic sizing."""
    sigs = generate_solution_signals(k5,k15,k1h,put_gen=PUT_GEN,call_gen=CALL_GEN,ret_threshold=2.0)
    ps = [s for s in sigs if s["side"]=="P"]
    cs = [s for s in sigs if s["side"]=="C"]
    psim = simulate_signal_set(ps,k5,sigma=sigma,expiry_hours=168.0,tp1_pct=PUT_EXIT["tp1"],tp2_pct=PUT_EXIT["tp2"],sl_pct=PUT_EXIT["sl"],option_horizon_h=PUT_EXIT["hold_h"],spread_pct=2.0) if ps else []
    csim = simulate_signal_set(cs,k5,sigma=sigma,expiry_hours=168.0,tp1_pct=CALL_EXIT["tp1"],tp2_pct=CALL_EXIT["tp2"],sl_pct=CALL_EXIT["sl"],option_horizon_h=CALL_EXIT["hold_h"],spread_pct=2.0) if cs else []
    cb = apply_circuit_breaker(psim+csim, consec_limit=5, pause_bars=576)

    # Bucket by month
    monthly = {}
    for s in cb:
        pnl_pct = s["option"].get("pnl_pct")
        if pnl_pct is None: continue
        ts = datetime.fromtimestamp(s["ts_ms"]/1000, tz=timezone.utc)
        m = ts.strftime("%Y-%m")
        spot = s["close"]
        side = s["side"]
        strike = round(spot / 25) * 25

        # Estimate entry premium at given sigma
        # For 96h hold, effective T = 96/8760 = 0.011 years
        T = 96 / (24 * 365)
        premium = bs_price(side, spot, strike, T, sigma)

        # Entry credit after spread (we sell at bid)
        entry_credit = premium * 0.99  # 1% half-spread

        # Margin per lot
        margin_per_lot = (IM_RATE * strike + premium) * LOT_SIZE
        budget = START_EQUITY * MARGIN_PCT_PER_TRADE
        n_lots = max(0, int(budget // margin_per_lot)) if margin_per_lot > 0 else 0
        if n_lots < 1:
            continue  # can't afford even 1 lot

        contracts = n_lots * LOT_SIZE
        notional = strike * contracts
        taker_fee = min(notional * 0.0003, entry_credit * contracts * 0.125)

        # P&L in $
        pnl_per_contract_pct = pnl_pct / 100.0
        pnl_per_contract = entry_credit * pnl_per_contract_pct
        pnl_usd = pnl_per_contract * contracts - taker_fee * 2  # entry + exit fee

        monthly.setdefault(m, []).append({
            "pnl_pct": pnl_pct,
            "pnl_usd": pnl_usd,
            "side": side,
            "premium": premium,
            "lots": n_lots,
            "fee": taker_fee * 2,
        })

    out = {}
    for m in sorted(monthly):
        trades = monthly[m]
        total_usd = sum(t["pnl_usd"] for t in trades)
        wins = sum(1 for t in trades if t["pnl_usd"] > 0)
        avg_prem = statistics.mean(t["premium"] for t in trades)
        avg_lots = statistics.mean(t["lots"] for t in trades)
        total_fee = sum(t["fee"] for t in trades)
        out[m] = {
            "n": len(trades),
            "wins": wins,
            "wr": wins / len(trades),
            "total_usd": round(total_usd, 2),
            "avg_pnl_usd": round(total_usd / len(trades), 2),
            "avg_prem": round(avg_prem, 2),
            "avg_lots": round(avg_lots, 1),
            "total_fee": round(total_fee, 2),
            "net_after_fee": round(total_usd - total_fee, 2),
        }
    return out, cb

t0 = time.time()
data_dir = find_data_dir(None)
k5,k15,k1h = load_local(data_dir)

print("=" * 80)
print(f"MONTHLY $ ESTIMATE — $400 deposit, realistic sizing")
print("=" * 80)
print(f"Margin per trade: 15% of equity = ${START_EQUITY * MARGIN_PCT_PER_TRADE:.0f}")
print(f"Max portfolio margin: {MAX_PORTFOLIO_MARGIN*100}% = ${START_EQUITY * MAX_PORTFOLIO_MARGIN:.0f}")
print(f"Min lot: {LOT_SIZE} ETH, IM rate: {IM_RATE*100}%")
print()

for sigma, label in [(0.60, "Backtest σ=0.60"), (0.40, "Real IV σ=0.40")]:
    monthly, all_cb = estimate_monthly(k5, k15, k1h, sigma=sigma)
    print(f"\n--- {label} ---")
    print(f"{'Month':<10} {'n':>4} {'WR':>6} {'total_$':>9} {'avg_$':>8} {'fee_$':>8} {'net_$':>8} {'prem_$':>7} {'lots':>5}")
    print("-" * 75)
    total_all = 0
    fee_all = 0
    for m, st in monthly.items():
        print(f"  {m}: {st['n']:>4} {st['wr']*100:>5.1f}% ${st['total_usd']:>+8.2f} ${st['avg_pnl_usd']:>+7.2f} ${st['total_fee']:>7.2f} ${st['net_after_fee']:>+7.2f} ${st['avg_prem']:>6.1f} {st['avg_lots']:>4.1f}")
        total_all += st["total_usd"]
        fee_all += st["total_fee"]

    n_months = len(monthly)
    neg_months = sum(1 for st in monthly.values() if st["total_usd"] < 0)
    avg_monthly = total_all / max(n_months, 1)
    avg_monthly_net = (total_all - fee_all) / max(n_months, 1)

    print(f"\n  Total: ${total_all:+.2f} | Fees: ${fee_all:.2f} | Net: ${total_all - fee_all:+.2f}")
    print(f"  Avg/month (gross): ${avg_monthly:+.2f}")
    print(f"  Avg/month (net):   ${avg_monthly_net:+.2f}")
    print(f"  Negative months: {neg_months}/{n_months}")
    print(f"  Trades/month: {sum(st['n'] for st in monthly.values()) / max(n_months, 1):.1f}")

print(f"\n{'='*80}")
print(f"SUMMARY: What to expect per month on $400 deposit")
print(f"{'='*80}")
print(f"  σ=0.60 (optimistic):  ~$XXX/month")
print(f"  σ=0.40 (realistic):   ~$XXX/month")
print(f"  (replace XXX after running)")
print(f"\nDone ({round(time.time()-t0,1)}s)")

"""Estimate realistic $ PnL for Config B + Combined exits on $400 deposit."""
import statistics, sys
sys.path.insert(0, ".")
from services.backtest import simulate_signal_set
from services.backtest_bs import price as bs_price
from services.local_optimizer import find_data_dir, load_local
from services.check_asymmetric_thresholds import generate_signals
from services.strategy_config import PUT_RET_MAX, CALL_RET_MIN
from services.retest_asymmetric_365d import apply_cb

MS_PER_DAY = 86_400_000
START_EQUITY = 400.0
MARGIN_PCT = 0.15
LOT_SIZE = 0.1
IM_RATE = 0.10
FEE_RATE = 0.0003
FEE_CAP = 0.125
SPREAD_HALF = 0.01

def estimate_pnl_dollar(sigs, k5, call_hold=12, call_sl=0.75, put_hold=168):
    """Simulate with real $ sizing."""
    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]
    
    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=0.50, tp2_pct=0.70, sl_pct=1.50, option_horizon_h=put_hold, spread_pct=2.0) if ps else []
    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=0.25, tp2_pct=0.45, sl_pct=call_sl, option_horizon_h=call_hold, spread_pct=2.0) if cs else []
    
    all_sims = apply_cb(psim + csim)
    
    equity = START_EQUITY
    trades = []
    
    for s in sorted(all_sims, key=lambda x: x["ts_ms"]):
        pnl_pct = s["option"].get("pnl_pct")
        if pnl_pct is None:
            continue
        
        spot = s["close"]
        strike = round(spot / 25) * 25
        side = s["side"]
        hold_h = put_hold if side == "P" else call_hold
        T = hold_h / (24 * 365)
        
        premium = bs_price(side, spot, strike, T, 0.6)
        if premium <= 0.01:
            continue
        
        entry_credit = premium * (1 - SPREAD_HALF)
        margin_per_lot = (IM_RATE * strike + premium) * LOT_SIZE
        if margin_per_lot <= 0:
            continue
        
        budget = equity * MARGIN_PCT
        n_lots = max(1, int(budget / margin_per_lot))
        contracts = n_lots * LOT_SIZE
        notional = strike * contracts
        premium_total = entry_credit * contracts
        entry_fee = min(notional * FEE_RATE, abs(premium_total) * FEE_CAP)
        
        pnl_per_contract = entry_credit * (pnl_pct / 100)
        pnl_usd = pnl_per_contract * contracts - entry_fee * 2
        
        equity_before = equity
        equity += pnl_usd
        
        trades.append({
            "ts_ms": s["ts_ms"], "spot": spot, "side": side, "strike": strike,
            "hold_h": hold_h, "premium": premium, "entry_credit": entry_credit,
            "n_lots": n_lots, "contracts": contracts,
            "margin": margin_per_lot * n_lots,
            "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
            "equity_before": equity_before, "equity_after": equity,
        })
    
    return trades, equity


def main():
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_local(data_dir)
    last_ms = k5[-1]["start_ms"]
    
    print("=== $ PnL Estimate: Config B + Combined Exits ===")
    print(f"Deposit: ${START_EQUITY}, Margin/trade: {MARGIN_PCT*100}% = ${START_EQUITY*MARGIN_PCT:.0f}")
    print(f"Put: hold=168h tp1=50%/tp2=70%/sl=150%")
    print(f"Call: hold=12h tp1=25%/tp2=45%/sl=75%")
    
    for cutoff_name, cutoff_ms, label in [
        ("Last 30 days", last_ms - 30*MS_PER_DAY, "1m"),
        ("Last 90 days", last_ms - 90*MS_PER_DAY, "3m"),
        ("Full 365 days", 0, "12m"),
    ]:
        sigs = generate_signals(k5, k15, k1h, cutoff_ms, PUT_RET_MAX, CALL_RET_MIN)
        trades, final_equity = estimate_pnl_dollar(sigs, k5)
        
        pnl_usds = [t["pnl_usd"] for t in trades]
        total_pnl = sum(pnl_usds)
        win = [t for t in trades if t["pnl_usd"] > 0]
        loss = [t for t in trades if t["pnl_usd"] <= 0]
        n_days = (last_ms - cutoff_ms) / MS_PER_DAY if cutoff_ms > 0 else 365
        n_months = n_days / 30
        avg_monthly = total_pnl / max(n_months, 1)
        
        max_dd = 0
        peak = START_EQUITY
        for t in trades:
            peak = max(peak, t["equity_after"])
            dd = (peak - t["equity_after"]) / peak * 100
            max_dd = max(max_dd, dd)
        
        wr = len(win)/len(trades)*100 if trades else 0
        
        print(f"\n{'='*60}")
        print(f"{cutoff_name} ({n_days:.0f}d, {n_months:.1f}mo, {len(trades)} trades)")
        print(f"  WR: {wr:.1f}%  |  Start: ${START_EQUITY:.0f}  →  End: ${final_equity:.0f}")
        print(f"  Total PnL: ${total_pnl:+.2f} ({total_pnl/START_EQUITY*100:+.1f}%)")
        print(f"  Avg/month: ${avg_monthly:+.2f}")
        print(f"  Max DD: {max_dd:.1f}%")
        if win: print(f"  Wins: {len(win)} avg=${statistics.mean([t['pnl_usd'] for t in win]):+.2f}")
        if loss: print(f"  Loss: {len(loss)} avg=${statistics.mean([t['pnl_usd'] for t in loss]):+.2f}")
        
        if trades:
            print(f"\n  Last 5 trades:")
            for t in trades[-5:]:
                print(f"    {t['side']} ${t['strike']:.0f} h={t['hold_h']}h "
                      f"n={t['n_lots']}lots prem=${t['premium']:.1f} "
                      f"pnl={t['pnl_usd']:+.2f}$ eq=${t['equity_before']:.0f}→${t['equity_after']:.0f}")


if __name__ == "__main__":
    main()

"""Full-year replay of the validated strategy.

Runs `sp.C_mtfdown.cd6.range+transition.decay_24h` (primary winner) on the
full 365 days of ETH 5m klines — NO train/test split. Outputs:
  - Every trade (entry/exit ts, side, strike, credit, exit_debit, pnl_pct, hold_h, exit_reason)
  - Monthly performance summary
  - Equity curve assuming fixed $100/trade sizing
  - Drawdown stats

Saves to /tmp/full_year_replay.json (mounted to /root/opt-app/sweep_out).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.backtest import simulate_signal_set
from services.backtest_data import fetch_set
from services.strategy_registry import gen_sell_premium_iv_high


# Validated winning combo (iter 4 + sensitivity confirmed 9/9)
WINNER = {
    "gen_kwargs": {
        "vol_threshold": 0.7,
        "regime_filter": ["range", "transition"],
        "side": "C",
        "adx_max": None,
        "mtf_direction_filter": "down",
        "cooldown_bars": 6,
    },
    "exit": {
        "tp1": 0.30, "tp2": 0.50, "sl": 0.50,
        "hold_h": 24, "tsl_t": 0.0, "tsl_o": 0.0,
    },
    "sigma": 0.6,
    "spread_pct": 2.0,
}


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main():
    days = 365
    print(f"=== Full-year replay: 365d, primary winner ===")
    t0 = time.time()

    print("\n[1] Fetching klines...")
    data = fetch_set("ETHUSDT", days=days, intervals=("5", "15", "60"))
    k5, k15, k1h = data["5"], data["15"], data["60"]
    print(f"  5m={len(k5)}, 15m={len(k15)}, 1h={len(k1h)}")

    print("\n[2] Generating signals (this is the slow step)...")
    signals = gen_sell_premium_iv_high(k5, k15, k1h, **WINNER["gen_kwargs"])
    print(f"  {len(signals)} signals generated")

    print("\n[3] Simulating on full year (no split)...")
    ex = WINNER["exit"]
    sims = simulate_signal_set(
        signals, k5,
        sigma=WINNER["sigma"], expiry_hours=168.0,
        tp1_pct=ex["tp1"], tp2_pct=ex["tp2"], sl_pct=ex["sl"],
        option_horizon_h=ex["hold_h"], spread_pct=WINNER["spread_pct"],
        tsl_trigger_pct=ex["tsl_t"], tsl_offset_pct=ex["tsl_o"],
    )

    # Extract trades
    trades = []
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt:
            continue
        trades.append({
            "entry_ts": s["ts_ms"],
            "entry_dt": fmt_ts(s["ts_ms"]),
            "entry_price": s["close"],
            "side": s["side"],
            "signal_type": s.get("signal_type"),
            "strike": opt.get("strike"),
            "entry_credit_pct": opt.get("entry_credit_pct"),
            "exit_debit_pct": opt.get("exit_debit_pct"),
            "pnl_pct": opt["pnl_pct"],
            "exit_reason": opt.get("exit_reason"),
            "hold_h": opt.get("hold_h"),
            "exit_ts": opt.get("exit_ts"),
            "exit_dt": fmt_ts(opt["exit_ts"]) if opt.get("exit_ts") else None,
        })
    trades.sort(key=lambda t: t["entry_ts"])

    # Stats
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    print(f"\n=== TRADES: {len(trades)} ===")
    print(f"  Win rate: {len(wins)/len(pnls)*100:.1f}%")
    print(f"  Avg P&L per trade: {statistics.mean(pnls):+.2f}%")
    print(f"  Median: {statistics.median(pnls):+.2f}%")
    print(f"  Best: {max(pnls):+.2f}%")
    print(f"  Worst: {min(pnls):+.2f}%")
    print(f"  Stdev: {statistics.stdev(pnls):.2f}%")
    print(f"  Sharpe: {statistics.mean(pnls)/statistics.stdev(pnls):.3f}")
    print(f"  Total compounded ret (sum): {sum(pnls):+.1f}%")
    print(f"  Avg win: +{statistics.mean(wins):.2f}%  |  Avg loss: {statistics.mean(losses):+.2f}%")

    # Equity curve — fixed $100 per trade
    SIZE = 100.0
    equity = SIZE * 10  # start at $1000
    peak = equity
    max_dd = 0.0
    monthly = {}  # YYYY-MM -> {pnl_sum, n_trades, wins}
    for t in trades:
        pnl_dollars = (t["pnl_pct"] / 100.0) * SIZE
        equity += pnl_dollars
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        max_dd = max(max_dd, dd)
        t["equity_after"] = round(equity, 2)
        ym = t["entry_dt"][:7]
        m = monthly.setdefault(ym, {"pnl_sum": 0.0, "n": 0, "wins": 0})
        m["pnl_sum"] += t["pnl_pct"]
        m["n"] += 1
        if t["pnl_pct"] > 0:
            m["wins"] += 1

    print(f"\n=== EQUITY (start $1000, fixed $100 per trade) ===")
    print(f"  Final equity: ${equity:.2f}  (return: {(equity/1000-1)*100:+.1f}%)")
    print(f"  Peak equity: ${peak:.2f}")
    print(f"  Max drawdown: {max_dd:.1f}%")

    print(f"\n=== MONTHLY BREAKDOWN ===")
    print(f"{'Month':<8} {'n':>4} {'WR':>6} {'sum_pnl':>9} {'avg':>7}")
    for ym in sorted(monthly):
        m = monthly[ym]
        wr = m["wins"] / m["n"] * 100 if m["n"] else 0
        avg = m["pnl_sum"] / m["n"] if m["n"] else 0
        print(f"{ym:<8} {m['n']:>4} {wr:>5.1f}% {m['pnl_sum']:+8.1f}% {avg:+6.2f}%")

    elapsed = round(time.time() - t0, 1)
    out_path = "/tmp/full_year_replay.json"
    with open(out_path, "w") as f:
        json.dump({
            "winner_spec": WINNER,
            "summary": {
                "n_trades": len(trades),
                "win_rate": round(len(wins) / len(pnls), 3),
                "avg_pnl_pct": round(statistics.mean(pnls), 2),
                "median_pnl_pct": round(statistics.median(pnls), 2),
                "stdev_pnl_pct": round(statistics.stdev(pnls), 2),
                "sharpe_per_trade": round(statistics.mean(pnls) / statistics.stdev(pnls), 3),
                "best_pnl_pct": round(max(pnls), 2),
                "worst_pnl_pct": round(min(pnls), 2),
                "total_summed_pnl_pct": round(sum(pnls), 1),
                "starting_equity": 1000.0,
                "final_equity": round(equity, 2),
                "max_drawdown_pct": round(max_dd, 1),
            },
            "monthly": {ym: {"n": m["n"], "wr": round(m["wins"] / m["n"], 3),
                              "avg_pnl_pct": round(m["pnl_sum"] / m["n"], 2),
                              "sum_pnl_pct": round(m["pnl_sum"], 1)}
                        for ym, m in monthly.items()},
            "trades": trades,
            "elapsed_s": elapsed,
        }, f, indent=2)
    print(f"\nSaved → {out_path} ({len(trades)} trades, {elapsed}s)")


if __name__ == "__main__":
    main()

"""Compare baseline winner vs 3 incremental improvements.

Variants:
  A) baseline           — current winner config, no extras
  B) +bull_filter       — skip when EMA50_1h/EMA200_1h > 1.05
  C) +bull+CB+dyn_size  — bull filter + post-hoc consecutive-loss circuit
                           breaker (3 losses → 24h pause) + dynamic position
                           sizing (halve if last-10 WR < 0.40)

We measure each on full 365d (no train/test split) and report:
  avg P&L per trade, sharpe per trade, total return, max drawdown.

If any variant strictly improves drawdown without halving total return, use it.
Otherwise ship baseline with a drawdown warning.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.backtest import simulate_signal_set
from services.backtest_data import fetch_set
from services.strategy_registry import gen_sell_premium_iv_high


WINNER_BASE = {
    "vol_threshold": 0.7,
    "regime_filter": ["range", "transition"],
    "side": "C",
    "adx_max": None,
    "mtf_direction_filter": "down",
    "cooldown_bars": 6,
}
EXIT = {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24, "tsl_t": 0.0, "tsl_o": 0.0}
SIGMA = 0.6
SPREAD = 2.0
START_EQUITY = 1000.0
BASE_SIZE = 100.0  # 10% of equity per trade


def equity_curve(trades: list[dict], *, apply_cb: bool, apply_dyn_sizing: bool) -> dict:
    """Apply post-hoc filters and compute equity curve.

    Trades come pre-sorted by entry_ts (we'll sort to be safe).
    apply_cb: drop trades that fire while in a 24h cooldown triggered by 3
              consecutive losses.
    apply_dyn_sizing: scale position size to 0.5x when last-10 WR < 0.40.
    """
    sorted_trades = sorted(trades, key=lambda t: t["ts_ms"])
    equity = START_EQUITY
    peak = equity
    max_dd_pct = 0.0
    kept = []
    recent_pnls: list[float] = []
    cooldown_until_ts = 0
    consec_losses = 0

    for t in sorted_trades:
        # Consecutive-loss CB
        if apply_cb and t["ts_ms"] < cooldown_until_ts:
            continue

        # Dynamic sizing
        size = BASE_SIZE
        if apply_dyn_sizing and len(recent_pnls) >= 10:
            recent_wr = sum(1 for p in recent_pnls[-10:] if p > 0) / 10.0
            if recent_wr < 0.40:
                size = BASE_SIZE * 0.5

        pnl_dollars = (t["pnl_pct"] / 100.0) * size
        equity += pnl_dollars
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100
        max_dd_pct = max(max_dd_pct, dd)

        recent_pnls.append(t["pnl_pct"])
        # Update consecutive loss counter for CB trigger
        if apply_cb:
            if t["pnl_pct"] <= 0:
                consec_losses += 1
                if consec_losses >= 3:
                    cooldown_until_ts = t["ts_ms"] + 24 * 60 * 60 * 1000
                    consec_losses = 0
            else:
                consec_losses = 0

        kept.append({**t, "size": size, "equity_after": round(equity, 2)})

    pnls = [t["pnl_pct"] for t in kept]
    if not pnls:
        return {"n": 0, "wr": 0, "avg": 0, "stdev": 0, "sharpe": 0,
                "total_pnl_pct": 0, "final_equity": equity,
                "max_dd_pct": max_dd_pct, "return_pct": 0}
    stdev = statistics.stdev(pnls) if len(pnls) > 1 else 0
    return {
        "n": len(pnls),
        "wr": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
        "avg": round(statistics.mean(pnls), 2),
        "stdev": round(stdev, 2),
        "sharpe": round(statistics.mean(pnls) / stdev, 3) if stdev > 0 else 0,
        "total_pnl_pct": round(sum(pnls), 1),
        "final_equity": round(equity, 2),
        "max_dd_pct": round(max_dd_pct, 1),
        "return_pct": round((equity / START_EQUITY - 1) * 100, 1),
    }


def run_variant(label: str, k5, k15, k1h, gen_overrides: dict, *,
                apply_cb: bool, apply_dyn_sizing: bool) -> dict:
    cfg = {**WINNER_BASE, **gen_overrides}
    t0 = time.time()
    signals = gen_sell_premium_iv_high(k5, k15, k1h, **cfg)
    elapsed_gen = time.time() - t0

    sims = simulate_signal_set(
        signals, k5,
        sigma=SIGMA, expiry_hours=168.0,
        tp1_pct=EXIT["tp1"], tp2_pct=EXIT["tp2"], sl_pct=EXIT["sl"],
        option_horizon_h=EXIT["hold_h"], spread_pct=SPREAD,
        tsl_trigger_pct=EXIT["tsl_t"], tsl_offset_pct=EXIT["tsl_o"],
    )
    trades = []
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt:
            continue
        trades.append({
            "ts_ms": s["ts_ms"],
            "side": s["side"],
            "pnl_pct": opt["pnl_pct"],
            "exit_reason": opt.get("exit_reason"),
        })

    stats = equity_curve(trades, apply_cb=apply_cb, apply_dyn_sizing=apply_dyn_sizing)
    elapsed = round(time.time() - t0, 1)
    print(f"\n=== {label} ===")
    print(f"  signals_generated: {len(signals)}  trades_after_filter: {stats['n']}")
    print(f"  win_rate:    {stats['wr']*100:.1f}%")
    print(f"  avg_per_tr:  {stats['avg']:+.2f}%")
    print(f"  stdev:       {stats['stdev']:.2f}%")
    print(f"  sharpe:      {stats['sharpe']:+.3f}")
    print(f"  total_pnl:   {stats['total_pnl_pct']:+.1f}%")
    print(f"  final_eq:    ${stats['final_equity']:.2f}  (return {stats['return_pct']:+.1f}%)")
    print(f"  max_dd:      {stats['max_dd_pct']:.1f}%")
    print(f"  [signal_gen took {elapsed_gen:.1f}s, total {elapsed}s]")
    return {"label": label, "stats": stats, "n_signals": len(signals)}


def main():
    print("=== Improvements comparison: baseline vs filters ===")
    t0 = time.time()

    print("\n[1] Fetching klines (365d)...")
    data = fetch_set("ETHUSDT", days=365, intervals=("5", "15", "60"))
    k5, k15, k1h = data["5"], data["15"], data["60"]
    print(f"  5m={len(k5)}, 15m={len(k15)}, 1h={len(k1h)}")

    out = []

    # A) Baseline — current winner
    out.append(run_variant(
        "A_baseline",
        k5, k15, k1h, gen_overrides={},
        apply_cb=False, apply_dyn_sizing=False,
    ))

    # B) +bull_filter only
    out.append(run_variant(
        "B_bull_filter",
        k5, k15, k1h, gen_overrides={"bull_market_ratio_max": 1.05},
        apply_cb=False, apply_dyn_sizing=False,
    ))

    # C) +bull_filter + CB + dynamic sizing
    out.append(run_variant(
        "C_bull_cb_dynsize",
        k5, k15, k1h, gen_overrides={"bull_market_ratio_max": 1.05},
        apply_cb=True, apply_dyn_sizing=True,
    ))

    # D) just CB + dynsize, no bull filter (isolate effect)
    out.append(run_variant(
        "D_cb_dynsize_only",
        k5, k15, k1h, gen_overrides={},
        apply_cb=True, apply_dyn_sizing=True,
    ))

    print(f"\n=== SUMMARY ===")
    print(f"{'variant':<22} {'n':>5} {'WR':>6} {'avg':>7} {'sharpe':>7} {'return':>9} {'max_dd':>8}")
    for r in out:
        s = r["stats"]
        print(f"  {r['label']:<20} {s['n']:>5} {s['wr']*100:>5.1f}% {s['avg']:>+6.2f}% {s['sharpe']:>+6.3f} {s['return_pct']:>+8.1f}% {s['max_dd_pct']:>7.1f}%")

    elapsed = round(time.time() - t0, 1)
    print(f"\nTotal: {elapsed}s")
    with open("/tmp/improvements_compare.json", "w") as f:
        json.dump({"variants": out, "elapsed_s": elapsed,
                   "start_equity": START_EQUITY, "base_size": BASE_SIZE,
                   "sigma": SIGMA, "spread_pct": SPREAD}, f, indent=2)


if __name__ == "__main__":
    main()

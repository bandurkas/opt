"""Combined fix: min-lot floor + staggered topup, vs $800-flat baseline,
across all 12 start anchors. Validates the production fix recommendation
for paper_strategy.py's realistic_size_lots() (same margin-starvation bug
confirmed live, not just in the backtest harness).

Run: cd backend && PYTHONPATH=. python3 services/eth_combined_fix_test.py
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from multiprocessing import cpu_count
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.option_futures_complement import gen_parallel
from services.strategy_config import CALL_GEN_KWARGS, PUT_GEN_KWARGS
from services.iv_mixed_deposit import (
    build_trades, PUT_96, CALL_24, MARGIN_PCT, IM_RATE, LOT, MAX_OPEN,
    PORT_MARGIN_CAP, CB_LOSSES, CB_COOLDOWN_MS, fee,
)
from services.eth_dollar_sl_4y_sweep import build_trades_dollar_sl

ANCHORS = ["2022-07", "2022-10", "2023-01", "2023-04", "2023-07", "2023-10",
          "2024-01", "2024-04", "2024-07", "2024-10", "2025-01", "2025-04"]
DAY_MS = 86_400_000
ABS_FLOOR_EQUITY = 50.0


def to_ts_ms(month_str):
    y, m = (int(x) for x in month_str.split("-"))
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def run_engine_full(trades, start_capital, topups, min_lot_floor):
    trades = sorted(trades, key=lambda t: t["ts"])
    if not trades:
        return 0, start_capital, 0.0
    t0 = trades[0]["ts"]
    pending = sorted([(t0 + d * DAY_MS, a) for d, a in topups], key=lambda x: x[0])
    equity = start_capital
    peak = equity
    max_dd = 0.0
    open_pos = []
    recent = []
    consec = 0
    cb_until = 0
    n_taken = n_rescue = 0

    def apply_topups(now_ts):
        nonlocal equity, peak, pending
        still = []
        for ts, amt in pending:
            if ts <= now_ts:
                equity += amt
                peak += amt
            else:
                still.append((ts, amt))
        pending = still

    def realize(now_ts):
        nonlocal equity, peak, max_dd, consec, cb_until
        still = []
        for p in sorted(open_pos, key=lambda x: x["exit_ts"]):
            if p["exit_ts"] <= now_ts:
                equity += p["pnl_dollars"]
                recent.append(p["pnl_pct"])
                if p["pnl_pct"] > 0:
                    consec = 0
                else:
                    consec += 1
                    if consec >= CB_LOSSES:
                        cb_until = p["exit_ts"] + CB_COOLDOWN_MS
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
            else:
                still.append(p)
        open_pos[:] = still

    for t in trades:
        apply_topups(t["ts"])
        realize(t["ts"])
        if t["ts"] < cb_until:
            continue
        if len(open_pos) >= MAX_OPEN:
            continue
        if equity < ABS_FLOOR_EQUITY:
            continue
        used = sum(p["margin"] for p in open_pos)
        free = max(0.0, equity * PORT_MARGIN_CAP - used)
        dyn = 0.5 if (len(recent) >= 10 and sum(1 for x in recent[-10:] if x > 0) / 10 < 0.40) else 1.0
        budget = min(equity * MARGIN_PCT * dyn, free)
        m_per_lot = (IM_RATE * t["strike"] + t["mid"]) * LOT
        n_lots = int(budget // m_per_lot) if m_per_lot > 0 else 0
        if n_lots < 1:
            if min_lot_floor and m_per_lot <= free:
                n_lots = 1
                n_rescue += 1
            else:
                continue
        qty = n_lots * LOT
        credit_total = t["credit"] * qty
        gross = credit_total * t["pnl_pct"]
        fees = 2 * fee(t["strike"] * qty, credit_total)
        open_pos.append({"exit_ts": t["exit_ts"], "margin": m_per_lot * n_lots,
                         "pnl_dollars": gross - fees, "pnl_pct": t["pnl_pct"]})
        n_taken += 1
    if open_pos:
        realize(max(p["exit_ts"] for p in open_pos) + 1)
    return n_taken, equity, max_dd


def main():
    ncore = cpu_count()
    print(f"[1] klines (4y) + parallel gen ({ncore} cores)...")
    k5, k15, k1h = load_coin("eth_long", find_data_dir(None))
    k1h = sorted(k1h, key=lambda c: c["start_ms"])
    calls = gen_parallel(k5, k15, k1h, CALL_GEN_KWARGS, ncore)
    puts = gen_parallel(k5, k15, k1h, PUT_GEN_KWARGS, ncore)
    put_live = build_trades(puts, k5, k1h, PUT_96)
    call_dollar10 = build_trades_dollar_sl(calls, k5, k1h, sl_dollar_frac=0.10)
    trades_all = call_dollar10 + put_live
    last_ts = max(t["ts"] for t in trades_all)

    scenarios = {
        "$800 flat, NO floor":         (800.0, [], False),
        "$800 flat, +floor":           (800.0, [], True),
        "$600+100@14+100@21, +floor":  (600.0, [(14, 100.0), (21, 100.0)], True),
    }
    print("\n---------- combined fix comparison (DEPLOYED $-SL=0.10 + live PUT) ----------")
    header = f"{'anchor':<9}" + "".join(f"{name:>32}" for name in scenarios)
    print(header)
    for anchor in ANCHORS:
        start_ts = to_ts_ms(anchor)
        if start_ts >= last_ts:
            break
        sub = [t for t in trades_all if t["ts"] >= start_ts]
        if not sub:
            continue
        row = f"{anchor:<9}"
        for name, (cap, topups, floor) in scenarios.items():
            n_taken, equity, max_dd = run_engine_full(sub, cap, topups, floor)
            ret = (equity / cap - 1) * 100
            row += f"  ${equity:>8,.0f}({ret:>+6.1f}%,dd{max_dd*100:>4.0f}%)"
        print(row)


if __name__ == "__main__":
    main()

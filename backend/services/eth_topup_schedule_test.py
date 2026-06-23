"""Does a $600 start + scheduled top-up to $800 over 2-3 weeks behave like a
$800 start, or does it inherit the $400/$600 account's early-drawdown risk
before the top-up lands?

Engine is iv_mixed_deposit.run_engine, unchanged, except START and a deposit
schedule (cash injected into equity AND peak at specific day-offsets from the
anchor date — added to peak too, since it's new capital, not unrealized
profit recovering a prior drawdown).

Run:
    cd backend && PYTHONPATH=. python3 services/eth_topup_schedule_test.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from multiprocessing import cpu_count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir              # noqa: E402
from services.multi_coin_signals import load_coin                # noqa: E402
from services.option_futures_complement import gen_parallel      # noqa: E402
from services.strategy_config import CALL_GEN_KWARGS, PUT_GEN_KWARGS  # noqa: E402
from services.iv_mixed_deposit import (                          # noqa: E402
    build_trades, PUT_96, CALL_24, MARGIN_PCT, IM_RATE, LOT, MAX_OPEN,
    PORT_MARGIN_CAP, CB_LOSSES, CB_COOLDOWN_MS, fee,
)
from services.eth_dollar_sl_4y_sweep import build_trades_dollar_sl  # noqa: E402

ANCHORS = ["2022-07", "2022-10", "2023-01", "2023-04", "2023-07", "2023-10",
          "2024-01", "2024-04", "2024-07", "2024-10", "2025-01", "2025-04"]

DAY_MS = 86_400_000


def to_ts_ms(month_str: str) -> int:
    y, m = (int(x) for x in month_str.split("-"))
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def run_engine_with_topups(trades, start_capital: float, topups: list[tuple[int, float]]):
    """topups: list of (day_offset_from_first_trade, amount_usd)."""
    trades = sorted(trades, key=lambda t: t["ts"])
    if not trades:
        return 0, start_capital, 0.0
    t0 = trades[0]["ts"]
    pending_topups = sorted([(t0 + days * DAY_MS, amt) for days, amt in topups], key=lambda x: x[0])

    equity = start_capital
    peak = equity
    max_dd = 0.0
    open_pos = []
    recent = []
    consec = 0
    cb_until = 0
    n_taken = n_cap = n_margin = n_cb = 0

    def apply_due_topups(now_ts):
        nonlocal equity, peak, pending_topups
        still = []
        for ts, amt in pending_topups:
            if ts <= now_ts:
                equity += amt
                peak += amt  # new capital, not recovered drawdown — raises the bar too
            else:
                still.append((ts, amt))
        pending_topups = still

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
        apply_due_topups(t["ts"])
        realize(t["ts"])
        if t["ts"] < cb_until:
            n_cb += 1
            continue
        if len(open_pos) >= MAX_OPEN:
            n_cap += 1
            continue
        used = sum(p["margin"] for p in open_pos)
        free = max(0.0, equity * PORT_MARGIN_CAP - used)
        dyn = 0.5 if (len(recent) >= 10 and sum(1 for x in recent[-10:] if x > 0) / 10 < 0.40) else 1.0
        budget = min(equity * MARGIN_PCT * dyn, free)
        m_per_lot = (IM_RATE * t["strike"] + t["mid"]) * LOT
        n_lots = int(budget // m_per_lot) if m_per_lot > 0 else 0
        if n_lots < 1:
            n_margin += 1
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
        "$400 flat (no topup)": (400.0, []),
        "$800 flat from start": (800.0, []),
        "$600 + $100@d14 + $100@d21": (600.0, [(14, 100.0), (21, 100.0)]),
        "$600 + $200@d7 (single, end of wk1)": (600.0, [(7, 200.0)]),
    }

    print("\n---------- DEPLOYED $-SL=0.10 + live PUT, capital schedule comparison ----------")
    header = f"{'anchor':<9}" + "".join(f"{name:>34}" for name in scenarios)
    print(header)
    for anchor in ANCHORS:
        start_ts = to_ts_ms(anchor)
        if start_ts >= last_ts:
            break
        sub = [t for t in trades_all if t["ts"] >= start_ts]
        if not sub:
            continue
        row = f"{anchor:<9}"
        for name, (cap, topups) in scenarios.items():
            n_taken, equity, max_dd = run_engine_with_topups(sub, cap, topups)
            ret = (equity / cap - 1) * 100
            row += f"  ${equity:>8,.0f}({ret:>+6.1f}%,dd{max_dd*100:>4.0f}%)"
        print(row)


if __name__ == "__main__":
    main()

"""Capital-aware sizing test for Sniper1's $400 margin-constrained engine.

eth_dollar_sl_start_point_sweep.py showed the account is bimodal: it either
avoids an early drawdown and compounds nicely (taken=900-1400, FINAL +145%
to +293%), or takes one early hit, then n_margin balloons (3000-5300 blocked
signals) while taken collapses to <500 — the % equity in run_engine.budget
falls below what one lot costs and the account is structurally LOCKED OUT,
since budget = equity * MARGIN_PCT scales down with the very equity it can
no longer recover. Since per-trade avg is robustly positive in every regime
(confirmed separately), the lockout itself — not the trades — is the loss.

This tests ONE capital-aware fix: guarantee a MINIMUM 1-lot floor whenever
the portfolio margin cap (PORT_MARGIN_CAP) still has room, even if
equity*MARGIN_PCT*dyn alone couldn't afford it — i.e. let a drawn-down
account keep attempting trades (where the edge is positive) instead of
mathematically asphyxiating itself, as long as it's not already near zero
(ABS_FLOOR_EQUITY) and not in a circuit-breaker cooldown.

Run:
    cd backend && PYTHONPATH=. python3 services/eth_capital_aware_sizing_test.py
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
    build_trades, run_engine, PUT_96, CALL_24,
    START, MARGIN_PCT, IM_RATE, LOT, MAX_OPEN, PORT_MARGIN_CAP,
    CB_LOSSES, CB_COOLDOWN_MS, fee,
)
from services.eth_dollar_sl_4y_sweep import build_trades_dollar_sl  # noqa: E402

ANCHORS = ["2022-07", "2022-10", "2023-01", "2023-04", "2023-07", "2023-10",
          "2024-01", "2024-04", "2024-07", "2024-10", "2025-01", "2025-04"]

ABS_FLOOR_EQUITY = 50.0  # below this, treat the account as dead — no min-lot rescue


def to_ts_ms(month_str: str) -> int:
    y, m = (int(x) for x in month_str.split("-"))
    return int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)


def run_engine_capital_aware(trades, label, *, min_lot_floor: bool):
    """Same engine as iv_mixed_deposit.run_engine, +1 change: when min_lot_floor
    is set, a position that can't afford its normal %-of-equity budget is still
    opened at exactly 1 lot if the PORTFOLIO margin cap has room and equity is
    above ABS_FLOOR_EQUITY — i.e. never let %-of-equity sizing alone lock the
    account out of an edge that stays positive at every equity level."""
    trades = sorted(trades, key=lambda t: t["ts"])
    equity = START
    peak = equity
    max_dd = 0.0
    open_pos = []
    recent = []
    taken_ts = []
    consec = 0
    cb_until = 0
    n_taken = n_cap = n_margin = n_cb = n_floor_rescue = 0

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
        realize(t["ts"])
        if t["ts"] < cb_until:
            n_cb += 1
            continue
        if len(open_pos) >= MAX_OPEN:
            n_cap += 1
            continue
        if equity < ABS_FLOOR_EQUITY:
            n_margin += 1
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
                n_floor_rescue += 1
            else:
                n_margin += 1
                continue
        qty = n_lots * LOT
        credit_total = t["credit"] * qty
        gross = credit_total * t["pnl_pct"]
        fees = 2 * fee(t["strike"] * qty, credit_total)
        open_pos.append({"exit_ts": t["exit_ts"], "margin": m_per_lot * n_lots,
                         "pnl_dollars": gross - fees, "pnl_pct": t["pnl_pct"]})
        taken_ts.append(t["ts"])
        n_taken += 1
    if open_pos:
        realize(max(p["exit_ts"] for p in open_pos) + 1)
    ret = (equity - START) / START * 100
    print(f"\n{label}")
    print(f"  signals={len(trades)}  taken={n_taken} (floor_rescue={n_floor_rescue})  "
          f"blocked: cap={n_cap} margin={n_margin} cb={n_cb}")
    print(f"  START $400 -> FINAL ${equity:,.2f}  ({ret:+.1f}%)  maxDD {max_dd*100:.1f}%")
    return n_taken, equity


def main():
    ncore = cpu_count()
    print(f"[1] klines (4y) + parallel gen ({ncore} cores)...")
    k5, k15, k1h = load_coin("eth_long", find_data_dir(None))
    k1h = sorted(k1h, key=lambda c: c["start_ms"])

    calls = gen_parallel(k5, k15, k1h, CALL_GEN_KWARGS, ncore)
    puts = gen_parallel(k5, k15, k1h, PUT_GEN_KWARGS, ncore)

    put_live = build_trades(puts, k5, k1h, PUT_96)
    call_live = build_trades(calls, k5, k1h, CALL_24)
    call_dollar10 = build_trades_dollar_sl(calls, k5, k1h, sl_dollar_frac=0.10)

    configs = {
        "LIVE %-SL=0.75":     call_live + put_live,
        "DEPLOYED $-SL=0.10": call_dollar10 + put_live,
    }

    last_ts = max(t["ts"] for trades in configs.values() for t in trades)
    results = []
    for anchor in ANCHORS:
        start_ts = to_ts_ms(anchor)
        if start_ts >= last_ts:
            break
        for label, trades in configs.items():
            sub = [t for t in trades if t["ts"] >= start_ts]
            if not sub:
                continue
            print(f"\n========== [{anchor}] {label} — BASELINE (no floor) ==========")
            n0, eq0, _ = run_engine(sub, f"  baseline")
            print(f"========== [{anchor}] {label} — CAPITAL-AWARE (min-lot floor) ==========")
            n1, eq1 = run_engine_capital_aware(sub, f"  capital-aware", min_lot_floor=True)
            results.append((anchor, label, n0, eq0, n1, eq1))

    print("\n---------- summary: baseline vs capital-aware min-lot floor ----------")
    print(f"{'anchor':<9}{'config':<22}{'base taken':>11}{'base FINAL':>13}"
          f"{'CA taken':>10}{'CA FINAL':>11}{'delta%':>9}")
    for anchor, label, n0, eq0, n1, eq1 in results:
        delta = (eq1 - eq0) / 400 * 100
        print(f"{anchor:<9}{label:<22}{n0:>11}{eq0:>13,.2f}{n1:>10}{eq1:>11,.2f}{delta:>+8.1f}%")


if __name__ == "__main__":
    main()

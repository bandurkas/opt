"""Account-level deposit & MARGIN_PCT sweep using the REALISTIC dollar-margin
stop (btc_straddle_dollar_stop.py), replacing the broken %-of-premium SL whose
gap risk was exposed in btc_straddle_gap_stress.py. Same methodology as
btc_straddle_account_sim.py (deposit knee + leverage sweep) but plugged into
the honest exit rule this time — sl_dollar_frac=2.0 (best by Sharpe).

Run: cd backend && python3 services/btc_straddle_dollar_account_sim.py [coin] [days_back]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.btc_straddle_dollar_stop import (
    build_cycles, simulate_leg_dollar_stop, trailing_sigma, nearest_1h_idx, CYCLE_H, TP2,
)

COIN = sys.argv[1] if len(sys.argv) > 1 else "btc_long"
DAYS_BACK = float(sys.argv[2]) if len(sys.argv) > 2 else 1095.0
SL_DOLLAR_FRAC = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0

MARGIN_PCT = 0.15
PORT_MARGIN_CAP = 0.80
FEE_RATE = 0.0003
FEE_CAP = 0.125
CB_LOSS_CYCLES = 5
CB_COOLDOWN_MS = 48 * 3600 * 1000
DEPOSITS = (400.0, 800.0, 1200.0, 1600.0, 2000.0, 3000.0, 5000.0)


def fee(notional, premium_total):
    return min(notional * FEE_RATE, abs(premium_total) * FEE_CAP)


def build_cycle_legdata(coin, days_back, sl_dollar_frac):
    k5, k1h, cycles_by_idx = build_cycles(coin, days_back)
    out = []
    for cycle_idx, legs in sorted(cycles_by_idx.items()):
        if "C" not in legs or "P" not in legs:
            continue
        idx_1h = nearest_1h_idx(k1h, k5[cycle_idx]["start_ms"])
        if idx_1h is None:
            continue
        sigma = trailing_sigma(k1h, idx_1h)
        if sigma is None:
            continue
        legres = {}
        for side in ("C", "P"):
            r = simulate_leg_dollar_stop(side, legs[side], k5, sigma, CYCLE_H, TP2, sl_dollar_frac)
            if r is None:
                legres = None
                break
            legres[side] = r
        if not legres:
            continue
        out.append({"ts": k5[cycle_idx]["start_ms"], "legs": legres})
    return out


def run_account(cycles, start, margin_pct, compounding=True):
    equity = start
    peak = equity
    max_dd = 0.0
    consec_losing_cycles = 0
    cb_until = 0
    n_taken = n_blocked_cap = n_blocked_margin = n_blocked_cb = 0
    monthly: dict[str, float] = {}

    for cyc in cycles:
        ts = cyc["ts"]
        if ts < cb_until:
            n_blocked_cb += 1
            continue
        if equity <= 0:
            break

        size_base = equity if compounding else start
        budget = min(size_base * margin_pct, size_base * PORT_MARGIN_CAP)
        half_budget = budget / 2.0

        legs_pnl = 0.0
        legs_taken = 0
        for side in ("C", "P"):
            leg = cyc["legs"][side]
            m_per_lot = leg["margin"]            # already $ for LOT=0.01
            pnl_per_lot = leg["pnl_dollars"]
            if m_per_lot <= 0:
                continue
            n_lots = int(half_budget // m_per_lot)
            if n_lots < 1:
                n_blocked_margin += 1
                continue
            notional_per_lot_strike = m_per_lot  # rough fee base proxy; fee impact is small either way
            gross = pnl_per_lot * n_lots
            fees = 2 * fee(m_per_lot * n_lots * 5, abs(pnl_per_lot) * n_lots)  # conservative fee proxy
            legs_pnl += gross - fees
            legs_taken += 1

        if legs_taken == 0:
            n_blocked_cap += 1
            continue

        equity += legs_pnl
        n_taken += 1
        if legs_pnl > 0:
            consec_losing_cycles = 0
        else:
            consec_losing_cycles += 1
            if consec_losing_cycles >= CB_LOSS_CYCLES:
                cb_until = ts + CB_COOLDOWN_MS

        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)
        mk = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m")
        monthly[mk] = monthly.get(mk, 0.0) + legs_pnl

    days = (cycles[-1]["ts"] - cycles[0]["ts"]) / 86_400_000 if cycles else 0
    months = max(1.0, days / 30.4)
    tot_ret = (equity - start) / start * 100 if start else 0.0
    cagr_monthly_pct = (((equity / start) ** (1 / months) - 1) * 100) if equity > 0 and start > 0 else -100.0

    return {
        "start": start, "final": equity, "tot_ret_pct": tot_ret, "max_dd_pct": max_dd * 100,
        "n_cycles": len(cycles), "n_taken": n_taken, "blocked_margin": n_blocked_margin,
        "blocked_cb": n_blocked_cb, "months": months, "monthly_pct_cagr": cagr_monthly_pct,
        "monthly": monthly,
    }


def main():
    print(f"loading {COIN}, last {DAYS_BACK:.0f}d, dollar-margin stop "
          f"(sl_dollar_frac={SL_DOLLAR_FRAC})...\n")
    cycles = build_cycle_legdata(COIN, DAYS_BACK, SL_DOLLAR_FRAC)
    print(f"{len(cycles)} complete cycles\n")

    print("=== DEPOSIT sweep (MARGIN_PCT=0.15) ===")
    print(f"{'deposit':>9} {'final':>11} {'totRet':>9} {'maxDD':>7} {'mo% CAGR':>9} "
          f"{'taken':>7} {'blk_margin':>10} {'blk_cb':>7}")
    for dep in DEPOSITS:
        r = run_account(cycles, dep, MARGIN_PCT)
        print(f"${dep:>7.0f} ${r['final']:>9,.0f} {r['tot_ret_pct']:>+8.1f}% {r['max_dd_pct']:>6.1f}% "
              f"{r['monthly_pct_cagr']:>+8.2f}% {r['n_taken']:>7}/{r['n_cycles']:<4} "
              f"{r['blocked_margin']:>10} {r['blocked_cb']:>7}")

    print(f"\n=== MARGIN_PCT sweep at $2000 deposit ===")
    print(f"{'margin_pct':>10} {'final':>11} {'totRet':>9} {'maxDD':>7} {'mo% CAGR':>9} {'taken':>7}")
    for mp in (0.15, 0.25, 0.35, 0.50, 0.70, 1.00):
        r = run_account(cycles, 2000.0, mp)
        print(f"{mp:>10.2f} ${r['final']:>9,.0f} {r['tot_ret_pct']:>+8.1f}% {r['max_dd_pct']:>6.1f}% "
              f"{r['monthly_pct_cagr']:>+8.2f}% {r['n_taken']:>7}/{r['n_cycles']:<4}")

    print(f"\n=== 2022 MONTHLY DETAIL at $2000 deposit, MARGIN_PCT=0.15 and 0.25 ===")
    for mp in (0.15, 0.25):
        r = run_account(cycles, 2000.0, mp)
        print(f"\n  -- MARGIN_PCT={mp} --")
        for mk in sorted(r["monthly"]):
            if mk.startswith("2022"):
                bar = "#" * max(0, int(abs(r["monthly"][mk]) / 20))
                sign = "+" if r["monthly"][mk] >= 0 else "-"
                print(f"    {mk}: {r['monthly'][mk]:>+9.2f}  {bar}")


if __name__ == "__main__":
    main()

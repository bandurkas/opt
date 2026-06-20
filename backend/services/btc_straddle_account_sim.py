"""BTC short-straddle account-level deposit sweep — same methodology as
deposit_sim.py / deposit_curve.py used to find ETH's $800 margin-knee, but
for the unconditional 24h BTC straddle (cycle=24h tp1=0.50 tp2=0.80 sl=0.75
mult=1.10, validated in btc_straddle_sweep.py / btc_straddle_winner_detail.py).

Real Bybit BTC option contract specs (queried live via VPS3, 2026-06-21):
  minOrderQty = qtyStep = 0.01 BTC (vs ETH's 0.1) — confirmed via
  /v5/market/instruments-info?category=option&baseCoin=BTC. Near-term strikes
  step $500 near current spot.

Each 24h cycle opens ONE call + ONE put (the straddle), both closed at cycle
end before the next cycle opens — so MAX_OPEN is naturally 2, not 4. Margin
for the two legs is summed independently (conservative — real Bybit portfolio
margin would net some of this via delta offset, so this likely UNDERSTATES
capital efficiency, not overstates it).

Run:
  cd backend && python3 services/btc_straddle_account_sim.py [coin] [days_back]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs
from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.btc_straddle_sweep import build_periodic_signals

COIN = sys.argv[1] if len(sys.argv) > 1 else "btc_long"
DAYS_BACK = float(sys.argv[2]) if len(sys.argv) > 2 else 1095.0

CYCLE_H, TP1, TP2, SL, MULT = 24.0, 0.50, 0.80, 0.75, 1.10
SIGMA_CLAMP = (0.20, 1.50)
SPREAD_PCT = 2.0
STRIKE_ROUND = 500.0   # real Bybit near-term BTC strike spacing at current spot

IM_RATE = 0.10
LOT = 0.01              # real Bybit BTC option minOrderQty/qtyStep
MARGIN_PCT = 0.15        # equity fraction budgeted per cycle (both legs combined)
PORT_MARGIN_CAP = 0.80
FEE_RATE = 0.0003
FEE_CAP = 0.125
HALF_SPREAD = 0.01
CB_LOSS_CYCLES = 5        # consecutive losing CYCLES (not legs) -> cooldown
CB_COOLDOWN_MS = 48 * 3600 * 1000
DEPOSITS = (400.0, 800.0, 1200.0, 1600.0, 2000.0, 3000.0, 5000.0)


def fee(notional, premium_total):
    return min(notional * FEE_RATE, abs(premium_total) * FEE_CAP)


def build_cycle_trades(coin, days_back):
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    if days_back:
        cutoff_ms = k5[-1]["start_ms"] - int(days_back * 86_400_000)
        k5 = [c for c in k5 if c["start_ms"] >= cutoff_ms]
        k1h = [c for c in k1h if c["start_ms"] >= cutoff_ms]

    sigs = build_periodic_signals(k5, CYCLE_H)
    out = simulate_signal_set(
        sigs, k5, sigma=0.60, expiry_hours=CYCLE_H, tp1_pct=TP1, tp2_pct=TP2,
        sl_pct=SL, option_horizon_h=CYCLE_H, spread_pct=SPREAD_PCT,
        dynamic_sigma=True, klines_1h=k1h, iv_rv_multiplier=MULT,
        sigma_clamp=SIGMA_CLAMP, strike_round_to=STRIKE_ROUND,
    )

    by_cycle: dict[int, dict] = {}
    for o in out:
        opt = o.get("option", {})
        if "pnl_pct" not in opt or opt.get("resolution") in ("no_entry", "no_data"):
            continue
        strike = round(o["close"] / STRIKE_ROUND) * STRIKE_ROUND
        T0 = CYCLE_H / (24 * 365)
        mid = bs.price(o["side"], o["close"], strike, T0, o["sigma_used"])
        bars = opt.get("bars_held", int(CYCLE_H * 12))
        leg = {"strike": strike, "mid": mid, "pnl_pct": opt["pnl_pct"] / 100.0,
               "exit_ts": int(o["ts_ms"]) + bars * 5 * 60 * 1000}
        by_cycle.setdefault(o["_cycle"], {"ts": int(o["ts_ms"])})[o["side"]] = leg

    cycles = []
    for c, d in sorted(by_cycle.items()):
        if "C" in d and "P" in d and d["C"]["mid"] > 0.01 and d["P"]["mid"] > 0.01:
            cycles.append(d)
    return cycles


def run_account(cycles, start, compounding=True):
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

        size_base = equity if compounding else start
        budget = size_base * MARGIN_PCT
        budget = min(budget, size_base * PORT_MARGIN_CAP)  # no other open positions exist at cycle start
        half_budget = budget / 2.0

        legs_pnl = 0.0
        legs_taken = 0
        for side in ("C", "P"):
            leg = cyc[side]
            m_per_lot = (IM_RATE * leg["strike"] + leg["mid"]) * LOT
            if m_per_lot <= 0:
                continue
            n_lots = int(half_budget // m_per_lot)
            if n_lots < 1:
                n_blocked_margin += 1
                continue
            qty = n_lots * LOT
            credit_total = leg["mid"] * (1 - HALF_SPREAD) * qty
            notional = leg["strike"] * qty
            gross = credit_total * leg["pnl_pct"]
            fees = 2 * fee(notional, credit_total)
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

        if equity <= 0:
            equity = 0.0
            break

    days = (cycles[-1]["ts"] - cycles[0]["ts"]) / 86_400_000 if cycles else 0
    months = max(1.0, days / 30.4)
    tot_ret = (equity - start) / start * 100
    monthly_avg = sum(monthly.values()) / len(monthly) if monthly else 0.0
    monthly_pct_avg = (monthly_avg / start * 100) if start > 0 else 0.0
    cagr_monthly_pct = (((equity / start) ** (1 / months) - 1) * 100) if equity > 0 else -100.0

    return {
        "start": start, "final": equity, "tot_ret_pct": tot_ret, "max_dd_pct": max_dd * 100,
        "n_cycles": len(cycles), "n_taken": n_taken,
        "blocked_cap": n_blocked_cap, "blocked_margin": n_blocked_margin, "blocked_cb": n_blocked_cb,
        "months": months, "monthly_pct_simple_avg": monthly_pct_avg, "monthly_pct_cagr": cagr_monthly_pct,
        "monthly": monthly,
    }


def main():
    print(f"loading {COIN}, last {DAYS_BACK:.0f}d, building 24h straddle cycles "
          f"(tp={TP1}/{TP2} sl={SL} mult={MULT} strike_round=${STRIKE_ROUND:.0f} "
          f"LOT={LOT} BTC)...\n")
    cycles = build_cycle_trades(COIN, DAYS_BACK)
    print(f"{len(cycles)} complete cycles with valid premiums\n")

    print(f"{'deposit':>9} {'final':>11} {'totRet':>9} {'maxDD':>7} "
          f"{'mo% simple':>11} {'mo% CAGR':>9} {'taken':>7} {'blk_margin':>10} {'blk_cb':>7}")
    for dep in DEPOSITS:
        r = run_account(cycles, dep, compounding=True)
        print(f"${dep:>7.0f} ${r['final']:>9,.0f} {r['tot_ret_pct']:>+8.1f}% {r['max_dd_pct']:>6.1f}% "
              f"{r['monthly_pct_simple_avg']:>+10.2f}% {r['monthly_pct_cagr']:>+8.2f}% "
              f"{r['n_taken']:>7}/{r['n_cycles']:<4} {r['blocked_margin']:>10} {r['blocked_cb']:>7}")

    print("\n(monthly% simple avg = mean of monthly $P&L / START deposit; "
          "monthly% CAGR = compound monthly rate implied by total return over the period)")

    print(f"\n=== MARGIN_PCT sweep at $2000 deposit (sizing aggressiveness) ===")
    global MARGIN_PCT
    orig = MARGIN_PCT
    print(f"{'margin_pct':>10} {'final':>11} {'totRet':>9} {'maxDD':>7} "
          f"{'mo% CAGR':>9} {'taken':>7} {'blk_margin':>10}")
    for mp in (0.15, 0.25, 0.35, 0.50, 0.70, 1.00):
        MARGIN_PCT = mp
        r = run_account(cycles, 2000.0, compounding=True)
        print(f"{mp:>10.2f} ${r['final']:>9,.0f} {r['tot_ret_pct']:>+8.1f}% {r['max_dd_pct']:>6.1f}% "
              f"{r['monthly_pct_cagr']:>+8.2f}% {r['n_taken']:>7}/{r['n_cycles']:<4} {r['blocked_margin']:>10}")
    MARGIN_PCT = orig


if __name__ == "__main__":
    main()

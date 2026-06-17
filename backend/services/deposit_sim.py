"""$400 deposit growth model for the ETH strategy on HONEST per-asset IV.

Replays the live V3 signal stream chronologically and compounds a real account
using the SAME Bybit-realistic friction model as the paper bot
(paper_strategy.py): 15% equity margin/trade, IM = (10%·strike+premium)·qty,
MAX_OPEN_POSITIONS=4, 80% portfolio-margin cap, 0.03% fees (capped), 1% half-
spread, dyn-size (½ when 10-trade WR<40%), circuit-breaker (5 losses → 48h).

Dollar P&L per short-premium trade = qty_eth · entry_credit · pnl_pct, minus
open+close fees. entry_credit/pnl_pct come from the dynamic-σ simulator.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/deposit_sim.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import dyn_sim_set, load_coin
from services.variant_backtest import generate

START = 400.0
MARGIN_PCT = 0.15
IM_RATE = 0.10
LOT = 0.1
MAX_OPEN = 4
PORT_MARGIN_CAP = 0.80
FEE_RATE = 0.0003
FEE_CAP = 0.125
HALF_SPREAD = 0.01          # spread_pct=2 → 1% one-way
EXPIRY_H = 168.0
CB_LOSSES = 5
CB_COOLDOWN_MS = 48 * 3600 * 1000


def fee(notional, premium_total):
    return min(notional * FEE_RATE, abs(premium_total) * FEE_CAP)


def run(coin="eth", compounding=True):
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    sigs = generate(k5, k15, k1h, variant="v3")
    sims = dyn_sim_set(sigs, k5, k1h)
    # keep only entered trades, sorted by entry time
    trades = []
    for s in sims:
        o = s["option"]
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        spot = s["close"]
        strike = round(spot / 25) * 25
        T0 = EXPIRY_H / (24 * 365)
        mid = bs.price(s["side"], spot, strike, T0, s["sigma_used"])
        if mid <= 0.01:
            continue
        credit = mid * (1 - HALF_SPREAD)
        bars = o.get("bars_held", int(EXPIRY_H * 12))
        trades.append({
            "ts": int(s["ts_ms"]), "exit_ts": int(s["ts_ms"]) + bars * 5 * 60 * 1000,
            "side": s["side"], "strike": strike, "mid": mid, "credit": credit,
            "pnl_pct": o["pnl_pct"] / 100.0,
        })
    trades.sort(key=lambda t: t["ts"])

    equity = START
    eq_curve = [(trades[0]["ts"], equity)] if trades else []
    peak = equity
    max_dd = 0.0
    open_pos = []          # list of dicts with exit_ts, margin, pnl_dollars
    recent_pnls = []       # closed pnl_pct, for dyn-size
    consec = 0
    cb_until = 0
    n_taken = n_blocked_cap = n_blocked_margin = n_blocked_cb = 0
    monthly = {}

    def realize_due(now_ts):
        nonlocal equity, peak, max_dd, consec, cb_until
        still = []
        for p in sorted(open_pos, key=lambda x: x["exit_ts"]):
            if p["exit_ts"] <= now_ts:
                equity += p["pnl_dollars"]
                recent_pnls.append(p["pnl_pct"])
                if p["pnl_pct"] > 0:
                    consec = 0
                else:
                    consec += 1
                    if consec >= CB_LOSSES:
                        cb_until = p["exit_ts"] + CB_COOLDOWN_MS
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
                mk = datetime.fromtimestamp(p["exit_ts"] / 1000, tz=timezone.utc).strftime("%Y-%m")
                monthly[mk] = monthly.get(mk, 0.0) + p["pnl_dollars"]
                eq_curve.append((p["exit_ts"], equity))
            else:
                still.append(p)
        open_pos[:] = still

    for t in trades:
        realize_due(t["ts"])
        if t["ts"] < cb_until:
            n_blocked_cb += 1
            continue
        if len(open_pos) >= MAX_OPEN:
            n_blocked_cap += 1
            continue
        size_base = equity if compounding else START
        used_margin = sum(p["margin"] for p in open_pos)
        free = max(0.0, size_base * PORT_MARGIN_CAP - used_margin)
        # dyn-size: halve when last-10 WR < 40%
        dyn = 0.5 if (len(recent_pnls) >= 10 and
                      sum(1 for x in recent_pnls[-10:] if x > 0) / 10 < 0.40) else 1.0
        budget = min(size_base * MARGIN_PCT * dyn, free)
        m_per_lot = (IM_RATE * t["strike"] + t["mid"]) * LOT
        n_lots = int(budget // m_per_lot) if m_per_lot > 0 else 0
        if n_lots < 1:
            n_blocked_margin += 1
            continue
        qty = n_lots * LOT
        margin = m_per_lot * n_lots
        credit_total = t["credit"] * qty
        notional = t["strike"] * qty
        gross = credit_total * t["pnl_pct"]
        fees = 2 * fee(notional, credit_total)
        pnl_dollars = gross - fees
        open_pos.append({"exit_ts": t["exit_ts"], "margin": margin,
                         "pnl_dollars": pnl_dollars, "pnl_pct": t["pnl_pct"]})
        n_taken += 1

    if open_pos:
        realize_due(max(p["exit_ts"] for p in open_pos) + 1)

    days = (trades[-1]["exit_ts"] - trades[0]["ts"]) / 86_400_000 if trades else 0
    tot_ret = (equity - START) / START * 100
    mode = "COMPOUNDING" if compounding else "FIXED $400 sizing"
    print(f"\n===== {coin.upper()} — {mode} =====")
    print(f"period: {days:.0f}d   signals_generated: {len(trades)}   trades_taken: {n_taken}")
    print(f"  blocked: cap={n_blocked_cap}  margin={n_blocked_margin}  cb={n_blocked_cb}")
    print(f"START ${START:.0f}  →  FINAL ${equity:,.2f}   ({tot_ret:+.1f}%, {equity/START:.2f}x)")
    print(f"max drawdown: {max_dd*100:.1f}%   avg $/taken-trade: ${(equity-START)/max(1,n_taken):+.2f}")
    print("monthly P&L ($):")
    for mk in sorted(monthly):
        bar = "█" * max(0, int(abs(monthly[mk]) / max(1, max(abs(v) for v in monthly.values())) * 24))
        print(f"  {mk}: {monthly[mk]:>+9.2f}  {bar}")
    return equity


if __name__ == "__main__":
    run("eth", compounding=True)
    run("eth", compounding=False)

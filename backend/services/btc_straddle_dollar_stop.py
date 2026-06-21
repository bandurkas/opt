"""Redesign of the BTC short-straddle exit rule: stop-loss in ABSOLUTE DOLLARS
relative to posted margin, not % of premium received.

Why the old rule broke (see btc_straddle_gap_stress.py): premium decays toward
zero near expiry, so a "-75% of premium" stop becomes a vanishingly small
dollar trigger right when gamma is highest — a 2% underlying move minutes
from expiry can swing the option's fair value 30-40x, blowing straight through
a stop sized off the (now tiny) premium. Margin posted, by contrast, is
IM_RATE*strike + entry_premium — dominated by the strike term, so it stays a
meaningful dollar amount for the WHOLE option life. Triggering the stop off
"unrealized loss >= X% of margin posted" gives a constant, sane dollar
tripwire throughout the cycle, including the last few minutes.

This sweeps SL_DOLLAR_FRAC (loss as a fraction of per-lot margin) and reports
honest (real BS @ breach) cycle stats — no separate "capped vs honest" split
needed this time since the dollar stop IS the honest exit by construction.

Run: cd backend && python3 services/btc_straddle_dollar_stop.py [coin] [days_back]
"""
from __future__ import annotations

import math
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.btc_straddle_sweep import build_periodic_signals

COIN = sys.argv[1] if len(sys.argv) > 1 else "btc_long"
DAYS_BACK = float(sys.argv[2]) if len(sys.argv) > 2 else 1095.0

CYCLE_H, TP2, MULT = 24.0, 0.80, 1.10
SIGMA_CLAMP = (0.20, 1.50)
SPREAD_PCT = 2.0
HALF_SPREAD = SPREAD_PCT / 200.0
STRIKE_ROUND_BY_COIN = {"btc": 500.0, "btc_long": 500.0, "eth": 25.0, "xaut": 25.0}
STRIKE_ROUND = STRIKE_ROUND_BY_COIN.get(COIN, 500.0)
IM_RATE = 0.10
LOT_BY_COIN = {"btc": 0.01, "btc_long": 0.01, "eth": 0.10}
LOT = LOT_BY_COIN.get(COIN, 0.01)
TRAIN_FRAC = 0.70


def trailing_sigma(k1h, idx_1h, lookback_h=168, mult=MULT):
    if idx_1h < lookback_h + 1:
        return None
    closes = [k1h[i]["close"] for i in range(idx_1h - lookback_h, idx_1h + 1)]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 2:
        return None
    sd = st.stdev(rets)
    sigma = sd * math.sqrt(8760) * mult
    return max(SIGMA_CLAMP[0], min(SIGMA_CLAMP[1], sigma))


def nearest_1h_idx(k1h, ts_ms, hour_ms=3600_000):
    bucket = (ts_ms // hour_ms) * hour_ms
    for i, c in enumerate(k1h):
        if int(c["start_ms"]) == bucket:
            return i
    return None


def simulate_leg_dollar_stop(side, entry_idx, k5, sigma, cycle_h, tp2_pct, sl_dollar_frac):
    spot0 = k5[entry_idx]["close"]
    strike = round(spot0 / STRIKE_ROUND) * STRIKE_ROUND
    T0 = cycle_h / (24 * 365)
    entry_mid = bs.price(side, spot0, strike, T0, sigma)
    if entry_mid <= 0.01:
        return None
    entry_credit = entry_mid * (1 - HALF_SPREAD)
    margin = (IM_RATE * strike + entry_mid) * LOT          # per-lot margin, $ terms
    credit_dollars = entry_credit * LOT
    sl_dollar_trip = sl_dollar_frac * margin                 # constant $ trigger, whole option life
    tp2_mid = entry_credit * (1 - tp2_pct) / (1 + HALF_SPREAD) if (1 + HALF_SPREAD) else 0

    bars_limit = int(cycle_h * 12)
    future = k5[entry_idx + 1: entry_idx + 1 + bars_limit]
    for bi, bar in enumerate(future):
        elapsed_h = (bi + 1) * 5 / 60
        T = max(0.0, (cycle_h - elapsed_h) / (24 * 365))
        hi_spot, lo_spot = bar["high"], bar["low"]
        if side == "C":
            premium_high = bs.price(side, hi_spot, strike, T, sigma)
            premium_low = bs.price(side, lo_spot, strike, T, sigma)
        else:
            premium_high = bs.price(side, lo_spot, strike, T, sigma)
            premium_low = bs.price(side, hi_spot, strike, T, sigma)

        buyback_ask_high = premium_high * (1 + HALF_SPREAD)
        unrealized_loss_dollars = (buyback_ask_high - entry_credit) * LOT
        if unrealized_loss_dollars >= sl_dollar_trip:
            pnl_dollars = credit_dollars - buyback_ask_high * LOT
            return {"resolution": "sl_dollar", "pnl_dollars": pnl_dollars, "margin": margin,
                    "pnl_pct_of_margin": pnl_dollars / margin * 100 if margin else 0.0}
        if premium_low <= tp2_mid:
            buyback_ask_low = premium_low * (1 + HALF_SPREAD)
            pnl_dollars = credit_dollars - buyback_ask_low * LOT
            return {"resolution": "tp2", "pnl_dollars": pnl_dollars, "margin": margin,
                    "pnl_pct_of_margin": pnl_dollars / margin * 100 if margin else 0.0}

    if not future:
        return None
    last = future[-1]
    elapsed_h = len(future) * 5 / 60
    T = max(0.0, (cycle_h - elapsed_h) / (24 * 365))
    final_mid = bs.price(side, last["close"], strike, T, sigma)
    buyback_ask = final_mid * (1 + HALF_SPREAD)
    pnl_dollars = credit_dollars - buyback_ask * LOT
    return {"resolution": "time_stop", "pnl_dollars": pnl_dollars, "margin": margin,
            "pnl_pct_of_margin": pnl_dollars / margin * 100 if margin else 0.0}


def build_cycles(coin, days_back):
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    if days_back:
        cutoff = k5[-1]["start_ms"] - int(days_back * 86_400_000)
        k5 = [c for c in k5 if c["start_ms"] >= cutoff]
        k1h = [c for c in k1h if c["start_ms"] >= cutoff]
    sigs = build_periodic_signals(k5, CYCLE_H)
    cycles_by_idx = {}
    for s in sigs:
        cycles_by_idx.setdefault(s["_cycle"], {})[s["side"]] = s["idx_5m"]
    return k5, k1h, cycles_by_idx


def agg(vals):
    n = len(vals)
    if n == 0:
        return "n 0"
    avg = sum(vals) / n
    sd = st.stdev(vals) if n > 1 else 0
    sh = avg / sd if sd > 0 else 0
    worst = min(vals)
    return f"n{n:>4} avg{avg:>+6.2f}% Sh{sh:>+5.2f}  worst{worst:>+7.1f}%"


def run_sweep(k5, k1h, cycles_by_idx, sl_dollar_frac):
    rows = []  # (ts, pnl_pct_of_margin_combined)
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
        # combined %-of-margin: total pnl$ / total margin$ for the two legs
        tot_pnl = legres["C"]["pnl_dollars"] + legres["P"]["pnl_dollars"]
        tot_margin = legres["C"]["margin"] + legres["P"]["margin"]
        pct = tot_pnl / tot_margin * 100 if tot_margin else 0.0
        rows.append((k5[cycle_idx]["start_ms"], pct))
    return rows


def main():
    k5, k1h, cycles_by_idx = build_cycles(COIN, DAYS_BACK)
    print(f"$-margin-based stop sweep — {len(cycles_by_idx)} candidate cycles\n")
    print(f"{'sl_$_frac':>10}   {'OVERALL (% of margin)':<32}")
    best = None
    candidates = []
    for frac in (0.30, 0.50, 0.75, 1.00, 1.50, 2.00, 3.00):
        rows = run_sweep(k5, k1h, cycles_by_idx, frac)
        pcts = [p for _, p in rows]
        if not pcts:
            continue
        sd = st.stdev(pcts) if len(pcts) > 1 else 0
        sh = (sum(pcts) / len(pcts)) / sd if sd > 0 else 0
        print(f"{frac:>10.2f}   {agg(pcts)}")
        candidates.append((frac, rows))

    if not candidates:
        print("no valid configs")
        return
    # select by TRAIN-only Sharpe (never peek at holdout during selection,
    # same discipline as btc_straddle_sweep.py)
    ts_all = sorted(t for t, _ in candidates[0][1])
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    for frac, rows in candidates:
        tr_pcts = [p for t, p in rows if t < split_ts]
        sd = st.stdev(tr_pcts) if len(tr_pcts) > 1 else 0
        train_sh = (sum(tr_pcts) / len(tr_pcts)) / sd if sd > 0 else 0
        if best is None or train_sh > best[1]:
            best = (frac, train_sh, rows)

    frac, train_sh, rows = best
    tr = [p for t, p in rows if t < split_ts]
    ho = [p for t, p in rows if t >= split_ts]
    print(f"\n=== BEST sl_$_frac={frac} (by TRAIN Sharpe={train_sh:+.2f}) — TRAIN/HOLDOUT check ===")
    print(f"TRAIN   {agg(tr)}")
    print(f"HOLDOUT {agg(ho)}")


if __name__ == "__main__":
    main()

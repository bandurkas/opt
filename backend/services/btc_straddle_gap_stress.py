"""Gap-risk stress test for the BTC short-straddle (cycle=24h tp1=0.50 tp2=0.80
sl=0.75 mult=1.10). The engine's SL detection already checks each 5m bar's
HIGH/LOW (not just close), so it correctly catches that a violent bar breached
the stop — but it then caps the realized loss at exactly -sl_pct, assuming a
perfect fill right at the threshold. Empirically, 92/954 SL-hit legs (3y BTC)
happened during a bar with >2% high-low range (up to 13.72% in one bar) — real
execution during a move that fast would slip past the modeled exit price.

This computes the UNCAPPED loss instead: when a bar's intrabar extreme first
breaches the SL trigger, price the option at that actual extreme via BS (which
already reflects the real move size) rather than freezing the loss at -sl_pct.
Removes the optimistic "always fills exactly at the SL line" assumption
without inventing an arbitrary stress multiplier.

Run: cd backend && python3 services/btc_straddle_gap_stress.py [coin] [days_back]
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.btc_straddle_sweep import build_periodic_signals

COIN = sys.argv[1] if len(sys.argv) > 1 else "btc_long"
DAYS_BACK = float(sys.argv[2]) if len(sys.argv) > 3 else 1095.0
CYCLE_H, TP1, TP2, SL, MULT = 24.0, 0.50, 0.80, 0.75, 1.10
SIGMA_CLAMP = (0.20, 1.50)
SPREAD_PCT = 2.0
STRIKE_ROUND_BY_COIN = {"btc": 500.0, "btc_long": 500.0, "eth": 25.0, "xaut": 25.0}
STRIKE_ROUND = STRIKE_ROUND_BY_COIN.get(COIN, 500.0)
HALF_SPREAD = SPREAD_PCT / 200.0


def trailing_sigma(k1h, idx_1h, lookback_h=168, mult=MULT):
    if idx_1h < lookback_h + 1:
        return None
    closes = [k1h[i]["close"] for i in range(idx_1h - lookback_h, idx_1h + 1)]
    import math
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


def simulate_leg_honest(side, entry_idx, k5, sigma, cycle_h, tp2_pct, sl_pct, early_close_h=0.0):
    """Re-implementation of _simulate_short_premium but WITHOUT capping the SL
    exit at exactly -sl_pct — uses the real BS price at the breaching bar's
    intrabar extreme instead. `early_close_h` forces a time-stop that many
    hours before the nominal expiry, to test avoiding the high-gamma window
    right before expiry."""
    spot0 = k5[entry_idx]["close"]
    strike = round(spot0 / STRIKE_ROUND) * STRIKE_ROUND
    T0 = cycle_h / (24 * 365)
    entry_mid = bs.price(side, spot0, strike, T0, sigma)
    if entry_mid <= 0.01:
        return None
    entry_credit = entry_mid * (1 - HALF_SPREAD)
    sl_mid = entry_credit * (1 + sl_pct) / (1 + HALF_SPREAD)
    tp2_mid = entry_credit * (1 - tp2_pct) / (1 + HALF_SPREAD) if (1 + HALF_SPREAD) else 0

    effective_h = cycle_h - early_close_h
    bars_limit = max(1, int(effective_h * 12))
    future = k5[entry_idx + 1: entry_idx + 1 + bars_limit]
    max_bar_range = 0.0
    for bi, bar in enumerate(future):
        elapsed_h = (bi + 1) * 5 / 60
        T = max(0.0, (cycle_h - elapsed_h) / (24 * 365))
        rng = (bar["high"] - bar["low"]) / bar["open"] * 100 if bar["open"] else 0
        max_bar_range = max(max_bar_range, rng)
        hi_spot, lo_spot = bar["high"], bar["low"]
        if side == "C":
            premium_high = bs.price(side, hi_spot, strike, T, sigma)
            premium_low = bs.price(side, lo_spot, strike, T, sigma)
        else:
            premium_high = bs.price(side, lo_spot, strike, T, sigma)
            premium_low = bs.price(side, hi_spot, strike, T, sigma)

        if premium_high >= sl_mid:
            # HONEST exit: actually buy back at the real (worse) intrabar price,
            # not frozen at the threshold.
            buyback_ask = premium_high * (1 + HALF_SPREAD)
            pnl = (entry_credit - buyback_ask) / entry_credit
            return {"resolution": "sl_honest", "pnl_pct": pnl * 100,
                    "capped_pnl_pct": -sl_pct * 100, "max_bar_range": max_bar_range}
        if premium_low <= tp2_mid:
            return {"resolution": "tp2", "pnl_pct": tp2_pct * 100,
                    "capped_pnl_pct": tp2_pct * 100, "max_bar_range": max_bar_range}

    if not future:
        return None
    last = future[-1]
    elapsed_h = len(future) * 5 / 60
    T = max(0.0, (cycle_h - elapsed_h) / (24 * 365))
    final_mid = bs.price(side, last["close"], strike, T, sigma)
    buyback_ask = final_mid * (1 + HALF_SPREAD)
    pnl = (entry_credit - buyback_ask) / entry_credit
    return {"resolution": "time_stop", "pnl_pct": pnl * 100,
            "capped_pnl_pct": pnl * 100, "max_bar_range": max_bar_range}


def main():
    k5, k15, k1h = load_coin(COIN, find_data_dir(None))
    if DAYS_BACK:
        cutoff = k5[-1]["start_ms"] - int(DAYS_BACK * 86_400_000)
        k5 = [c for c in k5 if c["start_ms"] >= cutoff]
        k1h = [c for c in k1h if c["start_ms"] >= cutoff]

    sigs = build_periodic_signals(k5, CYCLE_H)
    cycles_by_idx = {}
    for s in sigs:
        cycles_by_idx.setdefault(s["_cycle"], {})[s["side"]] = s["idx_5m"]

    def agg(vals):
        n = len(vals)
        if n == 0:
            return "n 0"
        avg = sum(vals) / n
        sd = st.stdev(vals) if n > 1 else 0
        sh = avg / sd if sd > 0 else 0
        worst = min(vals)
        return f"n{n:>4} avg{avg:>+6.2f}% Sh{sh:>+5.2f}  worst{worst:>+8.1f}%"

    def run_for(early_close_h):
        rows = []
        n_sl_honest_worse = 0
        worst_gaps = []
        for cycle_idx, legs in sorted(cycles_by_idx.items()):
            if "C" not in legs or "P" not in legs:
                continue
            idx_1h = nearest_1h_idx(k1h, k5[cycle_idx]["start_ms"])
            if idx_1h is None:
                continue
            sigma = trailing_sigma(k1h, idx_1h)
            if sigma is None:
                continue
            leg_results = {}
            for side in ("C", "P"):
                r = simulate_leg_honest(side, legs[side], k5, sigma, CYCLE_H, TP2, SL, early_close_h)
                if r is None:
                    leg_results = None
                    break
                leg_results[side] = r
            if not leg_results:
                continue
            capped_avg = (leg_results["C"]["capped_pnl_pct"] + leg_results["P"]["capped_pnl_pct"]) / 2
            honest_avg = (leg_results["C"]["pnl_pct"] + leg_results["P"]["pnl_pct"]) / 2
            rows.append((k5[cycle_idx]["start_ms"], capped_avg, honest_avg))
            for side in ("C", "P"):
                r = leg_results[side]
                if r["resolution"] == "sl_honest" and r["pnl_pct"] < r["capped_pnl_pct"] - 1.0:
                    n_sl_honest_worse += 1
                    worst_gaps.append((r["pnl_pct"], r["capped_pnl_pct"], r["max_bar_range"]))
        return rows, n_sl_honest_worse, worst_gaps

    print(f"early-close sweep on {CYCLE_H:.0f}h cycle — closing N hours before "
          f"nominal expiry to skip the high-gamma window\n")
    for early_h in (0.0, 1.0, 2.0, 4.0, 6.0, 8.0, 12.0):
        rows, n_worse, worst_gaps = run_for(early_h)
        capped = [r[1] for r in rows]
        honest = [r[2] for r in rows]
        print(f"close {early_h:>4.1f}h early ({CYCLE_H-early_h:>4.1f}h held):  "
              f"CAPPED {agg(capped)}   HONEST {agg(honest)}   worse_legs={n_worse}")

    print("\nworst 10 honest-vs-capped gaps AT early_close=0 (baseline, for reference):")
    rows0, _, worst_gaps0 = run_for(0.0)
    worst_gaps0.sort()
    for h, c, r in worst_gaps0[:10]:
        print(f"  honest={h:>+8.1f}%  capped={c:>+6.1f}%  bar_range={r:.2f}%")


if __name__ == "__main__":
    main()

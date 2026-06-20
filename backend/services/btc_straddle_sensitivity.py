"""Stress-test the winning BTC straddle config (cycle=24h tp1=0.50 tp2=0.80
sl=0.75 mult=1.10, found by btc_straddle_sweep.py) against the two gaps the
sweep didn't cover:

1. Real Bybit BTC strike spacing. The engine defaults to $25 spacing (right
   for ETH at ~$2-3k), which is near-perfect ATM for BTC at ~$30-100k — way
   finer than real listed strikes. Real Bybit BTC option strikes step in
   $500/$1000/$2500 depending on price range.
2. Spread sensitivity. The sweep assumed a flat 2% round-trip spread; the
   gold iron-butterfly finding flipped sign entirely once a realistic spread
   (up to 22%) replaced the optimistic 2% assumption — same trap could apply
   here.

Run: cd backend && python3 services/btc_straddle_sensitivity.py
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.btc_straddle_sweep import build_periodic_signals

COIN = "btc"
CYCLE_H, TP1, TP2, SL, MULT = 24.0, 0.50, 0.80, 0.75, 1.10
SIGMA_CLAMP = (0.20, 1.50)
TRAIN_FRAC = 0.70
STRIKE_ROUNDS = (25, 500, 1000, 2500, 5000)
SPREADS = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0)


def agg(pnls):
    if not pnls:
        return "n   0"
    n = len(pnls)
    avg = sum(pnls) / n
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    return f"n{n:>4} avg{avg:>+6.2f}% WR{wr*100:>3.0f}% Sh{sh:>+5.2f}"


def run(k5, k1h, sigs, spread_pct, strike_round_to):
    out = simulate_signal_set(
        sigs, k5, sigma=0.60, expiry_hours=CYCLE_H, tp1_pct=TP1, tp2_pct=TP2,
        sl_pct=SL, option_horizon_h=CYCLE_H, spread_pct=spread_pct,
        dynamic_sigma=True, klines_1h=k1h, iv_rv_multiplier=MULT,
        sigma_clamp=SIGMA_CLAMP, strike_round_to=strike_round_to,
    )
    by_cycle = {}
    for o in out:
        opt = o.get("option", {})
        if "pnl_pct" not in opt or opt.get("resolution") in ("no_entry", "no_data"):
            continue
        by_cycle.setdefault(o["_cycle"], {"ts_ms": o["ts_ms"]})[o["side"]] = opt["pnl_pct"]
    rows = [(d["ts_ms"], (d["C"] + d["P"]) / 2) for c, d in by_cycle.items() if "C" in d and "P" in d]
    if not rows:
        return None
    ts_all = sorted(t for t, _ in rows)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    tr = [p for t, p in rows if t < split_ts]
    ho = [p for t, p in rows if t >= split_ts]
    return tr, ho


def main():
    k5, k15, k1h = load_coin(COIN, find_data_dir(None))
    sigs = build_periodic_signals(k5, CYCLE_H)
    spot_now = k5[-1]["close"]
    print(f"BTC spot (last bar): ${spot_now:,.0f}\n")

    print("=== 1) STRIKE SPACING sensitivity (spread fixed at 2%) ===")
    for rt in STRIKE_ROUNDS:
        r = run(k5, k1h, sigs, 2.0, rt)
        if r is None:
            print(f"  round_to=${rt:<6}: no trades")
            continue
        tr, ho = r
        print(f"  round_to=${rt:<6}: TRAIN {agg(tr)}  |  HOLDOUT {agg(ho)}")

    print("\n=== 2) SPREAD sensitivity (strike spacing fixed at $25, then at $1000) ===")
    for rt_label, rt in (("$25 (sweep default)", 25), ("$1000 (realistic BTC)", 1000)):
        print(f"  -- strike spacing {rt_label} --")
        for sp in SPREADS:
            r = run(k5, k1h, sigs, sp, rt)
            if r is None:
                print(f"    spread={sp:>4.1f}%: no trades")
                continue
            tr, ho = r
            print(f"    spread={sp:>4.1f}%: TRAIN {agg(tr)}  |  HOLDOUT {agg(ho)}")


if __name__ == "__main__":
    main()

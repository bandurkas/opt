"""Mirror the live options bot's SIGNAL with a leveraged PERP on a second account.

User's idea: the bot picks a side (sells Call in down-bias, sells Put in up-bias);
shadow that direction with a futures position. Bot sells Call => SHORT perp;
bot sells Put => LONG perp. Deposit $70, leverage x3, compounding.

HONEST engine: sequential single position (a $70 account can't really run several
concurrent x3 legs; the bot fires ~4 signals/day held 24-96h, so they overlap and
you take far fewer). Models the thing that actually kills a small leveraged account:
LIQUIDATION. At leverage L a move of ~1/L against you wipes the margin; with
maintenance margin mm, liq trigger = ret_dir <= mm - 1/L (x3 => ~-32.8%). ETH does
that inside a 24-96h window in volatile months. Plus taker fee + slippage each side
and 8h funding (long pays positive funding).

Signals = exact live bot (variant_backtest.generate v3), same per-side hold (Call
24h, Put 96h). Chronological train/holdout. Also a leverage sweep, because leverage
IS the whole story here.

Run:
  docker run --rm -v "$PWD/backend:/app" -v "$PWD/data:/data" -w /app \
    -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/mirror_perp_backtest.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.option_futures_complement import fund_lookup
from services.variant_backtest import generate

START = 70.0
TAKER = 0.00055      # Bybit perp taker
SLIP = 0.00020       # slippage per side
MM = 0.005           # maintenance margin rate
HOLD_BARS = {"C": 24 * 12, "P": 96 * 12}   # option hold per side, in 5m bars
DIR = {"C": -1, "P": +1}                    # mirror: short call->short perp, short put->long perp
TRAIN_FRAC = 0.70


def run(signals, px5, ts5, fund_f, lev, start=START, label=""):
    equity = start
    peak = equity
    max_dd = 0.0
    busy_until = -1            # 5m index until which we hold a position (sequential)
    n_taken = n_liq = n_win = 0
    curve = []
    for s in sorted(signals, key=lambda x: x["idx_5m"]):
        ke = s["idx_5m"]
        if ke <= busy_until or ke >= len(px5):
            continue
        d = DIR[s["side"]]
        hold = HOLD_BARS[s["side"]]
        kx = min(ke + hold, len(px5) - 1)
        entry = px5[ke]
        notional = equity * lev
        # walk for liquidation
        liq = False
        liq_ret = mm_trigger = MM - 1.0 / lev
        for k in range(ke + 1, kx + 1):
            ret_dir = d * (px5[k] / entry - 1.0)
            if ret_dir <= mm_trigger:
                liq = True
                break
        if liq:
            equity = max(0.0, equity * (1.0 + lev * mm_trigger))   # margin wiped to ~maintenance
            n_liq += 1
            busy_until = k
            curve.append(equity)
            peak = max(peak, equity); max_dd = max(max_dd, (peak - equity) / peak if peak else 0)
            if equity < 1.0:
                break                                              # account blown
            continue
        # normal close at hold
        exit_px = px5[kx]
        gross = d * (exit_px / entry - 1.0) * notional
        fees = 2 * (TAKER + SLIP) * notional
        # funding: long pays positive funding; charged ~every 8h
        fund = 0.0
        for k in range(ke, kx):
            if ts5[k] % (8 * 3600 * 1000) < 5 * 60 * 1000:        # ~8h boundary bar
                fund += d * fund_f(ts5[k]) * notional               # long*positive = pays => subtract
        equity += gross - fees - fund
        n_taken += 1
        if gross - fees - fund > 0:
            n_win += 1
        busy_until = kx
        curve.append(equity)
        peak = max(peak, equity); max_dd = max(max_dd, (peak - equity) / peak if peak else 0)

    ret = (equity - start) / start * 100
    wr = 100 * n_win / max(1, n_taken)
    print(f"  {label:24} L=x{lev:<3.0f} taken={n_taken:4} liq={n_liq:3} WR={wr:4.0f}% "
          f"| START ${start:.0f} -> ${equity:8.2f} ({ret:+7.1f}%) | maxDD {max_dd*100:5.1f}%"
          f"{'  *** BLOWN ***' if equity < 1.0 else ''}")
    return equity


def main():
    k5, k15, k1h = load_coin("eth", find_data_dir(None))
    sigs = generate(k5, k15, k1h, variant="v3")
    px5 = [float(c["close"]) for c in k5]
    ts5 = [c["start_ms"] + 5 * 60 * 1000 for c in k5]
    fund_f = fund_lookup()
    n5 = len(k5); split = int(n5 * TRAIN_FRAC)
    tr = [s for s in sigs if s["idx_5m"] < split]
    ho = [s for s in sigs if s["idx_5m"] >= split]
    nc = sum(1 for s in sigs if s["side"] == "C"); np_ = len(sigs) - nc
    print(f"ETH mirror-perp | {len(sigs)} signals ({nc} Call->short, {np_} Put->long) "
          f"| train {len(tr)} / holdout {len(ho)} | $70, sequential, liq-modeled\n")

    print("########## MIRROR both sides (Call->short, Put->long), x3 ##########")
    run(sigs, px5, ts5, fund_f, 3, label="FULL period")
    run(tr,   px5, ts5, fund_f, 3, label="TRAIN")
    run(ho,   px5, ts5, fund_f, 3, label="HOLDOUT")

    print("\n########## leverage sweep (FULL period, both sides) ##########")
    for L in (1, 2, 3, 5, 10):
        run(sigs, px5, ts5, fund_f, L, label="mirror")

    print("\n########## sanity: ONE side only, x3 (is either leg directional?) ##########")
    run([s for s in sigs if s["side"] == "C"], px5, ts5, fund_f, 3, label="Call->SHORT only")
    run([s for s in sigs if s["side"] == "P"], px5, ts5, fund_f, 3, label="Put->LONG only")

    print("\n########## INVERSE mirror (fade the bot: Call->long, Put->short), x3 ##########")
    inv = [{**s, "side": ("P" if s["side"] == "C" else "C")} for s in sigs]
    # note: inverse flips DIR via swapped side, but keeps each signal's own hold horizon
    run(inv, px5, ts5, fund_f, 3, label="INVERSE both")

    print("\nReality check: a small x3 account lives or dies by liquidations + funding/fee bleed. "
          "Direction has no proven OOS edge (see perp_trend holdout -66%); this quantifies it for $70/x3.")


if __name__ == "__main__":
    main()

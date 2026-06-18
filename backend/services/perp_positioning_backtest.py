#!/usr/bin/env python3
"""H1 — Positioning-crowding MEAN-REVERSION on the ETH perp.

Hypothesis (reverse-engineered from the options project's edge): the project's
proven edge is VRP + the *range regime* (price reverts inside the implied cone).
We can't hold vega on a perp, but we CAN express the same "range = mean-reverting"
structure as a delta-one fade — and gate it with crowded-positioning data the
project already stores (OI, long/short ratio, taker buy ratio, funding).

Idea: when the crowd is extremely long (high LSR / aggressive taker buying / rising
OI / high funding) AND we are NOT in a strong trend, fade it (SHORT), expecting a
short-horizon reversion (squeeze). Symmetric on the short-crowded side (LONG).

This is the OPPOSITE of the rejected perp_trend_backtest.py (trend-follow in trend
windows, holdout -66%). Here we mean-revert in RANGE windows. Same rigor: causal
features only, realistic taker fee + slippage + funding over the hold, and an
honest chronological train/holdout split (no peeking).

All inputs are the options project's stored data (default ~/Desktop/options/data).
"""
import argparse
import json
import math
import os
import statistics as st
from pathlib import Path

DATA = Path(os.path.expanduser("~/Desktop/options/data"))
HOUR = 3_600_000


def load_series(fname, key, tkey="ts_ms"):
    """Return list of (ts_ms, value) sorted ascending."""
    d = json.loads((DATA / fname).read_text())
    out = [(int(r[tkey]), float(r[key])) for r in d]
    out.sort(key=lambda x: x[0])
    return out


def at_or_before(series, t):
    """Most recent value at or before time t (causal). series sorted asc."""
    lo, hi = 0, len(series) - 1
    if t < series[0][0]:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if series[mid][0] <= t:
            lo = mid
        else:
            hi = mid - 1
    return series[lo][1]


def build_panel():
    """Hourly aligned panel: ts, close, oi, lsr, buy_ratio, funding_hourly."""
    klines = json.loads((DATA / "eth_1h.json").read_text())
    klines.sort(key=lambda c: c["start_ms"])
    oi = load_series("eth_oi.json", "open_interest")
    lsr = load_series("eth_long_short.json", "long_short_ratio")
    buyr = load_series("eth_long_short.json", "buy_ratio")
    fund = load_series("eth_funding.json", "funding_rate")  # per 8h interval

    panel = []
    for c in klines:
        t = c["start_ms"]
        o, l, b, f = (at_or_before(oi, t), at_or_before(lsr, t),
                      at_or_before(buyr, t), at_or_before(fund, t))
        if None in (o, l, b):       # need positioning data; funding optional
            continue
        fh = (f / 8.0) if f is not None else 0.0   # 8h rate -> per-hour
        panel.append({"ts": t, "close": float(c["close"]), "oi": o,
                      "lsr": l, "buy": b, "fund_h": fh})
    return panel


def zscore_causal(vals, i, w):
    """z of vals[i] using window [i-w, i-1] (strictly past). None if insufficient."""
    if i < w:
        return None
    win = vals[i - w:i]
    m = st.fmean(win)
    s = st.pstdev(win)
    if s == 0:
        return 0.0
    return (vals[i] - m) / s


def efficiency_ratio(closes, i, n):
    """Kaufman ER over last n bars: |net move| / sum|moves|. 0=chop, 1=trend."""
    if i < n:
        return None
    net = abs(closes[i] - closes[i - n])
    path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - n + 1, i + 1))
    if path == 0:
        return 0.0
    return net / path


def run(panel, zwin=168, oi_lookback=24, thr=1.0, er_gate=0.35, hold=12,
        fee_rt=0.0007, slip=0.0004, train_frac=0.65, use_lsr=True):
    closes = [p["close"] for p in panel]
    lsr = [p["lsr"] for p in panel]
    buy = [p["buy"] for p in panel]
    oi = [p["oi"] for p in panel]
    n = len(panel)

    # OI rate-of-change series (causal), then z it
    oi_chg = [0.0] * n
    for i in range(n):
        if i >= oi_lookback and oi[i - oi_lookback] > 0:
            oi_chg[i] = oi[i] / oi[i - oi_lookback] - 1.0

    trades = []
    cooldown_until = -1
    for i in range(zwin, n - hold):
        if i < cooldown_until:
            continue
        er = efficiency_ratio(closes, i, 48)
        if er is None or er > er_gate:      # only fade in RANGE (low ER)
            continue
        zs = []
        if use_lsr:
            z = zscore_causal(lsr, i, zwin)
            if z is not None:
                zs.append(z)
        zb = zscore_causal(buy, i, zwin)
        if zb is not None:
            zs.append(zb)
        zo = zscore_causal(oi_chg, i, zwin)
        if zo is not None:
            zs.append(zo)
        if not zs:
            continue
        crowd = st.fmean(zs)            # >0 => crowd is long

        side = 0
        if crowd > thr:
            side = -1                   # fade crowded longs -> SHORT
        elif crowd < -thr:
            side = +1                   # fade crowded shorts -> LONG
        if side == 0:
            continue

        entry = closes[i]
        exit_ = closes[i + hold]
        gross = side * (exit_ / entry - 1.0)
        # funding over hold: short receives positive funding, long pays it
        fund_pnl = -side * sum(panel[j]["fund_h"] for j in range(i, i + hold))
        net = gross + fund_pnl - fee_rt - slip
        trades.append({"i": i, "ts": panel[i]["ts"], "side": side,
                       "gross": gross, "fund": fund_pnl, "net": net, "crowd": crowd})
        cooldown_until = i + hold       # non-overlapping positions

    split = int(n * train_frac)
    tr = [t for t in trades if t["i"] < split]
    ho = [t for t in trades if t["i"] >= split]
    return trades, tr, ho, split


def summ(label, ts):
    if not ts:
        print(f"  {label:9} n=0")
        return
    nets = [t["net"] for t in ts]
    longs = [t for t in ts if t["side"] > 0]
    shorts = [t for t in ts if t["side"] < 0]
    avg = st.fmean(nets) * 100
    tot = sum(nets) * 100
    wr = 100 * sum(x > 0 for x in nets) / len(nets)
    sharpe = (st.fmean(nets) / st.pstdev(nets) * math.sqrt(len(nets))) if len(nets) > 1 and st.pstdev(nets) > 0 else 0
    print(f"  {label:9} n={len(ts):4} | avg/trade {avg:+.3f}% | total {tot:+.1f}% | "
          f"win {wr:.0f}% | t-stat {sharpe:.2f} | L/S {len(longs)}/{len(shorts)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr", type=float, default=1.0, help="crowd z-score entry threshold")
    ap.add_argument("--er-gate", type=float, default=0.35, help="max efficiency ratio (range filter)")
    ap.add_argument("--hold", type=int, default=12, help="hold horizon in hours")
    ap.add_argument("--zwin", type=int, default=168, help="rolling z window (hrs)")
    ap.add_argument("--fee-rt", type=float, default=0.0007, help="round-trip taker fee")
    ap.add_argument("--slip", type=float, default=0.0004, help="round-trip slippage")
    ap.add_argument("--no-lsr", action="store_true", help="drop long/short ratio (weak signal) from composite")
    ap.add_argument("--train-frac", type=float, default=0.65)
    args = ap.parse_args()

    panel = build_panel()
    import datetime as dt
    t0 = dt.datetime.utcfromtimestamp(panel[0]["ts"] / 1000)
    t1 = dt.datetime.utcfromtimestamp(panel[-1]["ts"] / 1000)
    print(f"Panel: {len(panel)} hourly bars {t0:%Y-%m-%d}..{t1:%Y-%m-%d} (OI+LSR+price overlap)")
    print(f"Params: thr={args.thr} er_gate={args.er_gate} hold={args.hold}h zwin={args.zwin}h "
          f"costs={100*(args.fee_rt+args.slip):.2f}%/rt lsr={'off' if args.no_lsr else 'on'}")

    trades, tr, ho, split = run(panel, zwin=args.zwin, thr=args.thr, er_gate=args.er_gate,
                                hold=args.hold, fee_rt=args.fee_rt, slip=args.slip,
                                train_frac=args.train_frac, use_lsr=not args.no_lsr)
    st0 = dt.datetime.utcfromtimestamp(panel[split]["ts"] / 1000)
    print(f"Split at bar {split} ({st0:%Y-%m-%d}) — train older, holdout newer.\n")
    summ("ALL", trades)
    summ("TRAIN", tr)
    summ("HOLDOUT", ho)
    print("\navg/trade is NET (fees+slippage+funding). t-stat>2 ~ significant; holdout is the real test.")
    print("L/S balance shows if it's a real fade vs accidental directional bet.")


if __name__ == "__main__":
    main()

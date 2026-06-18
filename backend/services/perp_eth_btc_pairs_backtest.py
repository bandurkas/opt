#!/usr/bin/env python3
"""A — ETH/BTC market-neutral PAIRS mean-reversion (perp vs perp).

Why this, after H1/H2/trend all died: every prior perp idea failed by *directional
leak* (secretly short a falling market). A pairs trade is DELTA-NEUTRAL by
construction — long one leg, short the other in equal notional — so market
direction cancels and that failure mode is structurally removed. We fade the
ETH/BTC ratio's deviation from a causal moving anchor (classic stat-arb).

Signal: z = (logRatio - SMA_N) / std_N, causal.  Ratio = ETH/BTC.
  z > +thr -> ratio rich  -> SHORT spread (short ETH, long BTC)
  z < -thr -> ratio cheap -> LONG  spread (long ETH, short BTC)
Exit: ratio reverts (z crosses 0) or max-hold cap. Spread return = side*(ethRet-btcRet).

Honest accounting: 4 fills/round-trip (2 legs in + out) at taker fee + slippage each,
plus NET funding over the hold (long leg pays, short leg receives). Chronological
train/holdout, and a per-SIDE split — a real mean-reversion must profit on BOTH the
long-spread and short-spread side, else it's just riding ETH/BTC's net drift.

Data: data/eth_1h.json, btc_1h.json (Bybit klines), eth_funding.json, btc_funding.json.
"""
import argparse
import json
import math
import os
import statistics as st
import datetime as dt
from pathlib import Path

DATA = Path(os.path.expanduser("~/Desktop/options/data"))


def fund_lookup(fname):
    d = json.loads((DATA / fname).read_text())
    d.sort(key=lambda r: r["ts_ms"])
    ts = [r["ts_ms"] for r in d]
    rt = [float(r["funding_rate"]) for r in d]

    def f(t):
        lo, hi = 0, len(ts) - 1
        if t < ts[0]:
            return 0.0
        while lo < hi:
            m = (lo + hi + 1) // 2
            if ts[m] <= t:
                lo = m
            else:
                hi = m - 1
        return rt[lo] / 8.0   # 8h -> per hour
    return f


def build_panel():
    e = json.loads((DATA / "eth_1h.json").read_text()); e.sort(key=lambda c: c["start_ms"])
    b = json.loads((DATA / "btc_1h.json").read_text()); b.sort(key=lambda c: c["start_ms"])
    bd = {c["start_ms"]: float(c["close"]) for c in b}
    ef, bf = fund_lookup("eth_funding.json"), fund_lookup("btc_funding.json")
    panel = []
    for c in e:
        t = c["start_ms"]
        if t not in bd:
            continue
        panel.append({"ts": t, "eth": float(c["close"]), "btc": bd[t],
                      "ef": ef(t), "bf": bf(t)})
    return panel


def run(panel, zwin=168, thr=1.5, max_hold=168, fee=0.00035, slip=0.0002, train_frac=0.65):
    n = len(panel)
    lr = [math.log(p["eth"] / p["btc"]) for p in panel]
    eth = [p["eth"] for p in panel]
    btc = [p["btc"] for p in panel]
    rt_cost = 4 * (fee + slip)        # 2 legs * (in+out)

    trades = []
    i = zwin
    while i < n - 1:
        win = lr[i - zwin:i]
        m, s = st.fmean(win), st.pstdev(win)
        if s == 0:
            i += 1; continue
        z = (lr[i] - m) / s
        if abs(z) < thr:
            i += 1; continue
        side = -1 if z > 0 else +1     # +1 long-spread (long ETH/short BTC)
        entry_i = i
        # hold until z reverts toward 0 or cap
        j = i + 1
        while j < n - 1 and (j - entry_i) < max_hold:
            wj = lr[j - zwin:j]
            mj, sj = st.fmean(wj), st.pstdev(wj)
            zj = (lr[j] - mj) / sj if sj > 0 else 0.0
            if (side > 0 and zj >= 0) or (side < 0 and zj <= 0):
                break
            j += 1
        eth_ret = eth[j] / eth[entry_i] - 1.0
        btc_ret = btc[j] / btc[entry_i] - 1.0
        spread_ret = side * (eth_ret - btc_ret)
        # net funding: long leg pays its funding, short leg receives the other's
        fund = 0.0
        for k in range(entry_i, j):
            if side > 0:   # long ETH (pay ef), short BTC (receive bf)
                fund += panel[k]["bf"] - panel[k]["ef"]
            else:          # short ETH (receive ef), long BTC (pay bf)
                fund += panel[k]["ef"] - panel[k]["bf"]
        net = spread_ret + fund - rt_cost
        trades.append({"i": entry_i, "side": side, "net": net, "gross": spread_ret,
                       "fund": fund, "bars": j - entry_i, "z": z})
        i = j + 1   # non-overlapping
    split = int(n * train_frac)
    return trades, split


def summ(label, ts):
    if not ts:
        print(f"  {label:9} n=0"); return
    nets = [t["net"] for t in ts]
    L = [t["net"] for t in ts if t["side"] > 0]
    S = [t["net"] for t in ts if t["side"] < 0]
    avg = st.fmean(nets) * 100; tot = sum(nets) * 100
    wr = 100 * sum(x > 0 for x in nets) / len(nets)
    tstat = (st.fmean(nets) / st.pstdev(nets) * math.sqrt(len(nets))) if len(nets) > 1 and st.pstdev(nets) > 0 else 0
    hold = st.fmean([t["bars"] for t in ts])
    la = st.fmean(L) * 100 if L else 0; sa = st.fmean(S) * 100 if S else 0
    print(f"  {label:9} n={len(ts):4} | avg {avg:+.3f}% | tot {tot:+.1f}% | win {wr:.0f}% | "
          f"t {tstat:+.2f} | hold {hold:.0f}h | Lspr {len(L)}@{la:+.2f}% Sspr {len(S)}@{sa:+.2f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zwin", type=int, default=168)
    ap.add_argument("--thr", type=float, default=1.5)
    ap.add_argument("--max-hold", type=int, default=168)
    ap.add_argument("--fee", type=float, default=0.00035, help="taker fee per fill")
    ap.add_argument("--slip", type=float, default=0.0002, help="slippage per fill")
    ap.add_argument("--train-frac", type=float, default=0.65)
    args = ap.parse_args()

    panel = build_panel()
    t0 = dt.datetime.utcfromtimestamp(panel[0]["ts"] / 1000)
    t1 = dt.datetime.utcfromtimestamp(panel[-1]["ts"] / 1000)
    print(f"Panel: {len(panel)} hrs {t0:%Y-%m-%d}..{t1:%Y-%m-%d}  (ETH & BTC perp)")
    print(f"Params: zwin={args.zwin}h thr={args.thr}sigma max_hold={args.max_hold}h "
          f"cost={100*4*(args.fee+args.slip):.2f}%/round-trip (4 fills)")
    trades, split = run(panel, zwin=args.zwin, thr=args.thr, max_hold=args.max_hold,
                        fee=args.fee, slip=args.slip, train_frac=args.train_frac)
    tr = [t for t in trades if t["i"] < split]
    ho = [t for t in trades if t["i"] >= split]
    sd = dt.datetime.utcfromtimestamp(panel[split]["ts"] / 1000)
    print(f"Split {sd:%Y-%m-%d} (train older / holdout newer)\n")
    summ("ALL", trades); summ("TRAIN", tr); summ("HOLDOUT", ho)
    print("\nMarket-neutral: BOTH Lspr & Sspr must be positive & train~holdout for a real edge.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""H2 — IV-implied-cone MEAN-REVERSION on the ETH perp, gated by VRP.

Thesis (the futures expression of the project's proven edge): VRP>0 means implied
vol overstates realized — price tends to stay INSIDE the implied cone more than the
market prices. So when price has extended >k implied-sigma from a causal anchor AND
implied vol is rich (IV>RV), fade it back (mean-revert), in range regimes only.

We finally have REAL implied vol: Deribit ETH DVOL hourly index (data/eth_dvol_1h.json,
~16 mo) — not the synthetic RV-sigma every prior backtest used. RV from eth_1h klines.

Compares three modes on the same trades to isolate what (if anything) carries the edge:
  (1) IV-cone fade, NO VRP gate
  (2) IV-cone fade, VRP>0 gate            <- the H2 hypothesis
  (3) RV-cone fade (control: replace IV with realized vol in the band)

Same rigor as the rejected perp harnesses: causal features, realistic taker fee +
slippage + funding over the hold, chronological train/holdout, and a per-SIDE split
(a real fade must profit on BOTH long and short — not just ride the trend).
"""
import argparse
import json
import math
import os
import statistics as st
import datetime as dt
from pathlib import Path

DATA = Path(os.path.expanduser("~/Desktop/options/data"))


def build_panel(win):
    klines = json.loads((DATA / "eth_1h.json").read_text())
    klines.sort(key=lambda c: c["start_ms"])
    dvol = json.loads((DATA / "eth_dvol_1h.json").read_text())  # [ts,o,h,l,c]
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}   # annualized frac, by hour-bucket
    fund = json.loads((DATA / "eth_funding.json").read_text())
    fund.sort(key=lambda r: r["ts_ms"])
    f_ts = [r["ts_ms"] for r in fund]
    f_rt = [float(r["funding_rate"]) for r in fund]

    def fund_before(t):
        lo, hi = 0, len(f_ts) - 1
        if t < f_ts[0]:
            return 0.0
        while lo < hi:
            m = (lo + hi + 1) // 2
            if f_ts[m] <= t:
                lo = m
            else:
                hi = m - 1
        return f_rt[lo] / 8.0   # 8h -> per hour

    panel = []
    for c in klines:
        t = c["start_ms"]
        iv = iv_at.get(t // 3_600_000)
        if iv is None:
            continue
        panel.append({"ts": t, "close": float(c["close"]), "iv": iv, "fund_h": fund_before(t)})
    return panel


def efficiency_ratio(closes, i, n):
    if i < n:
        return None
    net = abs(closes[i] - closes[i - n])
    path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - n + 1, i + 1))
    return 0.0 if path == 0 else net / path


def run(panel, win=48, thr=1.5, hold=24, er_gate=0.40, vrp_gate=False, use_rv=False,
        fee_rt=0.0007, slip=0.0004, train_frac=0.65):
    closes = [p["close"] for p in panel]
    n = len(panel)
    # trailing realized vol (annualized) over `win` hours, causal
    logret = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]
    trades = []
    cooldown = -1
    for i in range(max(win, 168), n - hold):
        if i < cooldown:
            continue
        er = efficiency_ratio(closes, i, 48)
        if er is None or er > er_gate:        # range only
            continue
        anchor = st.fmean(closes[i - win:i])
        iv = panel[i]["iv"]
        rv_win = st.pstdev(logret[i - win:i]) * math.sqrt(8760)
        rv_168 = st.pstdev(logret[i - 168:i]) * math.sqrt(8760)
        vol = rv_win if use_rv else iv
        band = anchor * vol * math.sqrt(win / 8760.0)   # implied/realized $ move over win
        if band <= 0:
            continue
        z = (closes[i] - anchor) / band
        if abs(z) < thr:
            continue
        if vrp_gate and not (iv > rv_168):    # only fade when IV richer than RV
            continue
        side = -1 if z > 0 else +1            # fade the extension
        entry = closes[i]
        exit_ = closes[i + hold]
        gross = side * (exit_ / entry - 1.0)
        fund_pnl = -side * sum(panel[j]["fund_h"] for j in range(i, i + hold))
        net = gross + fund_pnl - fee_rt - slip
        trades.append({"i": i, "side": side, "net": net, "gross": gross, "z": z,
                       "vrp": iv - rv_168})
        cooldown = i + hold

    split = int(n * train_frac)
    return trades, split


def summ(label, ts):
    if not ts:
        print(f"  {label:9} n=0"); return
    nets = [t["net"] for t in ts]
    L = [t["net"] for t in ts if t["side"] > 0]
    S = [t["net"] for t in ts if t["side"] < 0]
    avg = st.fmean(nets) * 100
    tot = sum(nets) * 100
    wr = 100 * sum(x > 0 for x in nets) / len(nets)
    tstat = (st.fmean(nets) / st.pstdev(nets) * math.sqrt(len(nets))) if len(nets) > 1 and st.pstdev(nets) > 0 else 0
    la = st.fmean(L) * 100 if L else 0
    sa = st.fmean(S) * 100 if S else 0
    print(f"  {label:9} n={len(ts):4} | avg {avg:+.3f}% | tot {tot:+.1f}% | win {wr:.0f}% | "
          f"t {tstat:+.2f} | L {len(L)}@{la:+.2f}% S {len(S)}@{sa:+.2f}%")


def report(name, panel, split, **kw):
    trades, _ = run(panel, train_frac=split, **kw)
    sp = int(len(panel) * split)
    tr = [t for t in trades if t["i"] < sp]
    ho = [t for t in trades if t["i"] >= sp]
    print(f"\n### {name}")
    summ("ALL", trades); summ("TRAIN", tr); summ("HOLDOUT", ho)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", type=int, default=48, help="anchor/cone window (hrs)")
    ap.add_argument("--thr", type=float, default=1.5, help="extension threshold in implied-sigma")
    ap.add_argument("--hold", type=int, default=24)
    ap.add_argument("--er-gate", type=float, default=0.40)
    ap.add_argument("--fee-rt", type=float, default=0.0007)
    ap.add_argument("--slip", type=float, default=0.0004)
    ap.add_argument("--train-frac", type=float, default=0.65)
    args = ap.parse_args()

    panel = build_panel(args.win)
    t0 = dt.datetime.utcfromtimestamp(panel[0]["ts"] / 1000)
    t1 = dt.datetime.utcfromtimestamp(panel[-1]["ts"] / 1000)
    vrp_now = st.fmean([p["iv"] for p in panel[-168:]])
    print(f"Panel: {len(panel)} hrs {t0:%Y-%m-%d}..{t1:%Y-%m-%d} (klines x real Deribit DVOL)")
    print(f"Params: win={args.win}h thr={args.thr}sigma hold={args.hold}h er_gate={args.er_gate} "
          f"costs={100*(args.fee_rt+args.slip):.2f}%/rt | recent avg IV {vrp_now*100:.0f}%")
    common = dict(win=args.win, thr=args.thr, hold=args.hold, er_gate=args.er_gate,
                  fee_rt=args.fee_rt, slip=args.slip)
    report("(1) IV-cone fade, NO VRP gate", panel, args.train_frac, vrp_gate=False, use_rv=False, **common)
    report("(2) IV-cone fade, VRP>0 gate  [H2 thesis]", panel, args.train_frac, vrp_gate=True, use_rv=False, **common)
    report("(3) RV-cone fade [control]", panel, args.train_frac, vrp_gate=False, use_rv=True, **common)
    print("\nReal fade needs BOTH L and S positive AND train≈holdout. VRP gate should BEAT (1) if thesis holds.")


if __name__ == "__main__":
    main()

"""Standalone OOS edge test: Fair-Value-Gap (imbalance) retest on ETH, 1h, 1x.

Question (step 1, before any leverage or bot coupling): does entering on the retest
of an imbalance zone have ANY out-of-sample directional edge after costs?

FVG (3-candle imbalance) on 1h:
  bullish: low[i] > high[i-2]  -> demand gap, zone = [high[i-2], low[i]]
  bearish: high[i] < low[i-2]  -> supply gap, zone = [low[i-2], high[i]]
Continuation play (standard SMC): wait for price to retrace INTO the zone, enter in
the gap's direction (bull->LONG, bear->SHORT). Stop = far edge of zone (natural
invalidation, =1R). Target = R_mult * risk. Time-stop after HOLD bars. Net of round-
trip cost. Zone expires if not retested within VALID bars, or invalidated if price
closes through the far edge before retest.

HONEST: chronological train/holdout; params (min gap %, R-mult) locked on TRAIN and
read off HOLDOUT. Reported PER SIDE too — if only LONGs "work" it's just ETH beta in
an up-sample (the Put->long lesson), not a real imbalance edge. FADE variant printed
as a sanity check. Small param grid on purpose (FVG overfits easily).

Run:
  docker run --rm -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -v "$PWD/data:/root/Desktop/options/data" -w /app -e PYTHONPATH=/app \
    opt-app-bt:arm64 python3 services/imbalance_retest_backtest.py
"""
from __future__ import annotations

import os
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.backtest_data import load_local_set       # noqa: E402

DATA = Path(os.path.expanduser("~/Desktop/options/data"))
COST = 0.00075            # taker+slip per side -> 0.0015 round trip
VALID = 48               # bars to wait for a retest (48h)
HOLD = 24                # time-stop bars after entry


def find_zones(k, min_gap):
    """List of active FVG zones: (type, near, far, formed_idx). near=retest edge."""
    hi = [c["high"] for c in k]; lo = [c["low"] for c in k]; cl = [c["close"] for c in k]
    zones = []
    for i in range(2, len(k)):
        if lo[i] > hi[i - 2]:                                   # bullish gap
            gap = (lo[i] - hi[i - 2]) / cl[i]
            if gap >= min_gap:
                zones.append(("L", lo[i], hi[i - 2], i))        # near=top of gap, far=bottom (stop)
        elif hi[i] < lo[i - 2]:                                 # bearish gap
            gap = (lo[i - 2] - hi[i]) / cl[i]
            if gap >= min_gap:
                zones.append(("S", hi[i], lo[i - 2], i))        # near=bottom of gap, far=top (stop)
    return zones


def simulate(k, zones, r_mult, fade=False):
    hi = [c["high"] for c in k]; lo = [c["low"] for c in k]; cl = [c["close"] for c in k]
    trades = []
    for typ, near, far, fi in zones:
        side = typ
        if fade:
            side = "S" if typ == "L" else "L"
        # find retest within VALID bars, with no prior invalidation
        entry_idx = None
        for j in range(fi + 1, min(fi + 1 + VALID, len(k))):
            if typ == "L":
                if cl[j] < far:                                 # closed below stop edge -> dead
                    break
                if lo[j] <= near:                               # retraced into gap
                    entry_idx = j; break
            else:
                if cl[j] > far:
                    break
                if hi[j] >= near:
                    entry_idx = j; break
        if entry_idx is None:
            continue
        entry = near                                            # assume fill at zone edge
        d = 1 if side == "L" else -1
        # stop = far edge of the ORIGINAL zone; risk in price terms
        stop = far
        risk = abs(entry - stop) / entry
        if risk <= 0:
            continue
        target = entry * (1 + d * r_mult * risk)
        ret = None
        for j in range(entry_idx + 1, min(entry_idx + 1 + HOLD, len(k))):
            if d == 1:
                if lo[j] <= stop:  ret = -risk; break
                if hi[j] >= target: ret = r_mult * risk; break
            else:
                if hi[j] >= stop:  ret = -risk; break
                if lo[j] <= target: ret = r_mult * risk; break
        if ret is None:                                         # time-stop
            jx = min(entry_idx + HOLD, len(k) - 1)
            ret = d * (cl[jx] / entry - 1)
        trades.append({"idx": entry_idx, "side": side, "net": ret - 2 * COST})
    return trades


def summ(label, rows):
    if not rows:
        print(f"    {label:16} n=0"); return
    nets = [r["net"] * 100 for r in rows]
    wr = 100 * sum(n > 0 for n in nets) / len(rows)
    sd = st.pstdev(nets) if len(nets) > 1 else 0
    sharpe = (st.fmean(nets) / sd) if sd else 0
    print(f"    {label:16} n={len(rows):4} | avg {st.fmean(nets):+6.3f}% | total {sum(nets):+8.1f}% | "
          f"WR {wr:4.1f}% | exp/Sharpe {sharpe:+.3f}")


def report(k, split, r_mult, min_gap, fade=False):
    zones = find_zones(k, min_gap)
    trades = simulate(k, zones, r_mult, fade=fade)
    tr = [t for t in trades if t["idx"] < split]
    ho = [t for t in trades if t["idx"] >= split]
    tag = "FADE " if fade else ""
    print(f"  {tag}min_gap={min_gap*100:.1f}% R={r_mult}: zones={len(zones)} trades={len(trades)}")
    summ("TRAIN", tr); summ("  L", [t for t in tr if t["side"] == "L"]); summ("  S", [t for t in tr if t["side"] == "S"])
    summ("HOLDOUT", ho); summ("  L", [t for t in ho if t["side"] == "L"]); summ("  S", [t for t in ho if t["side"] == "S"])


def main():
    d = load_local_set(DATA); k = sorted(d["60"], key=lambda c: c["start_ms"])
    split = int(len(k) * 0.70)
    span_d = (k[-1]["start_ms"] - k[0]["start_ms"]) / 86_400_000
    print(f"ETH 1h imbalance-retest | {len(k)} bars (~{span_d:.0f}d) | train70/holdout30 split idx {split}")
    print(f"cost {2*COST*100:.2f}% round-trip | retest window {VALID}h | time-stop {HOLD}h\n")

    print("########## CONTINUATION (bull FVG->LONG, bear FVG->SHORT) ##########")
    for mg in (0.0, 0.002, 0.004):
        for rm in (1.5, 2.5):
            report(k, split, rm, mg)
            print()

    print("########## FADE sanity (reverse) min_gap=0.2% R=1.5 ##########")
    report(k, split, 1.5, 0.002, fade=True)

    print("\nEDGE only if HOLDOUT avg/exp > 0 on BOTH sides (not just LONG = beta), "
          "stable vs TRAIN, after the 0.15% cost. Otherwise imbalance-retest has no standalone edge.")


if __name__ == "__main__":
    main()

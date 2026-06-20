"""Monthly breakdown + leg split for the winning config found by
btc_straddle_sweep.py (cycle_h=24, tp1=0.50, tp2=0.80, sl=0.75, iv_rv_mult=1.10)
— sanity check against a single fat-tail month dominating the average before
trusting the sweep's holdout number.

Run: cd backend && python3 services/btc_straddle_winner_detail.py
"""
from __future__ import annotations

import statistics as st
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.btc_straddle_sweep import build_periodic_signals

COIN = sys.argv[1] if len(sys.argv) > 1 else "btc"
DAYS_BACK = float(sys.argv[2]) if len(sys.argv) > 2 else None
CYCLE_H, TP1, TP2, SL, MULT = 24.0, 0.50, 0.80, 0.75, 1.10
SIGMA_CLAMP = (0.20, 1.50)
SPREAD_PCT = 2.0
TRAIN_FRAC = 0.70


def mo(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def agg(pnls):
    if not pnls:
        return "  n   0"
    n = len(pnls)
    avg = sum(pnls) / n
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    worst = min(pnls)
    return f"n{n:>4} avg{avg:>+6.2f}% WR{wr*100:>3.0f}% Sh{sh:>+5.2f}  worst{worst:>+7.2f}%"


def main():
    k5, k15, k1h = load_coin(COIN, find_data_dir(None))
    if DAYS_BACK is not None:
        cutoff_ms = k5[-1]["start_ms"] - int(DAYS_BACK * 86_400_000)
        k5 = [c for c in k5 if c["start_ms"] >= cutoff_ms]
        k1h = [c for c in k1h if c["start_ms"] >= cutoff_ms]
    sigs = build_periodic_signals(k5, CYCLE_H)
    out = simulate_signal_set(
        sigs, k5, sigma=0.60, expiry_hours=CYCLE_H, tp1_pct=TP1, tp2_pct=TP2,
        sl_pct=SL, option_horizon_h=CYCLE_H, spread_pct=SPREAD_PCT,
        dynamic_sigma=True, klines_1h=k1h, iv_rv_multiplier=MULT,
        sigma_clamp=SIGMA_CLAMP,
    )
    by_cycle = {}
    for o in out:
        opt = o.get("option", {})
        if "pnl_pct" not in opt or opt.get("resolution") in ("no_entry", "no_data"):
            continue
        by_cycle.setdefault(o["_cycle"], {"ts_ms": o["ts_ms"]})[o["side"]] = opt["pnl_pct"]

    rows = []
    for c, d in sorted(by_cycle.items()):
        if "C" in d and "P" in d:
            rows.append((d["ts_ms"], (d["C"] + d["P"]) / 2, d["C"], d["P"]))

    print(f"BTC short straddle, cycle={CYCLE_H}h tp={TP1}/{TP2} sl={SL} mult={MULT} "
          f"(n={len(rows)} cycles)\n")

    pnls = [p for _, p, _, _ in rows]
    print(f"OVERALL: {agg(pnls)}")
    print(f"  call leg: {agg([c for *_, c, _ in rows])}")
    print(f"  put  leg: {agg([p for *_, _, p in rows])}")

    by_mo: "OrderedDict[str, list]" = OrderedDict()
    for ts, p, _, _ in rows:
        by_mo.setdefault(mo(ts), []).append(p)
    print("\nper-month:")
    for m in sorted(by_mo):
        print(f"  {m}: {agg(by_mo[m])}")

    ts_all = sorted(t for t, *_ in rows)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    tr = [p for t, p, _, _ in rows if t < split_ts]
    ho = [p for t, p, _, _ in rows if t >= split_ts]
    print(f"\nTRAIN   {agg(tr)}")
    print(f"HOLDOUT {agg(ho)}")

    # how much of the average comes from the single best month?
    month_avgs = {m: sum(v) / len(v) for m, v in by_mo.items()}
    best_month = max(month_avgs, key=month_avgs.get)
    rest = [p for ts, p, _, _ in rows if mo(ts) != best_month]
    print(f"\nbest single month: {best_month} (avg {month_avgs[best_month]:+.2f}%, "
          f"n={len(by_mo[best_month])})")
    print(f"  excluding it: {agg(rest)}")


if __name__ == "__main__":
    main()

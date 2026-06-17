"""Re-optimize exits for SHORT-dated (24h) expiry — goal: same/better per-trade
quality as the 7-day baseline, so the faster capital recycling lets the $400 account
execute more of the 1509 signals (throughput up, quality not down).

Discipline against in-sample mirage: sweep on TRAIN, print HOLDOUT side-by-side; only
a config strong on BOTH is acceptable. Put and Call optimized separately.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/iv_short_exit_opt.py
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.strategy_config import CALL_EXIT, PUT_EXIT
from services.variant_backtest import generate

EXPIRY_H = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
TRAIN_FRAC = 0.70
TP1 = [0.4, 0.6]
TP2 = [0.6, 0.8]
SL = [0.75, 1.25, 2.0]
HOLDF = [0.5, 0.75, 1.0]   # fraction of expiry


def metrics(rows):
    if not rows:
        return (0, 0.0, 0.0, 0.0)
    pnls = [r for r in rows]
    n = len(pnls)
    avg = sum(pnls) / n
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    return (n, avg, wr, sh)


def sim_side(sigs, k5, k1h, tp1, tp2, sl, hold_h):
    out = simulate_signal_set(sigs, k5, sigma=0.6, expiry_hours=EXPIRY_H,
            tp1_pct=tp1, tp2_pct=tp2, sl_pct=sl, option_horizon_h=hold_h,
            spread_pct=2.0, dynamic_sigma=True, klines_1h=k1h, iv_rv_multiplier=1.05)
    rows = []
    for s in out:
        o = s.get("option", {})
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        rows.append((int(s["ts_ms"]), o["pnl_pct"]))
    return rows


def split(rows, split_ts):
    tr = [p for ts, p in rows if ts < split_ts]
    ho = [p for ts, p in rows if ts >= split_ts]
    return tr, ho


def optimize_side(name, sigs, k5, k1h, split_ts, base_exit):
    print(f"\n================ {name} side ================")
    # baseline: current exits, hold capped to expiry
    b = sim_side(sigs, k5, k1h, base_exit["tp1_pct"], base_exit["tp2_pct"],
                 base_exit["sl_pct"], min(base_exit["hold_h"], EXPIRY_H))
    btr, bho = split(b, split_ts)
    print(f"{'config':<28} | {'TRAIN n/avg/WR/Sh':<30} | {'HOLDOUT n/avg/WR/Sh':<30}")
    def fmt(m):
        n, avg, wr, sh = m
        return f"{n:>4} {avg:>+6.2f} {wr*100:>4.0f}% {sh:>+5.3f}"
    print(f"{'BASELINE(7d-exits@24h)':<28} | {fmt(metrics(btr)):<30} | {fmt(metrics(bho)):<30}")

    results = []
    for tp1 in TP1:
        for tp2 in TP2:
            if tp2 <= tp1:
                continue
            for sl in SL:
                for hf in HOLDF:
                    hold = round(EXPIRY_H * hf)
                    rows = sim_side(sigs, k5, k1h, tp1, tp2, sl, hold)
                    tr, ho = split(rows, split_ts)
                    mt, mh = metrics(tr), metrics(ho)
                    results.append((tp1, tp2, sl, hold, mt, mh))
    # rank by TRAIN avg
    results.sort(key=lambda r: r[4][1], reverse=True)
    print("-- top 8 by TRAIN avg% (holdout shown for honesty) --")
    for tp1, tp2, sl, hold, mt, mh in results[:8]:
        cfg = f"tp{tp1}/{tp2} sl{sl} h{hold}"
        print(f"{cfg:<28} | {fmt(mt):<30} | {fmt(mh):<30}")
    # also the one best on HOLDOUT avg among the train-top-12 (robust pick)
    robust = max(results[:12], key=lambda r: r[5][1])
    tp1, tp2, sl, hold, mt, mh = robust
    print(f"-- robust pick (best holdout within train-top-12): "
          f"tp{tp1}/{tp2} sl{sl} h{hold}  TRAIN[{fmt(mt)}]  HOLDOUT[{fmt(mh)}]")
    return robust


def run(coin="eth"):
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    sigs = generate(k5, k15, k1h, variant="v3")
    p = [s for s in sigs if s["side"] == "P"]
    c = [s for s in sigs if s["side"] == "C"]
    ts_all = sorted(int(s["ts_ms"]) for s in sigs)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    print(f"{coin.upper()} @ {EXPIRY_H:.0f}h expiry — {len(p)} Put, {len(c)} Call signals; "
          f"train<{int(split_ts)}<=holdout")
    optimize_side("PUT", p, k5, k1h, split_ts, PUT_EXIT)
    optimize_side("CALL", c, k5, k1h, split_ts, CALL_EXIT)


if __name__ == "__main__":
    run(sys.argv[2] if len(sys.argv) > 2 else "eth")

"""§4.0 IV-richness entry filter — does selling premium ONLY when implied vol is
"rich" improve the existing Bybit ETH short-premium seller?

We have no separate market-IV series (that needs the forward collector), but the
variance-risk-premium mechanism is already in the sim: σ = trailing-168h realized
vol × 1.05 prices the option, and a short profits when the market then realizes
LESS than σ implied. So we test price-based RICHNESS PROXIES (no look-ahead, from
trailing 1h closes at signal time) as an entry gate:

  rv_short = realized_vol(closes,24)         # near-term vol
  rv_long  = realized_vol(closes,168)        # baseline vol (≈ sigma_used/1.05)
  backwardation = rv_short / rv_long         # >1 = near-term vol spiked = IV likely rich
  rv_pctile = percentile rank of rv_short in trailing 240h

Hypothesis: higher richness -> higher avg pnl_pct on the short. We (1) bucket trades
by each proxy and check monotonicity, (2) test a top-half richness GATE vs baseline,
reported on full / TRAIN(70%) / HOLDOUT(30%) to avoid threshold-overfitting.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/iv_richness_filter.py
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.indicators import realized_vol
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import dyn_sim_set, load_coin
from services.variant_backtest import generate

TRAIN_FRAC = 0.70


def i1h_at(k1h, ts_ms):
    lo, hi = 0, len(k1h)
    while lo < hi:
        mid = (lo + hi) // 2
        if k1h[mid]["start_ms"] + 3_600_000 <= ts_ms:
            lo = mid + 1
        else:
            hi = mid
    return lo


def richness_features(k1h, ts_ms):
    n = i1h_at(k1h, ts_ms)
    closes = [c["close"] for c in k1h[:n]]
    if len(closes) < 200:
        return None
    rv_s = realized_vol(closes, 24)
    rv_l = realized_vol(closes, 168)
    if not rv_s or not rv_l:
        return None
    back = rv_s / rv_l
    # percentile of current rv_short within trailing 240h of rolling rv_short
    window = closes[-(240 + 25):]
    roll = []
    for j in range(25, len(window)):
        v = realized_vol(window[:j + 1], 24)
        if v is not None:
            roll.append(v)
    pct = (sum(1 for v in roll if v <= rv_s) / len(roll)) if roll else 0.5
    return {"rv_short": rv_s, "rv_long": rv_l, "back": back, "pctile": pct}


def summarize(pnls):
    if not pnls:
        return (0, 0.0, 0.0, 0.0, 0.0)
    n = len(pnls)
    avg = sum(pnls) / n
    total = sum(pnls)
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    return (n, avg, total, wr, sh)


def line(label, pnls):
    n, avg, total, wr, sh = summarize(pnls)
    print(f"  {label:<26} n={n:>4}  avg={avg:>+6.2f}%  total={total:>+8.1f}  WR={wr*100:>4.1f}%  Sharpe={sh:>+5.3f}")


def buckets(label, rows, key, qs=(0.25, 0.5, 0.75)):
    vals = sorted(r[key] for r in rows)
    cuts = [vals[int(q * (len(vals) - 1))] for q in qs]
    groups = [[] for _ in range(len(qs) + 1)]
    for r in rows:
        v = r[key]
        idx = 0
        while idx < len(cuts) and v > cuts[idx]:
            idx += 1
        groups[idx].append(r["pnl"])
    print(f"\n{label}  (quartile buckets, cuts={[round(c,3) for c in cuts]})")
    names = ["Q1 lowest", "Q2", "Q3", "Q4 highest"]
    for nm, g in zip(names, groups):
        line(nm, g)


def gate_test(rows, key, thr_label, predicate, split_ts):
    base = [r["pnl"] for r in rows]
    kept = [r["pnl"] for r in rows if predicate(r)]
    tr_base = [r["pnl"] for r in rows if r["ts"] < split_ts]
    tr_kept = [r["pnl"] for r in rows if r["ts"] < split_ts and predicate(r)]
    ho_base = [r["pnl"] for r in rows if r["ts"] >= split_ts]
    ho_kept = [r["pnl"] for r in rows if r["ts"] >= split_ts and predicate(r)]
    print(f"\n=== GATE: {thr_label} ===")
    print("  FULL   "); line("baseline (all)", base); line("gated (rich only)", kept)
    print("  TRAIN  "); line("baseline (all)", tr_base); line("gated (rich only)", tr_kept)
    print("  HOLDOUT"); line("baseline (all)", ho_base); line("gated (rich only)", ho_kept)


def run(coin="eth"):
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    sigs = generate(k5, k15, k1h, variant="v3")
    sims = dyn_sim_set(sigs, k5, k1h)
    rows = []
    for s in sims:
        o = s.get("option", {})
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        f = richness_features(k1h, int(s["ts_ms"]))
        if f is None:
            continue
        rows.append({"ts": int(s["ts_ms"]), "pnl": o["pnl_pct"], "side": s["side"],
                     "sigma": s.get("sigma_used"), **f})
    rows.sort(key=lambda r: r["ts"])
    if not rows:
        print("no rows")
        return
    split_ts = rows[0]["ts"] + TRAIN_FRAC * (rows[-1]["ts"] - rows[0]["ts"])
    print(f"\n{coin.upper()} — {len(rows)} entered trades, split @70% time")
    print("Overall:")
    line("ALL entered", [r["pnl"] for r in rows])

    # 1) monotonicity by each proxy
    buckets("BACKWARDATION rv_short/rv_long  (higher = near-term vol spiked = IV richer)", rows, "back")
    buckets("RV-PERCENTILE rv_short rank (trailing 240h)", rows, "pctile")
    buckets("SIGMA_USED (premium level = rv_long)", rows, "sigma")

    # 2) gate tests (top-half on each proxy), train/holdout honest
    med_back = sorted(r["back"] for r in rows)[len(rows) // 2]
    gate_test(rows, "back", f"backwardation > median ({med_back:.3f})",
              lambda r: r["back"] > med_back, split_ts)
    gate_test(rows, "pctile", "rv-percentile > 0.60",
              lambda r: r["pctile"] > 0.60, split_ts)
    gate_test(rows, "back", "backwardation > 1.10 (clear near-term spike)",
              lambda r: r["back"] > 1.10, split_ts)

    # premium-floor gate — threshold fit on TRAIN ONLY, applied blind to holdout (no leak)
    train_sig = sorted(r["sigma"] for r in rows if r["ts"] < split_ts)
    sig_q1_tr = train_sig[len(train_sig) // 4]
    print(f"\n[premium floor fit on TRAIN ONLY: bottom-quartile σ cut = {sig_q1_tr:.3f}]")
    gate_test(rows, "sigma", f"sigma_used > {sig_q1_tr:.3f} (train-fit Q1) — drop cheapest-premium 25%",
              lambda r: r["sigma"] > sig_q1_tr, split_ts)


if __name__ == "__main__":
    run("eth")

"""ADX-score position sizing — out-of-sample evaluation on the CURRENT V2 trade set.

Why this exists: the older adx_hybrid_sweep.py scored an earlier (V3-era) signal
set and reported in-sample only. A sizing decision is an EDGE change, so per the
project's #1 lesson (overfit) it must be judged on a true holdout, on the trade
set the live V2 strategy actually produces.

Pipeline:
  1. Build the live V2 baseline trade set with variant_backtest.generate(...).
  2. Annotate each signal with its ADX readiness score at entry (compute_adx_score
     on the 1h history up to the signal timestamp). NOTE the score's polarity:
     HIGH score = low/falling ADX = range-like = good for selling premium.
  3. Simulate P/C with their real exits (variant_backtest.sim_set).
  4. Split chronologically into train (older) / holdout (last HOLDOUT_DAYS) via the
     strict holdout_split protocol — the holdout is never used to choose a winner.
  5. Apply each sizing rule and report train vs holdout edge, tail and trade count.

A rule is only worth deploying (AFTER the gate) if holdout edge >= baseline AND the
train->holdout gap is small AND worst-month / maxDD are not worse than baseline.

Run:  cd backend && PYTHONPATH=. python3 services/adx_sizing_oos.py
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

from services.adx_score import compute_adx_score
from services.holdout_split import (HOLDOUT_DAYS, holdout_cutoff_ms,
                                    split_signals_by_holdout)
from services.local_optimizer import find_data_dir, load_local
from services.variant_backtest import generate, sim_set

HISTORY = 240  # 1h bars of context for the ADX score (matches generate())


def annotate_adx_scores(sims: list[dict], k1h: list[dict]) -> None:
    """Attach `adx_score` to each sim from the 1h history up to its timestamp.
    Walks k1h once (sims sorted by ts_ms) so this is O(n)."""
    sims.sort(key=lambda s: s["ts_ms"])
    i1h = 0
    for s in sims:
        ts = int(s["ts_ms"])
        while i1h < len(k1h) and k1h[i1h]["start_ms"] + 3_600_000 <= ts:
            i1h += 1
        s1h = k1h[max(0, i1h - HISTORY):i1h]
        s["adx_score"] = compute_adx_score(s1h).get("score", 0.0) if s1h else 0.0


# ── sizing models ────────────────────────────────────────────────────────────
# Each is a callable score -> size multiplier. HIGH adx_score == range-like ==
# best premium decay, so multipliers rise with score. Discrete tiers are
# expressed as a callable too, for one uniform code path.

def _tiers(rules):
    """rules: list of (max_score_inclusive, multiplier), checked low->high."""
    def f(score):
        for max_sc, m in rules:
            if score <= max_sc:
                return m
        return rules[-1][1]
    return f


SIZING_MODELS = {
    "0 Baseline flat 1.0x":          lambda s: 1.0,
    "2-tier (FW§8) <=7:1.0 >7:1.5":  _tiers([(7, 1.0), (10, 1.5)]),
    "4-step A 0.5/1.0/1.25/1.5":     _tiers([(4, 0.5), (6, 1.0), (8, 1.25), (10, 1.5)]),
    "4-step B 0.75/1.0/1.25/1.5":    _tiers([(3, 0.75), (6, 1.0), (8, 1.25), (10, 1.5)]),
    "4-step aggressive 0.5..2.0":    _tiers([(4, 0.5), (6, 1.0), (8, 1.5), (10, 2.0)]),
    "continuous clamp(0.5+0.1*s)":   lambda s: max(0.5, min(1.5, 0.5 + 0.1 * s)),
}


def weighted_stats(sims: list[dict], size_fn) -> dict | None:
    """Portfolio stats applying size_fn(adx_score) as the per-trade size
    multiplier on a flat $100 base trade. avg_$ is the edge proxy."""
    dollars, pnls_pct, monthly = [], [], {}
    equity = 1000.0
    peak = equity
    max_dd = 0.0
    for s in sorted(sims, key=lambda x: x["ts_ms"]):
        opt = s.get("option", {})
        if "pnl_pct" not in opt:
            continue
        mult = size_fn(s.get("adx_score", 0.0))
        if mult <= 0.0:
            continue
        pnl = opt["pnl_pct"]
        pnls_pct.append(pnl)
        d = (pnl / 100.0) * (100.0 * mult)
        dollars.append(d)
        equity += d
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
        mo = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m")
        monthly.setdefault(mo, []).append(d)
    if not dollars:
        return None
    worst_month = min((sum(v) for v in monthly.values()), default=0.0)
    return {
        "n": len(dollars),
        "wr": sum(1 for p in pnls_pct if p > 0) / len(pnls_pct),
        "avg_$": statistics.mean(dollars),
        "total_$": sum(dollars),
        "worst_mo_$": worst_month,
        "max_dd": max_dd,
    }


def _row(name, st):
    if not st:
        return f"{name:<32} | no trades"
    return (f"{name:<32} | n={st['n']:>4} wr={st['wr']*100:>5.1f}% "
            f"avg=${st['avg_$']:>+6.2f} tot=${st['total_$']:>+8.1f} "
            f"worstMo=${st['worst_mo_$']:>+7.1f} DD={st['max_dd']:>4.1f}%")


def main():
    print("Loading data + building live-V2 trade set...", flush=True)
    k5, k15, k1h = load_local(find_data_dir(None))
    sigs = generate(k5, k15, k1h, variant="baseline")
    sims = sim_set(sigs, k5)
    sims = [s for s in sims if "pnl_pct" in s.get("option", {})]
    annotate_adx_scores(sims, k1h)

    cutoff = holdout_cutoff_ms(k5)
    train, holdout = split_signals_by_holdout(sims, cutoff)
    print(f"trades: total={len(sims)}  train={len(train)}  "
          f"holdout(last {HOLDOUT_DAYS}d)={len(holdout)}", flush=True)
    scs = [s["adx_score"] for s in sims]
    if scs:
        print(f"adx_score: min={min(scs):.1f} median={statistics.median(scs):.1f} "
              f"max={max(scs):.1f}", flush=True)

    for label, splitset in (("TRAIN", train), ("HOLDOUT", holdout), ("FULL", sims)):
        print(f"\n===== {label} =====", flush=True)
        base = weighted_stats(splitset, SIZING_MODELS["0 Baseline flat 1.0x"])
        for name, fn in SIZING_MODELS.items():
            st = weighted_stats(splitset, fn)
            tag = ""
            if st and base and name != "0 Baseline flat 1.0x":
                d = st["avg_$"] - base["avg_$"]
                tag = f"  Δavg={d:+.2f}"
            print(_row(name, st) + tag, flush=True)


if __name__ == "__main__":
    main()

"""Grogu (unconditional ETH straddle) — does ANY pre-cycle market metric predict
a bad cycle (SL-trip / bottom-quartile pnl%)? User's original idea: read funding,
OI, RSI divergence, vol-regime before each 24h cycle open and flag risk.

Reuses eth_straddle_sl_resweep.py's per-cycle simulator (frac=0.30, the
validated-optimal deployed setting) for outcomes, and perp_positioning_backtest.py's
load_series/at_or_before for funding/OI/long-short (causal, no lookahead).
RSI/realized-vol come from services.indicators (full price-history coverage).

Data coverage is NOT uniform — funding ~2mo, OI ~4.5mo, long/short ~7mo (per-user
decision: use what we have rather than backfill from Bybit). Each metric is
therefore tested only on the cycles that fall within ITS OWN window — n differs
per metric and is reported. RSI/vol-regime use full 1y "eth" history.

Methodology: for each metric, find a TRAIN-only threshold splitting cycles into
"flagged" vs "normal", compare bad-cycle-rate (SL-trip or bottom-quartile pnl%)
in each bucket on TRAIN, then check the SAME threshold on HOLDOUT — no peeking.

Run: cd backend && PYTHONPATH=. python3 services/eth_straddle_market_metrics_test.py
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs                          # noqa: E402
from services import indicators                                  # noqa: E402
from services.local_optimizer import find_data_dir                # noqa: E402
from services.multi_coin_signals import load_coin                 # noqa: E402
from services.btc_straddle_sweep import build_periodic_signals    # noqa: E402
from services.btc_straddle_dollar_stop import (                   # noqa: E402
    trailing_sigma, nearest_1h_idx, CYCLE_H, TP2, HALF_SPREAD, IM_RATE,
    STRIKE_ROUND_BY_COIN, LOT_BY_COIN,
)
from services.eth_straddle_sl_resweep import simulate_leg_full     # noqa: E402
from services.perp_positioning_backtest import load_series, at_or_before, DATA  # noqa: E402

COIN = "eth"  # 1y window — best overlap with the funding/OI/long-short coverage
FRAC = 0.30   # validated-optimal deployed SL_DOLLAR_FRAC (see eth_straddle_sl_resweep.py)
TRAIN_FRAC = 0.70


def build_cycle_outcomes():
    k5, k15, k1h = load_coin(COIN, find_data_dir(None))
    k1h = sorted(k1h, key=lambda c: c["start_ms"])
    sigs = build_periodic_signals(k5, CYCLE_H)
    cycles_by_idx = {}
    for s in sigs:
        cycles_by_idx.setdefault(s["_cycle"], {})[s["side"]] = s["idx_5m"]

    rows = []  # dicts: ts, pnl_pct (of margin), any_sl
    for cycle_idx, legs in sorted(cycles_by_idx.items()):
        if "C" not in legs or "P" not in legs:
            continue
        idx_1h = nearest_1h_idx(k1h, k5[cycle_idx]["start_ms"])
        if idx_1h is None:
            continue
        sigma = trailing_sigma(k1h, idx_1h)
        if sigma is None:
            continue
        legres = {}
        ok = True
        for side in ("C", "P"):
            r = simulate_leg_full(side, legs[side], k5, sigma, FRAC)
            if r is None:
                ok = False
                break
            legres[side] = r
        if not ok:
            continue
        tot_pnl = legres["C"]["pnl_dollars"] + legres["P"]["pnl_dollars"]
        tot_margin = legres["C"]["margin"] + legres["P"]["margin"]
        pct = tot_pnl / tot_margin * 100 if tot_margin else 0.0
        any_sl = legres["C"]["resolution"] == "sl_dollar" or legres["P"]["resolution"] == "sl_dollar"
        rows.append({"ts": k5[cycle_idx]["start_ms"], "idx_1h": idx_1h, "pnl_pct": pct, "any_sl": any_sl})
    return rows, k1h


def main():
    print("[1] building Grogu cycle outcomes (1y, frac=0.30)...")
    rows, k1h = build_cycle_outcomes()
    rows.sort(key=lambda r: r["ts"])
    n = len(rows)
    print(f"    {n} cycles")
    bad_cut = sorted(r["pnl_pct"] for r in rows)[max(0, n // 4 - 1)]
    for r in rows:
        r["bad"] = r["any_sl"] or (r["pnl_pct"] <= bad_cut)
    print(f"    bad-cycle rate overall: {sum(r['bad'] for r in rows)/n*100:.1f}% "
          f"(SL-trip OR bottom-quartile pnl%)")

    split_ts = rows[0]["ts"] + TRAIN_FRAC * (rows[-1]["ts"] - rows[0]["ts"])

    closes_1h = [float(c["close"]) for c in k1h]

    # ---- causal market-metric series ----
    oi = load_series("eth_oi.json", "open_interest")
    lsr = load_series("eth_long_short.json", "long_short_ratio")
    fund = load_series("eth_funding.json", "funding_rate")

    DAY = 86_400_000

    def feat_rsi(row):
        i = row["idx_1h"]
        if i < 20:
            return None
        return indicators.rsi(closes_1h[max(0, i - 60):i + 1], period=14)

    def feat_vol_regime(row):
        i = row["idx_1h"]
        rv24 = indicators.realized_vol(closes_1h[max(0, i - 24):i + 1], lookback=24)
        rv168 = indicators.realized_vol(closes_1h[max(0, i - 168):i + 1], lookback=168)
        if rv24 is None or rv168 is None or rv168 == 0:
            return None
        return rv24 / rv168  # >1 = vol expanding vs trailing week, <1 = compressed

    def feat_funding(row):
        f = at_or_before(fund, row["ts"])
        return abs(f) if f is not None else None  # extremity, sign-agnostic

    def feat_oi_delta(row):
        now = at_or_before(oi, row["ts"])
        prev = at_or_before(oi, row["ts"] - DAY)
        if now is None or prev is None or prev == 0:
            return None
        return (now - prev) / prev * 100  # 24h % change

    def feat_lsr_extreme(row):
        v = at_or_before(lsr, row["ts"])
        return abs(v - 1.0) if v is not None else None  # deviation from balanced 1.0

    metrics = {
        "RSI(14) extremity (|50-RSI|)": lambda r: (abs(50 - feat_rsi(r)) if feat_rsi(r) is not None else None),
        "Vol-regime jump (RV24/RV168)": feat_vol_regime,
        "Funding rate |extremity|":     feat_funding,
        "OI 24h %Δ":                    feat_oi_delta,
        "Long/short ratio extremity":   feat_lsr_extreme,
    }

    print(f"\n{'metric':<32}{'n':>6}{'train n':>9}{'hold n':>8}{'thr(p75)':>10}"
          f"{'TRAIN flagged-bad%':>20}{'TRAIN normal-bad%':>19}{'HOLD flagged-bad%':>19}{'HOLD normal-bad%':>18}")
    for name, fn in metrics.items():
        vals = [(r["ts"], fn(r), r["bad"]) for r in rows]
        vals = [(t, v, b) for t, v, b in vals if v is not None]
        if len(vals) < 20:
            print(f"{name:<32}{'insufficient data (n=' + str(len(vals)) + ')':>70}")
            continue
        train = [(t, v, b) for t, v, b in vals if t < split_ts]
        hold = [(t, v, b) for t, v, b in vals if t >= split_ts]
        if len(train) < 10 or len(hold) < 5:
            print(f"{name:<32}{'insufficient train/hold split':>70}")
            continue
        thr = sorted(v for _, v, _ in train)[int(len(train) * 0.75)]

        def bad_rate(subset, flagged: bool):
            sel = [b for _, v, b in subset if (v >= thr) == flagged]
            return (sum(sel) / len(sel) * 100, len(sel)) if sel else (float("nan"), 0)

        tr_f, ntf = bad_rate(train, True)
        tr_n, ntn = bad_rate(train, False)
        ho_f, nhf = bad_rate(hold, True)
        ho_n, nhn = bad_rate(hold, False)
        print(f"{name:<32}{len(vals):>6}{len(train):>9}{len(hold):>8}{thr:>10.3f}"
              f"{tr_f:>17.1f}%({ntf})  {tr_n:>14.1f}%({ntn})  {ho_f:>14.1f}%({nhf})  {ho_n:>13.1f}%({nhn})")

    print("\nflagged = metric >= TRAIN p75 threshold. If flagged-bad% is consistently")
    print("higher than normal-bad% on BOTH train and holdout, the metric has signal.")
    print("If it flips sign or holdout shows no gap, it's noise — don't deploy it.")


if __name__ == "__main__":
    main()

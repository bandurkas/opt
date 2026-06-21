"""Gold (XAUT) macro-trend FILTER test — the "possible future" idea noted at the
bottom of GOLD_OPTIONS_INTEGRATION.md: only sell Puts when gold is in a confirmed
long-term uptrend, sit out otherwise, instead of selling Puts unconditionally
(which inverted to -24.9%/trade in the 2026-03 correction, REJECTED, see
project_options_gold_rejected). gold_oos_regime.py already REPORTS a 30d trend
split; this harness actually APPLIES the filter as an entry gate (drops signals
where trend is down) across several trend-lookback windows, and re-measures
TRAIN/HOLDOUT.

Known caveat (documented, not fixable with this data): our 365d XAUT window
contains exactly ONE correction (2026-03→06, -27.6%). Any trend-filter result
here is fit to n=1 regime — informative for "does removing the down-leg even
help," but NOT a deploy-ready validation. Real validation needs gold bear/sideways
data (2013-15, 2020-22) we don't have.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/gold_trend_filter_backtest.py
"""
from __future__ import annotations

import bisect
import statistics as st
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set  # noqa: E402
from services.local_optimizer import find_data_dir  # noqa: E402
from services.multi_coin_signals import load_coin  # noqa: E402
from services.strategy_config import PUT_EXIT  # noqa: E402

COIN = "xaut"
EXPIRY_H = 168.0
TRAIN_FRAC = 0.70
TREND_WINDOWS_H = [24 * 7, 24 * 14, 24 * 30, 24 * 60, 24 * 90]  # 7d..90d lookback


def mo(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def agg(pnls):
    if not pnls:
        return "  n   0"
    n = len(pnls)
    avg = sum(pnls) / n
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    return f"n{n:>4} avg{avg:>+6.2f}% WR{wr*100:>3.0f}% Sh{sh:>+5.2f}"


def sim_side(sigs, k5, k1h, ex):
    out = simulate_signal_set(sigs, k5, sigma=0.6, expiry_hours=EXPIRY_H,
            tp1_pct=ex["tp1_pct"], tp2_pct=ex["tp2_pct"], sl_pct=ex["sl_pct"],
            option_horizon_h=ex["hold_h"], spread_pct=2.0, dynamic_sigma=True,
            klines_1h=k1h, iv_rv_multiplier=1.05)
    rows = []
    for s in out:
        o = s.get("option", {})
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        rows.append((int(s["ts_ms"]), o["pnl_pct"]))
    return rows


def main():
    from services.variant_backtest import generate
    k5, k15, k1h = load_coin(COIN, find_data_dir(None))
    sigs_all = generate(k5, k15, k1h, variant="v3")
    put_sigs = [s for s in sigs_all if s["side"] == "P"]

    cl = sorted((int(c["start_ms"]), float(c["close"])) for c in k1h)
    ts_arr = [t for t, _ in cl]
    px_arr = [p for _, p in cl]

    ts_all = sorted(int(s["ts_ms"]) for s in put_sigs)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    split_lbl = mo(int(split_ts))

    print(f"XAUT Put-side macro-trend FILTER test — TRAIN < {split_lbl} <= HOLDOUT\n"
          f"CAVEAT: only 1 correction in sample (2026-03..06) -> any filter result here "
          f"is fit to n=1 regime, not a real OOS validation.\n")

    print("=== BASELINE: no trend filter (all Put signals) ===")
    rows = sim_side(put_sigs, k5, k1h, PUT_EXIT)
    tr = [p for ts, p in rows if ts < split_ts]
    ho = [p for ts, p in rows if ts >= split_ts]
    print(f"  ALL    : {agg([p for _, p in rows])}")
    print(f"  TRAIN  : {agg(tr)}")
    print(f"  HOLDOUT: {agg(ho)}\n")

    for window_h in TREND_WINDOWS_H:
        step_bars = window_h  # k1h is hourly bars

        def trend_up_at(ts_ms: int, sb=step_bars) -> bool:
            i = bisect.bisect_right(ts_arr, ts_ms) - 1
            if i < sb:
                return True
            return px_arr[i] >= px_arr[i - sb]

        filt_sigs = [s for s in put_sigs if trend_up_at(int(s["ts_ms"]))]
        dropped = len(put_sigs) - len(filt_sigs)
        rows_f = sim_side(filt_sigs, k5, k1h, PUT_EXIT)
        tr_f = [p for ts, p in rows_f if ts < split_ts]
        ho_f = [p for ts, p in rows_f if ts >= split_ts]

        print(f"=== FILTER: trend-up over trailing {window_h//24}d "
              f"(dropped {dropped}/{len(put_sigs)} signals) ===")
        print(f"  ALL    : {agg([p for _, p in rows_f])}")
        print(f"  TRAIN  : {agg(tr_f)}")
        print(f"  HOLDOUT: {agg(ho_f)}")
        by_mo: "OrderedDict[str, list]" = OrderedDict()
        for ts, p in rows_f:
            by_mo.setdefault(mo(ts), []).append(p)
        print("  per-month:")
        for m in sorted(by_mo):
            print(f"    {m}: {agg(by_mo[m])}")
        print()


if __name__ == "__main__":
    main()

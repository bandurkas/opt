"""Gold (XAUT) honest-IV strategy — OOS / out-of-bull regime test.

The decisive gate (GOLD_OPTIONS_INTEGRATION.md §2.3): gold's full-period edge is
~all Put-side (+28%) and gold rose all sample — is that a real vol-edge or just
trend-capture (= a hidden leveraged-long via naked put selling)? Our 365d XAUT
window happens to contain a REAL −27.6% gold correction (2026-03→06), so we can
test whether the Put edge survives a genuine gold drawdown instead of guessing.

Reports, per side (P/C), with honest σ (168h RV×1.05) at the deployed exits:
  1) per-MONTH avg %/trade  → see exactly where the edge lives,
  2) chronological TRAIN(early bull) vs HOLDOUT(late correction) split,
  3) split by UNDERLYING regime (gold 30d trend up vs flat/down at entry).

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/gold_oos_regime.py
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
from services.strategy_config import CALL_EXIT, PUT_EXIT

COIN = sys.argv[1] if len(sys.argv) > 1 else "xaut"
EXPIRY_H = 168.0
TRAIN_FRAC = 0.70


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
    sigs = generate(k5, k15, k1h, variant="v3")

    # underlying 30d (720h) trend at each 1h bar, for regime classification
    cl = sorted((int(c["start_ms"]), float(c["close"])) for c in k1h)
    ts_arr = [t for t, _ in cl]
    px_arr = [p for _, p in cl]
    import bisect

    def trend_up_at(ts_ms: int) -> bool:
        i = bisect.bisect_right(ts_arr, ts_ms) - 1
        if i < 720:
            return True  # not enough history → treat as up (bull start)
        return px_arr[i] >= px_arr[i - 720]

    ts_all = sorted(int(s["ts_ms"]) for s in sigs)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    split_lbl = mo(int(split_ts))
    print(f"{COIN.upper()} @168h honest σ — {len(sigs)} sigs; "
          f"TRAIN < {split_lbl} <= HOLDOUT  (PUT_EXIT={PUT_EXIT}, CALL_EXIT={CALL_EXIT})\n")

    for side, ex in (("P", PUT_EXIT), ("C", CALL_EXIT)):
        ss = [s for s in sigs if s["side"] == side]
        rows = sim_side(ss, k5, k1h, ex)
        print(f"================ {side} side  (n={len(rows)}) ================")
        # per-month
        by_mo: "OrderedDict[str,list]" = OrderedDict()
        for ts, p in rows:
            by_mo.setdefault(mo(ts), []).append(p)
        print("  per-month:")
        for m in sorted(by_mo):
            print(f"    {m}: {agg(by_mo[m])}")
        # train/holdout
        tr = [p for ts, p in rows if ts < split_ts]
        ho = [p for ts, p in rows if ts >= split_ts]
        print(f"  TRAIN(bull) : {agg(tr)}")
        print(f"  HOLDOUT(corr): {agg(ho)}")
        # underlying-regime split
        up = [p for ts, p in rows if trend_up_at(ts)]
        dn = [p for ts, p in rows if not trend_up_at(ts)]
        print(f"  gold 30d-UP : {agg(up)}")
        print(f"  gold 30d-FLAT/DOWN: {agg(dn)}\n")


if __name__ == "__main__":
    main()

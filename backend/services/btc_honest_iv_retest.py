"""Re-check BTC with honest per-asset IV, using the CURRENT (live) V3 strategy
config — not a frozen snapshot. The original multi-coin research (2026-06-17,
project_options_multicoin_research.md) found BTC's "+8%" was a mispricing
artifact of constant sigma=0.6; with honest dynamic_sigma it dropped to -0.81%
sharpe -0.01, and was rejected.

Since then strategy_config.py changed twice in ways that import live into that
same harness (multi_coin_signals.py pulls PUT_EXIT/CALL_EXIT at import time,
not a frozen copy):
  - 2026-06-17: CALL_EXIT moved to short-dated 24h (tp 0.40/0.80, sl 0.75)
  - 2026-06-19: PUT_EXIT sl widened 1.50->2.00 (ETH-validated, never checked on BTC)

This redoes the BTC-only honest-IV backtest with TRAIN/HOLDOUT split (the
original test was full-sample only) to see if BTC's verdict changes under the
exits BTC was never actually tested against.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/btc_honest_iv_retest.py
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

TRAIN_FRAC = 0.70
IV_RV_MULT = 1.05


def sim(sigs, k5, k1h):
    p = [s for s in sigs if s["side"] == "P"]
    c = [s for s in sigs if s["side"] == "C"]
    common = dict(sigma=0.6, expiry_hours=168.0, spread_pct=2.0,
                  dynamic_sigma=True, klines_1h=k1h, iv_rv_multiplier=IV_RV_MULT)
    ps = simulate_signal_set(p, k5, tp1_pct=PUT_EXIT["tp1_pct"], tp2_pct=PUT_EXIT["tp2_pct"],
            sl_pct=PUT_EXIT["sl_pct"], option_horizon_h=PUT_EXIT["hold_h"], **common) if p else []
    cs = simulate_signal_set(c, k5, tp1_pct=CALL_EXIT["tp1_pct"], tp2_pct=CALL_EXIT["tp2_pct"],
            sl_pct=CALL_EXIT["sl_pct"], option_horizon_h=CALL_EXIT["hold_h"], **common) if c else []
    rows = []
    for s in ps + cs:
        o = s.get("option", {})
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        rows.append((int(s["ts_ms"]), s["side"], o["pnl_pct"]))
    return rows


def agg(rows):
    pnls = [p for _, _, p in rows]
    if not pnls:
        return "n   0"
    n = len(pnls)
    avg = sum(pnls) / n
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    return f"n{n:>4} avg{avg:>+6.2f}% WR{wr*100:>3.0f}% Sh{sh:>+5.2f}"


def main():
    coin = "btc"
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    sigs = generate(k5, k15, k1h, variant="v3")
    rows = sim(sigs, k5, k1h)

    print(f"BTC honest-IV retest — CURRENT live exits "
          f"(PUT sl={PUT_EXIT['sl_pct']} hold={PUT_EXIT['hold_h']}h, "
          f"CALL sl={CALL_EXIT['sl_pct']} hold={CALL_EXIT['hold_h']}h)\n")

    print(f"OVERALL: {agg(rows)}")
    for side in ("P", "C"):
        side_rows = [r for r in rows if r[1] == side]
        print(f"  {side}-side only: {agg(side_rows)}")

    if not rows:
        return
    ts_all = sorted(t for t, _, _ in rows)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    tr = [r for r in rows if r[0] < split_ts]
    ho = [r for r in rows if r[0] >= split_ts]
    print(f"\nTRAIN   {agg(tr)}")
    print(f"HOLDOUT {agg(ho)}")
    for side in ("P", "C"):
        tr_s = [r for r in tr if r[1] == side]
        ho_s = [r for r in ho if r[1] == side]
        print(f"  {side}: TRAIN {agg(tr_s)}  |  HOLDOUT {agg(ho_s)}")


if __name__ == "__main__":
    main()

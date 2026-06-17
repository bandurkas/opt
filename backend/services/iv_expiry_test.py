"""Short-dated vs 7-day expiry test for the ETH premium seller.

Screenshot insight: Bybit lists LIQUID daily ETH expiries (18h to expiry, tight ATM
spreads). Our bot prices a synthetic 168h (7-day) option. Short-dated options carry
the steepest theta/day and historically the fattest variance-risk-premium. Hold the
SAME V3 signal set constant and only vary expiry_hours -> compare edge PER DAY and
per unit time, to see if the daily expiries are the better instrument.

NOTE: exit params (TP/SL) are 7-day-tuned; this is a first directional read, not a
re-optimized short-dated strategy. option_horizon_h capped at the expiry.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/iv_expiry_test.py
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


def sim_at(sigs, k5, k1h, expiry_h):
    p = [s for s in sigs if s["side"] == "P"]
    c = [s for s in sigs if s["side"] == "C"]
    common = dict(sigma=0.6, expiry_hours=expiry_h, spread_pct=2.0,
                  dynamic_sigma=True, klines_1h=k1h, iv_rv_multiplier=1.05)
    ps = simulate_signal_set(p, k5, tp1_pct=PUT_EXIT["tp1_pct"], tp2_pct=PUT_EXIT["tp2_pct"],
            sl_pct=PUT_EXIT["sl_pct"], option_horizon_h=min(PUT_EXIT["hold_h"], expiry_h), **common) if p else []
    cs = simulate_signal_set(c, k5, tp1_pct=CALL_EXIT["tp1_pct"], tp2_pct=CALL_EXIT["tp2_pct"],
            sl_pct=CALL_EXIT["sl_pct"], option_horizon_h=min(CALL_EXIT["hold_h"], expiry_h), **common) if c else []
    return ps + cs


def run(coin="eth"):
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    sigs = generate(k5, k15, k1h, variant="v3")
    print(f"\n{coin.upper()} — same {len(sigs)} V3 signals, only expiry varied")
    print(f"{'expiry_h':>8} {'n':>5} {'avg%':>7} {'WR':>6} {'Sharpe':>7} "
          f"{'avgDaysHeld':>11} {'%/day':>7} {'res mix (tp/sl/time)':>22}")
    for eh in (24, 48, 72, 120, 168):
        sims = sim_at(sigs, k5, k1h, eh)
        pnls, bars, res = [], [], {}
        for s in sims:
            o = s.get("option", {})
            if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
                continue
            pnls.append(o["pnl_pct"])
            bars.append(o.get("bars_held", 0))
            r = o.get("resolution", "?")
            tag = "tp" if r.startswith("tp") else ("sl" if r in ("sl", "tsl") else "time")
            res[tag] = res.get(tag, 0) + 1
        if not pnls:
            continue
        n = len(pnls)
        avg = sum(pnls) / n
        wr = sum(1 for p in pnls if p > 0) / n
        sd = st.stdev(pnls) if n > 1 else 0.0
        sh = avg / sd if sd > 0 else 0.0
        days = (sum(bars) / len(bars)) * 5 / 60 / 24
        perday = avg / days if days > 0 else 0.0
        mix = f"{res.get('tp',0)}/{res.get('sl',0)}/{res.get('time',0)}"
        print(f"{eh:>8} {n:>5} {avg:>+7.2f} {wr*100:>5.1f}% {sh:>+7.3f} "
              f"{days:>11.2f} {perday:>+7.2f} {mix:>22}")


if __name__ == "__main__":
    run("eth")

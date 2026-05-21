"""Sensitivity test for iter4 winners.

Tests how the 3 winning combos hold up across:
  - sigma in [0.4, 0.6, 0.8]
  - spread_pct in [1, 2, 4]

= 9 (sigma, spread) cells per combo × 3 combos = 27 runs.

If a combo stays test_avg > 1.0 in 7+ of 9 cells → real edge confirmed.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.backtest_data import fetch_set
from services.strategy_registry import gen_sell_premium_iv_high
from services.strategy_sweep import evaluate_signals


# The 3 iter4 winners
WINNERS = [
    {
        "label": "P_mtfup.cd6.range.decay_48h_wide_sl",
        "gen_kwargs": {
            "vol_threshold": 0.5,
            "regime_filter": ["range"],
            "side": "P",
            "adx_max": None,
            "mtf_direction_filter": "up",
            "cooldown_bars": 6,
        },
        "exit": {"tp1": 0.40, "tp2": 0.60, "sl": 1.00, "hold_h": 48,
                 "tsl_t": 0.0, "tsl_o": 0.0},
    },
    {
        "label": "C_mtfdown.cd6.range+transition.decay_24h",
        "gen_kwargs": {
            "vol_threshold": 0.7,
            "regime_filter": ["range", "transition"],
            "side": "C",
            "adx_max": None,
            "mtf_direction_filter": "down",
            "cooldown_bars": 6,
        },
        "exit": {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24,
                 "tsl_t": 0.0, "tsl_o": 0.0},
    },
    {
        "label": "C_mtfdown.cd12.range+transition.decay_24h",
        "gen_kwargs": {
            "vol_threshold": 0.7,
            "regime_filter": ["range", "transition"],
            "side": "C",
            "adx_max": None,
            "mtf_direction_filter": "down",
            "cooldown_bars": 12,
        },
        "exit": {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24,
                 "tsl_t": 0.0, "tsl_o": 0.0},
    },
]

SIGMAS = [0.4, 0.6, 0.8]
SPREADS = [1.0, 2.0, 4.0]


def main():
    days = 365
    print(f"=== Sensitivity test: 365d, 3 winners × 9 (sigma, spread) cells ===")
    t0 = time.time()

    print("\n[1] Fetching klines...")
    data = fetch_set("ETHUSDT", days=days, intervals=("5", "15", "60"))
    k5, k15, k1h = data["5"], data["15"], data["60"]
    print(f"  5m={len(k5)}, 15m={len(k15)}, 1h={len(k1h)}")

    out = []
    for winner in WINNERS:
        print(f"\n[2] {winner['label']}")
        signals = gen_sell_premium_iv_high(k5, k15, k1h, **winner["gen_kwargs"])
        print(f"   signals: {len(signals)}")
        for sigma in SIGMAS:
            for spread in SPREADS:
                ex = winner["exit"]
                res = evaluate_signals(
                    signals, k5,
                    sigma=sigma, expiry_h=168.0,
                    tp1=ex["tp1"], tp2=ex["tp2"], sl=ex["sl"],
                    hold_h=ex["hold_h"], spread=spread,
                    tsl_t=ex["tsl_t"], tsl_o=ex["tsl_o"],
                )
                te = res["test"]
                tr = res["train"]
                line = {
                    "label": winner["label"],
                    "sigma": sigma, "spread": spread,
                    "train_avg": tr["avg"] if tr else None,
                    "test_avg": te["avg"] if te else None,
                    "test_n": te["n"] if te else 0,
                    "test_sharpe": te.get("sharpe") if te else None,
                    "test_wr": te.get("wr") if te else None,
                }
                out.append(line)
                te_avg = te["avg"] if te else None
                te_sh = te.get("sharpe") if te else None
                print(f"   sigma={sigma} spread={spread}%  "
                      f"train={tr['avg'] if tr else 'n/a':+.2f} "
                      f"test={te_avg:+.2f} n={te['n'] if te else 0} sh={te_sh:+.2f}",
                      flush=True)

    print(f"\n=== Pass-fail table (test_avg > 1.0) ===")
    for winner in WINNERS:
        rows = [r for r in out if r["label"] == winner["label"]]
        passes = sum(1 for r in rows if (r["test_avg"] or -999) > 1.0)
        print(f"  {winner['label']:<55} {passes}/9 pass")

    elapsed = round(time.time() - t0, 1)
    print(f"\nTotal: {len(out)} cells in {elapsed}s")
    with open("/tmp/sensitivity_test.json", "w") as f:
        json.dump({"days": days, "results": out, "elapsed_s": elapsed}, f, indent=2)


if __name__ == "__main__":
    main()

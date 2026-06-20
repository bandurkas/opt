#!/usr/bin/env python3
"""Iteration 5 — take MORE of the SAME baseline signals without adding leverage.

iter4 raised MAX_OPEN at CONSTANT per-trade size => doubled total exposure => margin
exhaustion + correlated-tail maxDD blowup. Wrong knob. The right one (user's intuition):
raise MAX_OPEN AND shrink per-trade MARGIN_PCT together, holding total deployed (MO x MP)
~constant. More, SMALLER, temporally-spread positions of the SAME high-quality signals.

Baseline uses MO=4 x MP=0.15 = 60% max deployed (under the 80% PORT cap) -> slots, not
margin, are the binding limit (cap=796 blocked). We sweep (MO, MP) pairs and read FINAL $,
maxDD, taken. Win = more taken + FINAL $ >= baseline + maxDD not worse.

Real DVOL pricing, live exits, baseline gate (P0.50/C0.60). Generate+build ONCE; replay.
Run:  PYTHONPATH=. python3 services/slots_size_sweep.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import services.variant_backtest as vb
import services.iv_mixed_deposit as dep
from services.iv_mixed_deposit import build_trades
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
import services.indicators as ind
from services.strategy_config import PUT_EXIT, CALL_EXIT

_orig_rv = ind.realized_vol_at_idx_1h
DVOL_IV = []


def _patched_rv(closes, i, lookback_h=168):
    if 0 <= i < len(DVOL_IV) and DVOL_IV[i] is not None:
        return DVOL_IV[i]
    return _orig_rv(closes, i, lookback_h)


CFG = {
    "P": {"tp1": PUT_EXIT["tp1_pct"], "tp2": PUT_EXIT["tp2_pct"], "sl": PUT_EXIT["sl_pct"],
          "hold": PUT_EXIT["hold_h"], "expiry": 168.0},
    "C": {"tp1": CALL_EXIT["tp1_pct"], "tp2": CALL_EXIT["tp2_pct"], "sl": CALL_EXIT["sl_pct"],
          "hold": CALL_EXIT["hold_h"], "expiry": 24.0},
}

# (label, MAX_OPEN, MARGIN_PCT) — last column = MO*MP = max % equity deployed
CONFIGS = [
    ("BASE   MO4  MP.150  (60%)", 4, 0.150),
    ("A      MO6  MP.100  (60%)", 6, 0.100),
    ("B      MO8  MP.075  (60%)", 8, 0.075),
    ("C      MO8  MP.100  (80%)", 8, 0.100),
    ("D      MO10 MP.060  (60%)", 10, 0.060),
    ("E      MO12 MP.050  (60%)", 12, 0.050),
    ("F      MO12 MP.066  (80%)", 12, 0.066),
    ("G      MO16 MP.050  (80%)", 16, 0.050),
]


def main():
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_coin("eth", data_dir)
    k1h = sorted(k1h, key=lambda c: c["start_ms"])
    dvol = json.loads((Path(data_dir) / "eth_dvol_1h.json").read_text())
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    global DVOL_IV
    DVOL_IV = [iv_at.get(c["start_ms"] // 3_600_000) for c in k1h]
    ind.realized_vol_at_idx_1h = _patched_rv

    vb.PUT_GEN_KWARGS = {**vb.PUT_GEN_KWARGS, "vol_threshold": 0.50}
    vb.CALL_GEN_KWARGS = {**vb.CALL_GEN_KWARGS, "vol_threshold": 0.60}
    sigs = vb.generate(k5, k15, k1h, variant="v3")
    p = [s for s in sigs if s["side"] == "P"]
    c = [s for s in sigs if s["side"] == "C"]
    trades = build_trades(p, k5, k1h, CFG["P"]) + build_trades(c, k5, k1h, CFG["C"])
    print(f"baseline gate: raw={len(sigs)} | trades={len(trades)}\n")

    mo0, mp0, st0 = dep.MAX_OPEN, dep.MARGIN_PCT, dep.START
    for START in (400.0, 2000.0):
        dep.START = START
        print("\n" + "#" * 70 + f"\n##### DEPOSIT ${START:.0f}  (1 lot ~ {100*25/START:.1f}% of equity)\n" + "#" * 70)
        rows = []
        for label, mo, mp in CONFIGS:
            dep.MAX_OPEN = mo
            dep.MARGIN_PCT = mp
            n_taken, equity, _ = dep.run_engine(trades, f"### {label}")
            rows.append((label, n_taken, equity))
        print("-" * 70)
        base_eq = base_tk = None
        for label, tk, eq in rows:
            if base_eq is None:
                base_eq, base_tk = eq, tk
            print(f"{label:30} taken={tk:4} (x{tk/max(1,base_tk):.1f})  FINAL ${eq:9,.0f} "
                  f"(ROI {(eq/START-1)*100:+.0f}%)")
    dep.MAX_OPEN, dep.MARGIN_PCT, dep.START = mo0, mp0, st0
    print("\nWin = on $2000 a higher-MO/smaller-MP config takes ~750 at ROI >= baseline & sane maxDD.")


if __name__ == "__main__":
    main()

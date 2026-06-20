#!/usr/bin/env python3
"""Iteration 4 — iter3 proved margin SLOTS (not signal generation) are the binding
constraint. So the honest frequency lever is CAPACITY: does raising MAX_OPEN bank more
$ at acceptable maxDD, on the BASELINE gate (P0.50/C0.60)?

Generate baseline signals + build trades ONCE, then replay the account engine with
MAX_OPEN in {4,6,8,10}. Real DVOL pricing, live exits. Decide by FINAL $ vs maxDD.
Caveat: more concurrent short-vol = more correlated tail risk; watch maxDD, not just $.

Run:  PYTHONPATH=. python3 services/max_open_sweep.py
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
    print(f"baseline gate: raw P={len(p)} C={len(c)} | trades={len(trades)}\n")

    base_orig = dep.MAX_OPEN
    for mo in (4, 6, 8, 10):
        dep.MAX_OPEN = mo
        dep.run_engine(trades, f"### MAX_OPEN={mo}")
    dep.MAX_OPEN = base_orig
    print("\nWin = FINAL$ rises materially with MAX_OPEN while maxDD stays tolerable (<~25%).")
    print("If $ flat or maxDD jumps -> capacity already sufficient / extra slots add only risk.")


if __name__ == "__main__":
    main()

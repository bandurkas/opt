#!/usr/bin/env python3
"""TEST GATE — OOS validation of the paper-experiment config before deploy.

Candidate: $2000 / MAX_OPEN=6 / MARGIN_PCT=0.10  (the "more activity" compromise).
Baseline:  $2000 / MAX_OPEN=4 / MARGIN_PCT=0.15  (deposit-matched control).

Runs the real account engine SEPARATELY on the TRAIN (first 70%) and HOLDOUT (last 30%)
trade segments — each starting fresh at $2000 — so the holdout is genuinely unseen and
compounding can't leak across the split. Honors DEVELOPMENT_FLOW: no deploy until the
candidate clears holdout (ROI>0, maxDD tolerable) and train~holdout (no OOS drift).

Run:  PYTHONPATH=. python3 services/candidate_holdout.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import services.variant_backtest as vb
import services.iv_mixed_deposit as dep
from services.iv_mixed_deposit import build_trades, TRAIN_FRAC
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

CONFIGS = [("BASELINE $2000 MO4 MP.15", 4, 0.15),
           ("CANDIDATE $2000 MO6 MP.10", 6, 0.10)]


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
    trades = sorted(build_trades(p, k5, k1h, CFG["P"]) + build_trades(c, k5, k1h, CFG["C"]),
                    key=lambda t: t["ts"])
    ts0, ts1 = trades[0]["ts"], trades[-1]["ts"]
    split_ts = ts0 + TRAIN_FRAC * (ts1 - ts0)
    train = [t for t in trades if t["ts"] < split_ts]
    hold = [t for t in trades if t["ts"] >= split_ts]
    print(f"trades={len(trades)} (train {len(train)} / holdout {len(hold)}), START=$2000\n")

    mo0, mp0, st0 = dep.MAX_OPEN, dep.MARGIN_PCT, dep.START
    dep.START = 2000.0
    for label, mo, mp in CONFIGS:
        dep.MAX_OPEN, dep.MARGIN_PCT = mo, mp
        print(f"========== {label} ==========")
        dep.run_engine(train, "  TRAIN  (first 70%)")
        dep.run_engine(hold, "  HOLDOUT (last 30%, unseen, fresh $2000)")
        print()
    dep.MAX_OPEN, dep.MARGIN_PCT, dep.START = mo0, mp0, st0
    print("PASS = candidate HOLDOUT ROI > 0, maxDD tolerable, and not much worse than baseline holdout.")
    print("(Engine prints START $400 as a fixed label — actual start is $2000; %/$ are correct.)")


if __name__ == "__main__":
    main()

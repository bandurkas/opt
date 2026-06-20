#!/usr/bin/env python3
"""Item 3 — deposit-scaling curve, OOS-validated. iter6 hinted ROI is SUPER-linear in
deposit at MO4 ($400->+74%, $2000->+209%) because $400 is granularity-starved (1 lot ~6%
of equity, margin-blocks + capped compounding). Confirm on TRAIN and HOLDOUT separately
and find the KNEE: the smallest deposit where granularity stops dragging ROI (above it,
extra capital just scales $ ~linearly at flat ROI%). That knee = the deposit target.

Baseline gate (P0.50/C0.60), MO4/MP.15, real DVOL. No deploy — measurement only.

Run:  PYTHONPATH=. python3 services/deposit_curve.py
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
DEPOSITS = [400, 800, 1200, 2000, 3000, 5000, 8000]


def run_quiet(trades, start, mo=4, mp=0.15):
    """run_engine but capture (taken, equity, maxDD) without its verbose print."""
    import io, contextlib
    dep.START, dep.MAX_OPEN, dep.MARGIN_PCT = float(start), mo, mp
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        tk, eq, _ = dep.run_engine(trades, "")
    # parse maxDD from the captured line
    dd = 0.0
    for ln in buf.getvalue().splitlines():
        if "maxDD" in ln:
            dd = float(ln.split("maxDD")[1].split("%")[0])
    return tk, eq, dd


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
    split = ts0 + TRAIN_FRAC * (ts1 - ts0)
    train = [t for t in trades if t["ts"] < split]
    hold = [t for t in trades if t["ts"] >= split]
    print(f"trades={len(trades)} (train {len(train)} / holdout {len(hold)}) | MO4/MP.15 real-DVOL\n")

    st0, mo0, mp0 = dep.START, dep.MAX_OPEN, dep.MARGIN_PCT
    print(f"{'deposit':>8} | {'TRAIN taken/ROI/DD':>24} | {'HOLDOUT taken/ROI/DD':>24}")
    prev_ho_roi = None
    for d in DEPOSITS:
        tt, te, td = run_quiet(train, d)
        ht, he, hd = run_quiet(hold, d)
        ho_roi = (he / d - 1) * 100
        knee = ""
        if prev_ho_roi is not None and ho_roi - prev_ho_roi < 5:
            knee = "  <- ROI flattening (knee)"
        prev_ho_roi = ho_roi
        print(f"${d:>6} | {tt:4} {(te/d-1)*100:+5.0f}% DD{td:4.1f}%      | "
              f"{ht:4} {ho_roi:+5.0f}% DD{hd:4.1f}%{knee}")
    dep.START, dep.MAX_OPEN, dep.MARGIN_PCT = st0, mo0, mp0
    print("\nKnee = deposit where HOLDOUT ROI% stops rising materially -> escape granularity drag.")
    print("Below knee: capital-starved (suboptimal). At/above: ROI% flat, $ scales linearly.")


if __name__ == "__main__":
    main()

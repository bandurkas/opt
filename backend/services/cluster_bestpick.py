#!/usr/bin/env python3
"""Loose end — when a vol cluster fires more signals than free slots, does picking the
RICHEST (highest sigma) instead of the FIRST (chronological/FIFO) improve the account?

The engine admits signals in ts order; within a cluster it takes whoever arrived first.
Here we re-order trades within 2h buckets by descending sigma (richest first) and replay
a ts-faithful engine, vs the pure-chronological baseline. Same gate (P0.50/C0.60), same
MO4/$400, real DVOL. If FINAL$/maxDD barely move -> selection order is marginal (expected).

Run:  PYTHONPATH=. python3 services/cluster_bestpick.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import services.variant_backtest as vb
import services.iv_mixed_deposit as dep
from services.iv_mixed_deposit import (build_trades, START, MARGIN_PCT, IM_RATE, LOT,
                                       MAX_OPEN, PORT_MARGIN_CAP, CB_LOSSES, CB_COOLDOWN_MS, fee)
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
import services.indicators as ind
from services.strategy_config import PUT_EXIT, CALL_EXIT

_orig_rv = ind.realized_vol_at_idx_1h
DVOL_IV = []
BUCKET_MS = 2 * 3600 * 1000


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


def engine(trades, order):
    """Account engine; `order` = list of trades in ADMISSION order (ts used for timing,
    monotonic-guarded). Mirrors iv_mixed_deposit.run_engine economics."""
    equity = START
    peak = equity
    max_dd = 0.0
    open_pos = []
    recent = []
    consec = 0
    cb_until = 0
    n_taken = 0
    now = order[0]["ts"]

    def realize(now_ts):
        nonlocal equity, peak, max_dd, consec, cb_until
        still = []
        for p in sorted(open_pos, key=lambda x: x["exit_ts"]):
            if p["exit_ts"] <= now_ts:
                equity += p["pnl_dollars"]
                recent.append(p["pnl_pct"])
                if p["pnl_pct"] > 0:
                    consec = 0
                else:
                    consec += 1
                    if consec >= CB_LOSSES:
                        cb_until = p["exit_ts"] + CB_COOLDOWN_MS
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
            else:
                still.append(p)
        open_pos[:] = still

    for t in order:
        now = max(now, t["ts"])
        realize(now)
        if t["ts"] < cb_until or len(open_pos) >= MAX_OPEN:
            continue
        used = sum(p["margin"] for p in open_pos)
        free = max(0.0, equity * PORT_MARGIN_CAP - used)
        dyn = 0.5 if (len(recent) >= 10 and sum(1 for x in recent[-10:] if x > 0) / 10 < 0.40) else 1.0
        budget = min(equity * MARGIN_PCT * dyn, free)
        m_per_lot = (IM_RATE * t["strike"] + t["mid"]) * LOT
        n_lots = int(budget // m_per_lot) if m_per_lot > 0 else 0
        if n_lots < 1:
            continue
        qty = n_lots * LOT
        credit_total = t["credit"] * qty
        gross = credit_total * t["pnl_pct"]
        fees = 2 * fee(t["strike"] * qty, credit_total)
        open_pos.append({"exit_ts": t["exit_ts"], "margin": m_per_lot * n_lots,
                         "pnl_dollars": gross - fees, "pnl_pct": t["pnl_pct"]})
        n_taken += 1
    if open_pos:
        realize(max(p["exit_ts"] for p in open_pos) + 1)
    return n_taken, equity, max_dd * 100


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

    chrono = sorted(trades, key=lambda t: t["ts"])
    # richest-first within each 2h bucket, buckets stay chronological
    bestpick = sorted(trades, key=lambda t: (t["ts"] // BUCKET_MS, -t.get("sigma", 0.0)))

    print(f"trades={len(trades)} | MO={MAX_OPEN} START=${START:.0f} real-DVOL\n")
    for name, order in (("CHRONO (FIFO, baseline)", chrono),
                        ("BESTPICK (richest in 2h)", bestpick)):
        tk, eq, dd = engine(trades, order)
        print(f"{name:26} taken={tk:4}  FINAL ${eq:8,.2f}  ({(eq/START-1)*100:+.1f}%)  maxDD {dd:.1f}%")
    print("\nMaterial = BESTPICK FINAL$ clearly > CHRONO at <= maxDD. Else selection order is marginal.")


if __name__ == "__main__":
    main()

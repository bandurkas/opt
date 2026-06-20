"""Decisive SL sweep on the REAL $400 account engine (margin, MAX_OPEN=4,
compounding, circuit-breaker, fees) — the lens that turns per-trade %-edge into
real final-equity / APR / maxDD. Builds on iv_mixed_deposit.py's banked MIXED-24
book (Call@24h + Put@96h) and varies ONLY the stop-loss, one side at a time, then
the best-of-both combo. Decide by FINAL $ and maxDD, sanity-checked vs HOLDOUT
per-trade avg (guards the compounding mirage).

Run:
  docker run --rm -v "$PWD/backend:/app" -v "$PWD/data:/data" -w /app \
    -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/sl_deposit_sweep.py
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.iv_mixed_deposit import build_trades, run_engine, START, TRAIN_FRAC
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.variant_backtest import generate

# live MIXED-24 exits; only "sl" is swept
CALL_BASE = {"tp1": 0.4, "tp2": 0.8, "sl": 0.75, "hold": 24, "expiry": 24.0}   # live call
PUT_BASE = {"tp1": 0.5, "tp2": 0.7, "sl": 1.50, "hold": 96, "expiry": 168.0}   # live put

CALL_SL_GRID = [0.75, 1.00, 1.25, 1.50]
PUT_SL_GRID = [1.50, 1.75, 2.00, 2.50]


def cfg(base, sl):
    d = dict(base); d["sl"] = sl; return d


def holdout_avg(trades, split_ts):
    ho = [t["pnl_pct"] * 100 for t in trades if t["ts"] >= split_ts]
    if not ho:
        return 0.0, 0
    return st.fmean(ho), len(ho)


def main():
    k5, k15, k1h = load_coin("eth", find_data_dir(None))
    sigs = generate(k5, k15, k1h, variant="v3")
    p = [s for s in sigs if s["side"] == "P"]
    c = [s for s in sigs if s["side"] == "C"]
    ts_all = sorted(int(s["ts_ms"]) for s in sigs)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    print(f"ETH: {len(p)} Put, {len(c)} Call signals | engine=$400 margin/MAX_OPEN4/compound/CB\n")

    # cache put/call trade-sets per sl so we don't rebuild repeatedly
    put_cache = {sl: build_trades(p, k5, k1h, cfg(PUT_BASE, sl)) for sl in set(PUT_SL_GRID) | {1.50}}
    call_cache = {sl: build_trades(c, k5, k1h, cfg(CALL_BASE, sl)) for sl in set(CALL_SL_GRID) | {0.75}}

    def line(label, calls, puts):
        book = calls + puts
        n_taken, equity, _ = run_engine(book, f"=== {label} ===")
        ca, cn = holdout_avg(calls, split_ts)
        pa, pn = holdout_avg(puts, split_ts)
        print(f"     HOLDOUT per-trade avg: CALL {ca:+.2f}% (n={cn}) | PUT {pa:+.2f}% (n={pn})")
        return equity

    print("########## CALL SL sweep (Put sl fixed @ live 1.50) ##########")
    for sl in CALL_SL_GRID:
        star = "  <== LIVE" if abs(sl - 0.75) < 1e-9 else ""
        line(f"CALL sl={sl:.2f}{star}  + Put sl=1.50", call_cache[sl], put_cache[1.50])

    print("\n########## PUT SL sweep (Call sl fixed @ live 0.75) ##########")
    for sl in PUT_SL_GRID:
        star = "  <== LIVE" if abs(sl - 1.50) < 1e-9 else ""
        line(f"CALL sl=0.75 + Put sl={sl:.2f}{star}", call_cache[0.75], put_cache[sl])

    print("\n########## best-of-both combos ##########")
    for csl in (1.00, 1.25):
        for psl in (1.75, 2.00):
            line(f"CALL sl={csl:.2f} + Put sl={psl:.2f}", call_cache[csl], put_cache[psl])

    print("\nLIVE = CALL 0.75 / PUT 1.50. Adopt a change only if FINAL $ rises AND "
          "maxDD does not worsen materially AND HOLDOUT per-trade avg holds.")


if __name__ == "__main__":
    main()

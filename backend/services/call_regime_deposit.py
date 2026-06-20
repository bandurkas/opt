"""Does CALL regime=['transition'] only (drop 'range') survive the REAL $400 engine?

realiv_improve / realiv_mixed validated transition-only calls on a per-trade real-IV
basis (holdout +4.49→+5.11%/trade, SL 17→15%). Per-trade ≠ account: the $400 engine
(margin, MAX_OPEN=4, compounding, circuit-breaker) can reward or punish a thinner
trade stream differently (fewer calls => fewer slots used => maybe more puts taken,
or just less throughput). Tested on the CURRENTLY DEPLOYED config: Call sl=0.75 @24h,
Put sl=2.00 @96h (the just-shipped SL upgrade).

Compares, with Put held identical:
  LIVE call regime   = range+transition (what generate() emits for side C)
  CANDIDATE          = transition only

Run:
  docker run --rm -v "$PWD/backend:/app" -v "$PWD/data:/data" -w /app \
    -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/call_regime_deposit.py
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.iv_mixed_deposit import build_trades, run_engine, TRAIN_FRAC
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.variant_backtest import generate

CALL_24 = {"tp1": 0.4, "tp2": 0.8, "sl": 0.75, "hold": 24, "expiry": 24.0}      # live call
PUT_96 = {"tp1": 0.5, "tp2": 0.7, "sl": 2.00, "hold": 96, "expiry": 168.0}      # deployed put SL=2.0


def holdout_avg(trades, split_ts):
    ho = [t["pnl_pct"] * 100 for t in trades if t["ts"] >= split_ts]
    if not ho:
        return 0.0, 0
    return st.fmean(ho), len(ho)


def main():
    k5, k15, k1h = load_coin("eth", find_data_dir(None))
    sigs = generate(k5, k15, k1h, variant="v3")
    p = [s for s in sigs if s["side"] == "P"]
    c_all = [s for s in sigs if s["side"] == "C"]
    c_trans = [s for s in c_all if s.get("regime") == "transition"]
    c_range = [s for s in c_all if s.get("regime") == "range"]
    ts_all = sorted(int(s["ts_ms"]) for s in sigs)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    print(f"ETH calls: {len(c_all)} total = {len(c_range)} range + {len(c_trans)} transition | puts {len(p)}")
    print(f"engine=$400 margin/MAX_OPEN4/compound/CB | Call sl=0.75@24h, Put sl=2.00@96h\n")

    put = build_trades(p, k5, k1h, PUT_96)
    call_all = build_trades(c_all, k5, k1h, CALL_24)
    call_trans = build_trades(c_trans, k5, k1h, CALL_24)

    def line(label, calls):
        run_engine(calls + put, f"=== {label} ===")
        ca, cn = holdout_avg(calls, split_ts)
        pa, pn = holdout_avg(put, split_ts)
        print(f"     HOLDOUT per-trade avg: CALL {ca:+.2f}% (n={cn}) | PUT {pa:+.2f}% (n={pn})")

    line("LIVE  call regime=range+transition", call_all)
    line("CAND  call regime=transition only", call_trans)
    print("\nAdopt transition-only if FINAL $ rises (or ~flat) with maxDD not worse AND "
          "CALL holdout per-trade avg improves (the realiv_improve effect should carry).")


if __name__ == "__main__":
    main()

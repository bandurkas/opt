"""eth_dollar_sl_deposit_sweep.py's CALL dollar-SL frac sweep, re-run on ETH's
full 4-year history (eth_long_*.json, 2022-06 on) instead of the ~1y window.

Real Deribit DVOL (eth_dvol_1h.json) only covers 2025-02 onward (~1.4y) — it
does NOT span 4 years, so this uses the same RV-based dynamic_sigma fallback
that eth_dollar_sl_backtest.py's _patched_rv already falls back to whenever
DVOL is unavailable for a bar (i.e. simply never patching DVOL_IV at all is
equivalent to "DVOL coverage = 0%", which is the correct, honest choice for a
4y window). This is the SAME RV-based engine iv_mixed_deposit.py uses for its
live %-SL baseline, so the baseline and the dollar-SL variants are priced
consistently with each other.

Parallelized across SL_DOLLAR_FRAC values (Pool(8), full 4y) instead of the
original's sequential loop.

Run:
    cd backend && PYTHONPATH=. python3 services/eth_dollar_sl_4y_sweep.py
"""
from __future__ import annotations

import statistics as st
import sys
import time
from datetime import datetime, timezone
from multiprocessing import cpu_count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs                              # noqa: E402
from services.local_optimizer import find_data_dir                  # noqa: E402
from services.multi_coin_signals import load_coin                   # noqa: E402
from services.eth_dollar_sl_backtest import simulate_short_dollar_sl  # noqa: E402
from services.iv_mixed_deposit import (                             # noqa: E402
    build_trades, run_engine, TRAIN_FRAC, CALL_24, PUT_96, HALF_SPREAD,
)
from services.option_futures_complement import gen_parallel         # noqa: E402
from services.strategy_config import CALL_GEN_KWARGS, PUT_GEN_KWARGS  # noqa: E402

FRACS = (0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30)


def build_trades_dollar_sl(sigs, k5, k1h, *, sl_dollar_frac: float) -> list[dict]:
    """Same trade-record shape as iv_mixed_deposit.build_trades, dollar-SL leg.
    No DVOL patch applied — pure RV-based sigma, valid across the full 4y span
    (matches build_trades' own RV-based dynamic_sigma=True baseline)."""
    out = simulate_short_dollar_sl(
        sigs, k5, expiry_hours=CALL_24["expiry"], tp2_pct=CALL_24["tp2"],
        sl_dollar_frac=sl_dollar_frac, option_horizon_h=CALL_24["hold"],
        spread_pct=2.0, klines_1h=k1h, iv_rv_multiplier=1.05, sigma_clamp=(0.20, 1.50),
    )
    T0 = CALL_24["expiry"] / (24 * 365)
    trades = []
    for s in out:
        o = s.get("option", {})
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        spot = s["close"]
        strike = round(spot / 25) * 25
        mid = bs.price(s["side"], spot, strike, T0, s["sigma_used"])
        if mid <= 0.01:
            continue
        bars = o.get("bars_held") or int(CALL_24["expiry"] * 12)
        trades.append({"ts": int(s["ts_ms"]), "exit_ts": int(s["ts_ms"]) + bars * 5 * 60 * 1000,
                       "strike": strike, "mid": mid, "credit": mid * (1 - HALF_SPREAD),
                       "pnl_pct": o["pnl_pct"] / 100.0, "sigma": s.get("sigma_used", 0.0)})
    return trades


def holdout_avg(trades, split_ts):
    ho = [t["pnl_pct"] * 100 for t in trades if t["ts"] >= split_ts]
    return (st.fmean(ho), len(ho)) if ho else (0.0, 0)


def train_avg(trades, split_ts):
    tr = [t["pnl_pct"] * 100 for t in trades if t["ts"] < split_ts]
    return (st.fmean(tr), len(tr)) if tr else (0.0, 0)


def _month(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def main():
    ncore = cpu_count()
    print(f"[1] klines (4y) + parallel gen ({ncore} cores)...")
    k5, k15, k1h = load_coin("eth_long", find_data_dir(None))
    k1h = sorted(k1h, key=lambda c: c["start_ms"])
    print(f"    5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}")

    calls = gen_parallel(k5, k15, k1h, CALL_GEN_KWARGS, ncore)
    puts = gen_parallel(k5, k15, k1h, PUT_GEN_KWARGS, ncore)
    print(f"    {len(calls)} call signals, {len(puts)} put signals")

    ts_all = sorted(int(s["ts_ms"]) for s in (calls + puts))
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])

    put_live = build_trades(puts, k5, k1h, PUT_96)
    call_live = build_trades(calls, k5, k1h, CALL_24)

    print("\n########## BASELINE (live %-SL=0.75, RV-based sigma, 4y) ##########")
    run_engine(call_live + put_live, "=== CALL %-SL=0.75 + PUT %-SL=2.00 (LIVE) ===")
    ta0, tn0 = train_avg(call_live, split_ts)
    ca0, cn0 = holdout_avg(call_live, split_ts)
    print(f"     TRAIN per-trade avg: CALL {ta0:+.2f}% (n={tn0})  |  "
          f"HOLDOUT per-trade avg: CALL {ca0:+.2f}% (n={cn0})")

    # Sequential, not parallelized: each frac is ~5,619 signals x <=288 bars of
    # BS-pricing — cheap (seconds), and an earlier Pool.map version that passed
    # the full 4y k5/k1h/calls arrays as per-task arguments deadlocked for 2h+
    # with zero CPU progress (likely a fork/pickle-IPC pitfall under this
    # Docker-on-Apple-Silicon setup) for no real speed benefit anyway.
    print(f"\n[2] sweeping {len(FRACS)} dollar-SL fracs (sequential)...")
    by_frac = {}
    for frac in FRACS:
        t_frac = time.time()
        by_frac[frac] = build_trades_dollar_sl(calls, k5, k1h, sl_dollar_frac=frac)
        print(f"    frac={frac:.2f} done in {time.time()-t_frac:.1f}s", flush=True)

    for frac in FRACS:
        call_dollar = by_frac[frac]
        print(f"\n########## CALL $-SL frac={frac:.2f} (Put unchanged, live %-SL=2.00) ##########")
        run_engine(call_dollar + put_live, f"=== CALL $-SL={frac:.2f} + PUT %-SL=2.00 ===")
        ta, tn = train_avg(call_dollar, split_ts)
        ca, cn = holdout_avg(call_dollar, split_ts)
        print(f"     TRAIN per-trade avg: CALL {ta:+.2f}% (n={tn}) [live {ta0:+.2f}%]  |  "
              f"HOLDOUT per-trade avg: CALL {ca:+.2f}% (n={cn}) [live {ca0:+.2f}%]")

    monthly_label_to_trades = {"LIVE(0.75)": call_live,
                               "$-frac=0.10": by_frac[0.10],
                               "$-frac=0.12": by_frac[0.12]}
    months = sorted({_month(t["ts"]) for trades in monthly_label_to_trades.values() for t in trades})
    print("\n---------- per-month avg %-PnL (CALL only) ----------")
    print(f"{'month':<9}" + "".join(f"{lbl:>18}" for lbl in monthly_label_to_trades))
    losing_months = {lbl: 0 for lbl in monthly_label_to_trades}
    for m in months:
        row = f"{m:<9}"
        for lbl, trades in monthly_label_to_trades.items():
            vals = [t["pnl_pct"] * 100 for t in trades if _month(t["ts"]) == m]
            if vals:
                avg = st.fmean(vals)
                if avg < 0:
                    losing_months[lbl] += 1
                row += f"{f'{avg:+.1f}% (n={len(vals)})':>18}"
            else:
                row += f"{'—':>18}"
        print(row)
    print("losing months (avg<0): " + ", ".join(
        f"{lbl}={n}/{len(months)}" for lbl, n in losing_months.items()))


if __name__ == "__main__":
    main()

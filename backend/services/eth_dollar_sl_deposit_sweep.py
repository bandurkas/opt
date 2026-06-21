"""$-account-level check on the Call dollar-margin SL candidate from
eth_dollar_sl_backtest.py (frac 0.10/0.15 — the only fracs that beat live %-SL=0.75
on raw %-PnL, train AND holdout). Raw %-PnL aggregates said "modest gain, worse
maxDD" — this runs it through the REAL $400 account engine (margin, MAX_OPEN=4,
80% portfolio cap, dyn-size, circuit-breaker, compounding fees) to see whether the
final-$/maxDD tradeoff holds up once capital constraints and compounding are in
the loop, exactly like sl_deposit_sweep.py did for the live PUT 1.50->2.00 change.

Put side is NOT swept here — eth_dollar_sl_backtest.py already showed dollar-SL
has no viable operating point for Puts (every frac either hurts or converges to
the live result), so Put stays at its live %-SL the whole time.

Run:
    cd backend && PYTHONPATH=. python3 services/eth_dollar_sl_deposit_sweep.py
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs                              # noqa: E402
from services.backtest_data import load_local_set                   # noqa: E402
from services.eth_dollar_sl_backtest import (                       # noqa: E402
    simulate_short_dollar_sl, IM_RATE as DOLLAR_IM_RATE, _patched_rv,
)
import services.eth_dollar_sl_backtest as dsl                       # noqa: E402
import services.indicators as ind                                   # noqa: E402
from services.iv_mixed_deposit import (                             # noqa: E402
    build_trades, run_engine, START, TRAIN_FRAC, CALL_24, PUT_96, HALF_SPREAD,
)
from services.option_futures_complement import gen_parallel         # noqa: E402
from services.strategy_config import CALL_GEN_KWARGS, PUT_GEN_KWARGS  # noqa: E402
from multiprocessing import cpu_count                                # noqa: E402

DATA = Path(os.path.expanduser("~/Desktop/options/data"))


def build_trades_dollar_sl(sigs, k5, k1h, *, sl_dollar_frac: float) -> list[dict]:
    """Same trade-record shape as iv_mixed_deposit.build_trades, but priced with
    the dollar-margin SL instead of %-of-premium (CALL_24's tp2=0.8/hold=24/
    expiry=24.0 kept identical to live; only the SL leg changes)."""
    # NOTE: iv_rv_multiplier=1.05 + sigma_clamp=(0.20, 1.50) must match
    # iv_mixed_deposit.build_trades's defaults exactly — this IS the live-baseline
    # pricing config, and a mismatched clamp would bias the comparison.
    out = simulate_short_dollar_sl(
        sigs, k5, expiry_hours=CALL_24["expiry"], tp2_pct=CALL_24["tp2"],
        sl_dollar_frac=sl_dollar_frac, option_horizon_h=CALL_24["hold"],
        spread_pct=2.0, im_rate=DOLLAR_IM_RATE, klines_1h=k1h,
        iv_rv_multiplier=1.05, sigma_clamp=(0.20, 1.50),
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
    if not ho:
        return 0.0, 0
    return st.fmean(ho), len(ho)


def train_avg(trades, split_ts):
    tr = [t["pnl_pct"] * 100 for t in trades if t["ts"] < split_ts]
    if not tr:
        return 0.0, 0
    return st.fmean(tr), len(tr)


def _month(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def monthly_breakdown(label_to_trades: dict[str, list[dict]]) -> None:
    """Guard against a single-month-driven mirage (the 'losing months' check used
    throughout this project, e.g. variant_backtest.py's `lm` stat)."""
    months = sorted({_month(t["ts"]) for trades in label_to_trades.values() for t in trades})
    print("\n---------- per-month avg %-PnL (CALL only) ----------")
    print(f"{'month':<9}" + "".join(f"{lbl:>18}" for lbl in label_to_trades))
    losing_months = {lbl: 0 for lbl in label_to_trades}
    for m in months:
        row = f"{m:<9}"
        for lbl, trades in label_to_trades.items():
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


def main():
    ncore = cpu_count()
    print(f"[1] klines + parallel gen ({ncore} cores)...")
    d = load_local_set(DATA)
    k5, k15, k1h = d["5"], d["15"], d["60"]
    k1h = sorted(k1h, key=lambda c: c["start_ms"])

    # REAL Deribit DVOL for both the live %-SL build_trades (dynamic_sigma=True)
    # and the dollar-SL builder (uses the same monkeypatched realized_vol_at_idx_1h).
    dvol = json.loads((DATA / "eth_dvol_1h.json").read_text())
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    dsl.DVOL_IV[:] = [iv_at.get(c["start_ms"] // 3_600_000) for c in k1h]
    ind.realized_vol_at_idx_1h = _patched_rv

    calls = gen_parallel(k5, k15, k1h, CALL_GEN_KWARGS, ncore)
    puts = gen_parallel(k5, k15, k1h, PUT_GEN_KWARGS, ncore)
    print(f"    {len(calls)} call signals, {len(puts)} put signals")

    ts_all = sorted(int(s["ts_ms"]) for s in (calls + puts))
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])

    put_live = build_trades(puts, k5, k1h, PUT_96)          # fixed, live %-SL=2.00 throughout
    call_live = build_trades(calls, k5, k1h, CALL_24)        # baseline, live %-SL=0.75

    print("\n########## BASELINE (live %-SL both sides) ##########")
    n0, eq0, _ = run_engine(call_live + put_live, "=== CALL %-SL=0.75 + PUT %-SL=2.00 (LIVE) ===")
    ta0, tn0 = train_avg(call_live, split_ts)
    ca0, cn0 = holdout_avg(call_live, split_ts)
    print(f"     TRAIN per-trade avg: CALL {ta0:+.2f}% (n={tn0})  |  "
          f"HOLDOUT per-trade avg: CALL {ca0:+.2f}% (n={cn0})")

    for frac in (0.08, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30):
        call_dollar = build_trades_dollar_sl(calls, k5, k1h, sl_dollar_frac=frac)
        print(f"\n########## CALL $-SL frac={frac:.2f} (Put unchanged, live %-SL=2.00) ##########")
        n, eq, _ = run_engine(call_dollar + put_live,
                              f"=== CALL $-SL={frac:.2f} + PUT %-SL=2.00 ===")
        ta, tn = train_avg(call_dollar, split_ts)
        ca, cn = holdout_avg(call_dollar, split_ts)
        print(f"     TRAIN per-trade avg: CALL {ta:+.2f}% (n={tn}) [live {ta0:+.2f}%]  |  "
              f"HOLDOUT per-trade avg: CALL {ca:+.2f}% (n={cn}) [live {ca0:+.2f}%]")

    print(f"\nLIVE baseline: FINAL above. Adopt only if FINAL $ rises AND maxDD does not "
          f"worsen materially AND HOLDOUT per-trade avg holds vs the {ca0:+.2f}%/n={cn0} live row.")

    call_010 = build_trades_dollar_sl(calls, k5, k1h, sl_dollar_frac=0.10)
    call_012 = build_trades_dollar_sl(calls, k5, k1h, sl_dollar_frac=0.12)
    monthly_breakdown({"LIVE(0.75)": call_live, "$-frac=0.10": call_010, "$-frac=0.12": call_012})


if __name__ == "__main__":
    main()

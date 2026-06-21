#!/usr/bin/env python3
"""ETH short-premium SL: %-of-premium (live) vs dollar-margin (BTC-style) — full year, REAL DVOL.

The BTC straddle bot uses a dollar-margin stop instead of a %-of-premium stop
because %-premium breaks near expiry: as premium decays toward 0, a fixed
percentage of it becomes a vanishingly tight dollar stop, even though the
position's actual risk (intrinsic-value sensitivity) hasn't shrunk. Question:
does the live ETH bot have the same latent issue, and would switching its SL
to dollar-margin (scaled off entry margin = IM_RATE*strike + entry_credit)
change the realized P&L?

Method: same signal generator + same exit framework as `sl_sweep.py` (REAL
Deribit-derived DVOL via monkeypatched `realized_vol_at_idx_1h`, identical to
the validated engine — no edits there), chronological 65/35 train/holdout.
Only the SL leg of `_simulate_short_premium` is swapped for a dollar-margin
equivalent; TP2/time-stop math is untouched (mirrors `btc_straddle_sl.py`'s
margin formula: margin = IM_RATE*strike + entry_credit; trip when buyback ask
loss >= SL_DOLLAR_FRAC * margin).

Run:
    cd backend && PYTHONPATH=. python3 services/eth_dollar_sl_backtest.py
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs                              # noqa: E402
from services.backtest_data import load_local_set                   # noqa: E402
from services.backtest import simulate_signal_set                   # noqa: E402
from services.option_futures_complement import gen_parallel         # noqa: E402
from services.strategy_config import (CALL_GEN_KWARGS, CALL_EXIT,    # noqa: E402
                                      PUT_GEN_KWARGS, PUT_EXIT)
import services.indicators as ind                                   # noqa: E402
from multiprocessing import cpu_count                                # noqa: E402

DATA = Path(os.path.expanduser("~/Desktop/options/data"))
IM_RATE = 0.10  # Bybit cross-margin IM rate for short ETH options (paper_strategy.IM_RATE)

_orig_rv = ind.realized_vol_at_idx_1h
DVOL_IV: list[float | None] = []


def _patched_rv(closes, i, lookback_h=168):
    if 0 <= i < len(DVOL_IV) and DVOL_IV[i] is not None:
        return DVOL_IV[i]
    return _orig_rv(closes, i, lookback_h)


def maxdd(pnls: list[float]) -> float:
    peak = 0.0
    c = 0.0
    dd = 0.0
    for p in pnls:
        c += p
        peak = max(peak, c)
        dd = min(dd, c - peak)
    return dd


def summ(label: str, rows: list[dict]) -> None:
    if not rows:
        print(f"      {label:9} n=0")
        return
    p = [r["pnl"] for r in rows]
    sl = 100 * sum(r["sl"] for r in rows) / len(rows)
    print(f"      {label:9} n={len(rows):4} | avg {st.fmean(p):+6.2f}% | total {sum(p):+8.1f}% | "
          f"SL {sl:4.1f}% | win {100*sum(x>0 for x in p)/len(rows):4.1f}% | maxDD {maxdd(p):+7.1f}%")


def simulate_short_dollar_sl(
    signals: list[dict], klines_5m: list[dict], *,
    expiry_hours: float, tp2_pct: float, sl_dollar_frac: float,
    option_horizon_h: float, spread_pct: float, im_rate: float = IM_RATE,
    strike_round_to: float = 25.0,
    klines_1h: list[dict] | None = None,
    iv_rv_multiplier: float = 1.0, sigma_clamp: tuple[float, float] = (0.05, 3.0),
) -> list[dict]:
    """Same walk-forward as backtest.py's short-premium path, but SL is dollar-
    margin (margin = im_rate*strike + entry_credit; trip at sl_dollar_frac*margin
    of buyback loss) instead of a fixed %-of-entry-credit. TP2/time-stop unchanged.
    """
    ts_to_idx_1h: dict[int, int] = {}
    closes_1h: list[float] = []
    if klines_1h:
        ts_to_idx_1h = {int(k["start_ms"]): i for i, k in enumerate(klines_1h)}
        closes_1h = [k["close"] for k in klines_1h]

    out = []
    for sig in signals:
        idx = sig["idx_5m"]
        side = sig["side"]
        future = klines_5m[idx + 1:]

        sigma_t = 0.6
        if closes_1h:
            sig_ts = int(sig["ts_ms"])
            hour_start = (sig_ts // 3_600_000) * 3_600_000
            i_1h = ts_to_idx_1h.get(hour_start, -1)
            if i_1h <= 0:
                for j, k in enumerate(klines_1h):
                    if int(k["start_ms"]) > sig_ts:
                        i_1h = j - 1
                        break
            if i_1h >= 168:
                rv168 = ind.realized_vol_at_idx_1h(closes_1h, i_1h, lookback_h=168)
                if rv168 is not None:
                    sigma_t = max(sigma_clamp[0], min(sigma_clamp[1], rv168 * iv_rv_multiplier))

        entry_spot = sig["close"]
        strike = round(entry_spot / strike_round_to) * strike_round_to
        T0 = expiry_hours / (24 * 365)
        bs_mid = bs.price(side, entry_spot, strike, T0, sigma_t)
        if bs_mid <= 0.01:
            out.append({**sig, "option": {"resolution": "no_entry", "pnl_pct": 0.0}})
            continue

        half_spread = spread_pct / 200.0
        entry_credit = bs_mid * (1 - half_spread)
        margin = im_rate * strike + entry_credit
        sl_dollar_trip = sl_dollar_frac * margin
        sl_pct_equiv = sl_dollar_trip / entry_credit if entry_credit > 0 else 0.0
        sl_mid = (entry_credit + sl_dollar_trip) / (1 + half_spread)
        tp2_mid = entry_credit * (1 - tp2_pct) / (1 + half_spread)

        bars_to_use = min(len(future), int(option_horizon_h * 12))
        resolution, pnl_pct, bars_held = None, None, None
        for bi in range(bars_to_use):
            bar = future[bi]
            elapsed_h = (bi + 1) * 5 / 60
            T = max(0.0, (expiry_hours - elapsed_h) / (24 * 365))
            hi_spot, lo_spot = bar["high"], bar["low"]
            if side == "C":
                premium_high = bs.price(side, hi_spot, strike, T, sigma_t)
                premium_low = bs.price(side, lo_spot, strike, T, sigma_t)
            else:
                premium_high = bs.price(side, lo_spot, strike, T, sigma_t)
                premium_low = bs.price(side, hi_spot, strike, T, sigma_t)

            if premium_high >= sl_mid:
                resolution, pnl_pct, bars_held = "sl", round(-sl_pct_equiv * 100, 2), bi + 1
                break
            if premium_low <= tp2_mid:
                resolution, pnl_pct, bars_held = "tp2", round(tp2_pct * 100, 2), bi + 1
                break

        if resolution is None:
            last_bar = future[bars_to_use - 1] if bars_to_use > 0 else None
            bars_held = bars_to_use
            if last_bar is None:
                resolution, pnl_pct = "no_data", 0.0
            else:
                elapsed_h = bars_to_use * 5 / 60
                T = max(0.0, (expiry_hours - elapsed_h) / (24 * 365))
                final_mid = bs.price(side, last_bar["close"], strike, T, sigma_t)
                buyback_ask = final_mid * (1 + half_spread)
                pnl = (entry_credit - buyback_ask) / entry_credit
                resolution, pnl_pct = "time_stop", round(pnl * 100, 2)

        out.append({**sig, "sigma_used": round(sigma_t, 4),
                    "option": {"resolution": resolution, "pnl_pct": pnl_pct, "bars_held": bars_held}})
    return out


def sweep_side(name: str, signals: list[dict], k5: list[dict], k1h: list[dict],
               exit_kw: dict, expiry_h: int, sl_dollar_grid: list[float], split: int) -> None:
    print(f"\n========== {name}  (expiry {expiry_h}h, tp2 {exit_kw['tp2_pct']}, "
          f"live %-SL={exit_kw['sl_pct']}) ==========")

    # Baseline: live %-of-premium SL (unchanged engine).
    sims = simulate_signal_set(signals, k5, sigma=0.6, expiry_hours=float(expiry_h),
                               tp1_pct=exit_kw["tp1_pct"], tp2_pct=exit_kw["tp2_pct"],
                               sl_pct=exit_kw["sl_pct"], option_horizon_h=exit_kw["hold_h"],
                               spread_pct=2.0, klines_1h=k1h, dynamic_sigma=True,
                               iv_rv_multiplier=1.0, sigma_clamp=(0.05, 3.0))
    rows = [{"idx": s["idx_5m"], "pnl": s["option"]["pnl_pct"],
             "sl": 1 if s["option"].get("resolution") == "sl" else 0}
            for s in sims if "pnl_pct" in s.get("option", {})]
    rows.sort(key=lambda r: r["idx"])
    print(f"  --- LIVE %-SL = {exit_kw['sl_pct']:.2f} ---")
    summ("TRAIN", [r for r in rows if r["idx"] < split])
    summ("HOLDOUT", [r for r in rows if r["idx"] >= split])

    for frac in sl_dollar_grid:
        sims = simulate_short_dollar_sl(signals, k5, expiry_hours=float(expiry_h),
                                        tp2_pct=exit_kw["tp2_pct"], sl_dollar_frac=frac,
                                        option_horizon_h=exit_kw["hold_h"], spread_pct=2.0,
                                        klines_1h=k1h, iv_rv_multiplier=1.0, sigma_clamp=(0.05, 3.0))
        rows = [{"idx": s["idx_5m"], "pnl": s["option"]["pnl_pct"],
                 "sl": 1 if s["option"].get("resolution") == "sl" else 0}
                for s in sims if "pnl_pct" in s.get("option", {})]
        rows.sort(key=lambda r: r["idx"])
        print(f"  --- $-SL_FRAC = {frac:.2f} (margin = {IM_RATE:.0%}*strike + entry_credit) ---")
        summ("TRAIN", [r for r in rows if r["idx"] < split])
        summ("HOLDOUT", [r for r in rows if r["idx"] >= split])


def main():
    ncore = cpu_count()
    print(f"[1] klines + parallel gen ({ncore} cores)...")
    d = load_local_set(DATA)
    k5, k15, k1h = d["5"], d["15"], d["60"]
    k1h = sorted(k1h, key=lambda c: c["start_ms"])

    dvol = json.loads((DATA / "eth_dvol_1h.json").read_text())
    iv_at = {int(r[0]) // 3_600_000: r[4] / 100.0 for r in dvol}
    global DVOL_IV
    DVOL_IV = [iv_at.get(c["start_ms"] // 3_600_000) for c in k1h]
    cov = 100 * sum(x is not None for x in DVOL_IV) / len(DVOL_IV)
    ind.realized_vol_at_idx_1h = _patched_rv
    split = int(len(k5) * 0.65)
    print(f"    DVOL coverage {cov:.0f}% | {len(k5)} 5m bars | split idx {split} (65%)")

    sl_dollar_grid = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.75, 1.00, 1.50, 2.00]

    calls = gen_parallel(k5, k15, k1h, CALL_GEN_KWARGS, ncore)
    print(f"\n[2] {len(calls)} call signals")
    sweep_side("CALL", calls, k5, k1h, CALL_EXIT, 24, sl_dollar_grid, split)

    puts = gen_parallel(k5, k15, k1h, PUT_GEN_KWARGS, ncore)
    print(f"\n[2] {len(puts)} put signals")
    sweep_side("PUT", puts, k5, k1h, PUT_EXIT, 168, sl_dollar_grid, split)

    print("\nPick the $-SL frac whose HOLDOUT total/avg beats the live %-SL row WITHOUT "
          "maxDD blowing out. If none beat it, the live %-SL is fine as-is for ETH.")


if __name__ == "__main__":
    main()

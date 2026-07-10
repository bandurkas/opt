"""Jony basket: trailing profit-lock A/B on the SAME engine that validated the
deployed config (basket_premium_backtest, ETH P+C + BTC C-only, MO4/cap3).

Question (user, 2026-07-10): instead of fixed TP2-or-time-stop, trail the
captured premium decay — let winners run but lock what's already earned.
Priors AGAINST: Boba1 trailing (arm×giveback) had no robust OOS edge;
Sniper early-TP/TP50 rejected (edge lives in the decay tail). Priors are from
OTHER engines — this tests Jony's own (different tenor/exits), per
feedback_dont_overclose_research_branches.

Mechanics (mid-space, mirrors the engine's tp2_mid/sl_mid conventions):
  decay(t) = 1 - premium_mid(t)/entry_credit
  ARM: trailing activates once peak decay >= arm.
  GIVEBACK: once armed, exit when decay retraces >= giveback from peak.
  Anti-lookahead: the trigger is evaluated against the PREVIOUS bar's peak;
  the peak updates at the end of the bar. SL checked first (worst-case-first,
  same as the engine); TP2/time-stop unchanged (trailing is an EXTRA exit).

Run: cd backend && PYTHONPATH=. .venv311/bin/python3 services/jony_trailing_backtest.py
"""
from __future__ import annotations

import multiprocessing as mp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest as bt
from services import backtest_bs as bs
from services.basket_premium_backtest import (
    coin_trades, replay_account, split, quarters,
)
from services.local_optimizer import find_data_dir

# trailing params consumed by the patched walker (set per worker)
_TRAIL = {"arm": None, "giveback": None}

_orig_walker = bt._simulate_short_premium


def _walker_with_trailing(*, side, strike, sigma, expiry_hours, bars_5m_forward,
                          bars_to_use_limit, entry_credit, half_spread,
                          tp1_mid, tp2_mid, sl_mid, tp1_pct, tp2_pct, sl_pct):
    arm, giveback = _TRAIL["arm"], _TRAIL["giveback"]
    if arm is None:
        return _orig_walker(side=side, strike=strike, sigma=sigma,
                            expiry_hours=expiry_hours, bars_5m_forward=bars_5m_forward,
                            bars_to_use_limit=bars_to_use_limit, entry_credit=entry_credit,
                            half_spread=half_spread, tp1_mid=tp1_mid, tp2_mid=tp2_mid,
                            sl_mid=sl_mid, tp1_pct=tp1_pct, tp2_pct=tp2_pct, sl_pct=sl_pct)

    bars_to_use = min(len(bars_5m_forward), bars_to_use_limit)
    peak_decay = 0.0  # best decay seen up to the PREVIOUS bar
    for bi in range(bars_to_use):
        bar = bars_5m_forward[bi]
        elapsed_h = (bi + 1) * 5 / 60
        T = max(0.0, (expiry_hours - elapsed_h) / (24 * 365))
        hi_spot, lo_spot = bar["high"], bar["low"]
        if side == "C":
            premium_high = bs.price(side, hi_spot, strike, T, sigma)
            premium_low = bs.price(side, lo_spot, strike, T, sigma)
        else:
            premium_high = bs.price(side, lo_spot, strike, T, sigma)
            premium_low = bs.price(side, hi_spot, strike, T, sigma)

        if premium_high >= sl_mid:
            return {"resolution": "sl", "pnl_pct": round(-sl_pct * 100, 2), "bars_held": bi + 1}

        # trailing lock: vs previous-bar peak, BEFORE updating it with this bar
        if peak_decay >= arm:
            worst_decay_now = 1 - premium_high / entry_credit
            if peak_decay - worst_decay_now >= giveback:
                trigger_mid = entry_credit * (1 - (peak_decay - giveback))
                buyback = trigger_mid * (1 + half_spread)
                pnl = (entry_credit - buyback) / entry_credit
                return {"resolution": "trail", "pnl_pct": round(pnl * 100, 2), "bars_held": bi + 1}

        if premium_low <= tp2_mid:
            return {"resolution": "tp2", "pnl_pct": round(tp2_pct * 100, 2), "bars_held": bi + 1}

        peak_decay = max(peak_decay, 1 - premium_low / entry_credit)

    last_bar = bars_5m_forward[bars_to_use - 1] if bars_to_use > 0 else None
    if last_bar is None:
        return {"resolution": "no_data", "pnl_pct": 0.0}
    elapsed_h = bars_to_use * 5 / 60
    T = max(0.0, (expiry_hours - elapsed_h) / (24 * 365))
    final_mid = bs.price(side, last_bar["close"], strike, T, sigma)
    buyback_ask = final_mid * (1 + half_spread)
    pnl = (entry_credit - buyback_ask) / entry_credit
    return {"resolution": "time_stop", "pnl_pct": round(pnl * 100, 2), "bars_held": bars_to_use}


bt._simulate_short_premium = _walker_with_trailing

JONY_COINS = {"eth": "PC", "btc": "C"}   # deployed Jony basket
MO, CAP = 4, 3


def run_config(args):
    arm, giveback = args
    _TRAIL["arm"], _TRAIL["giveback"] = arm, giveback
    data_dir = find_data_dir(None)
    trades = []
    for coin, sides in JONY_COINS.items():
        for t in coin_trades(coin, data_dir):
            if t["side"] in sides:
                trades.append(t)
    trades.sort(key=lambda t: t["ts"])
    tr, ho = split(trades)
    out = {"arm": arm, "giveback": giveback}
    for label, subset in (("train", tr), ("holdout", ho), ("full", trades)):
        m = replay_account(subset, MO, CAP)
        out[label] = m
    out["quarters"] = [replay_account(q, MO, CAP) for q in quarters(trades)]
    return out


def main():
    grid = [(None, None)]  # baseline first
    for arm in (0.25, 0.35, 0.45, 0.55):
        for gb in (0.10, 0.15, 0.20):
            grid.append((arm, gb))
    with mp.Pool(min(len(grid), max(1, mp.cpu_count() - 1))) as pool:
        results = pool.map(run_config, grid)

    print(f"{'config':<16} {'train$':>8} {'trDD%':>6} {'hold$':>8} {'hoDD%':>6} "
          f"{'full$':>8} {'fuDD%':>6} {'n_full':>6} {'Q+':>3}")
    print("-" * 78)
    for r in results:
        label = "BASELINE" if r["arm"] is None else f"arm{r['arm']:.2f}/gb{r['giveback']:.2f}"
        qpos = sum(1 for q in r["quarters"] if q["final"] > 800.0)  # START=800
        print(f"{label:<16} {r['train']['final']:>8.0f} {r['train']['max_dd']:>6.1f} "
              f"{r['holdout']['final']:>8.0f} {r['holdout']['max_dd']:>6.1f} "
              f"{r['full']['final']:>8.0f} {r['full']['max_dd']:>6.1f} "
              f"{r['full']['n_taken']:>6} {qpos:>2}/4")


if __name__ == "__main__":
    main()

"""Validates a NEW idea for Boba1/Grogu1: instead of opening one straddle per
24h cycle and riding it to TP2(90% decay)/SL/time-stop, take a SMALL combined
profit target across both legs (e.g. $2) as soon as it's reached, then
immediately re-open a fresh pair and repeat — potentially several times
within the same 24h window — instead of waiting for the next midnight
boundary. Per-leg dollar-margin SL still applies (sized off margin, sweepable
here too, since the user wants the stop "figured out statistically").

This is NOT the same idea as the already-rejected "event-driven reopen"
(SESSION_HANDOFF_2026-06-25_GROGU.md): that tested reopening immediately
after the FULL TP2/SL/time-stop resolution (avg hold ~hours-to-24h) and found
re-entering right after a volatile resolution walks back into still-elevated
vol (clustering) — worse net$ despite more cycles. Here the exit target is
much smaller/faster (a few dollars, likely sub-hour to few-hour holds), which
changes the holding-time distribution enough to warrant a fresh test rather
than assuming the prior rejection covers it.

Mechanics: within each original 24h day window, repeatedly open a fresh
Call+Put pair (re-priced via BS at the CURRENT spot/sigma, with remaining
tenor = day_end - now, matching how a real same-day reopen would buy into an
option expiring at the SAME fixed daily boundary, not a fresh 24h one — reuses
the entry_bar_offset convention from straddle_leg_stagger_backtest.py).
Walks bar-by-bar; closes BOTH legs together at the first of:
  (a) combined favorable-side credit captured >= profit_target_usd ("quick_tp")
  (b) EITHER leg's adverse dollar-SL trips ("forced_sl" — both close together,
      priced off the same bar's close, not each leg's own best/worst extreme,
      since this is a deliberate force-close-the-pair event)
  (c) day boundary reached ("day_end" — same time-stop convention as today)
If closed via (a) or (b) before the day ends, immediately reopens at the next
bar and continues until the day boundary.

Run: cd backend && PYTHONPATH=. python3 services/straddle_quick_scalp_backtest.py eth
"""
from __future__ import annotations

import statistics as st
import sys

from services import backtest_bs as bs
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.btc_straddle_sweep import build_periodic_signals
from services.btc_straddle_dollar_stop import (
    trailing_sigma, nearest_1h_idx, CYCLE_H, HALF_SPREAD, IM_RATE,
    STRIKE_ROUND_BY_COIN, LOT_BY_COIN,
)

DAY_BARS = int(CYCLE_H * 12)  # 5m bars per 24h


def _leg_entry(side, spot, strike, remaining_h, sigma):
    mid = bs.price(side, spot, strike, remaining_h / (24 * 365), sigma)
    if mid <= 0.01:
        return None
    credit = mid * (1 - HALF_SPREAD)
    margin = (IM_RATE * strike + mid)
    return {"credit": credit, "margin": margin}


def simulate_quick_scalp_day(cycle_idx, k5, sigma, sl_frac, profit_target_usd,
                              strike_round, lot, max_subtrades=20,
                              cb_consec_limit=999, cb_pause_h=0.0, min_reentry_h=0.0):
    """Returns {'day_pnl': float, 'n_subtrades': int, 'resolutions': [str,...],
    'cb_armed_count': int}. CB: after `cb_consec_limit` consecutive forced_sl
    sub-trades (reset by any quick_tp), stop re-entering for `cb_pause_h`
    hours (a flat $0 stretch, no position) before resuming — same shape as
    Sniper1's CB_CONSEC_LIMIT/CB_PAUSE_HOURS, scoped to within a single day
    since the day boundary already resets state. Default args (999, 0) make
    this a no-op, reproducing the plain quick-scalp behavior exactly.
    `min_reentry_h`: once fewer than this many hours remain before the day
    boundary, stop opening new pairs entirely for the rest of the day (the
    "harvest early, rest late" shape — not a tiny floor against zero-tenor
    entries, a deliberate multi-hour cutoff)."""
    day_end = cycle_idx + DAY_BARS
    if day_end >= len(k5):
        return None

    day_pnl = 0.0
    resolutions = []
    sub_start = cycle_idx
    n = 0
    consec_sl = 0
    cb_armed_count = 0
    while sub_start < day_end and n < max_subtrades:
        # CB pause: skip re-entry for cb_pause_h hours after cb_consec_limit
        # consecutive forced_sl losses — sit out (no position) until it lifts
        # or the day ends, whichever comes first.
        if consec_sl >= cb_consec_limit:
            cb_armed_count += 1
            pause_bars = int(cb_pause_h * 12)
            sub_start = min(sub_start + pause_bars, day_end)
            consec_sl = 0
            if sub_start >= day_end:
                break
            continue
        remaining_h = (day_end - sub_start) * 5 / 60
        if remaining_h <= min_reentry_h:
            break
        n += 1
        spot0 = k5[sub_start]["close"]
        strike = round(spot0 / strike_round) * strike_round
        ec = _leg_entry("C", spot0, strike, remaining_h, sigma)
        ep = _leg_entry("P", spot0, strike, remaining_h, sigma)
        if ec is None or ep is None:
            break
        sl_trip_c = sl_frac * ec["margin"] * lot
        sl_trip_p = sl_frac * ep["margin"] * lot
        credit_c_usd = ec["credit"] * lot
        credit_p_usd = ep["credit"] * lot

        future = k5[sub_start + 1: day_end + 1]
        closed = False
        for bi, bar in enumerate(future):
            elapsed_h = (bi + 1) * 5 / 60
            T = max(0.0, (remaining_h - elapsed_h) / (24 * 365))

            # Both legs MUST be priced off the SAME single spot at any one
            # check — you cannot buy back the call at the bar's low AND the
            # put at the bar's high simultaneously, they're different instants.
            # Check the two candidate within-bar spots (high, low) as two
            # separate, internally-consistent scenarios; SL takes priority
            # over TP within a scenario (matches simulate_leg_exit's order),
            # and if either scenario trips an SL we treat the bar as an SL
            # bar (conservative — don't assume the nicer scenario happened).
            sl_tripped = False
            best_tp_pnl = None
            for spot in (bar["high"], bar["low"]):
                call_ask = bs.price("C", spot, strike, T, sigma) * (1 + HALF_SPREAD)
                put_ask = bs.price("P", spot, strike, T, sigma) * (1 + HALF_SPREAD)
                call_loss = (call_ask - ec["credit"]) * lot
                put_loss = (put_ask - ep["credit"]) * lot
                if call_loss >= sl_trip_c or put_loss >= sl_trip_p:
                    sl_tripped = True
                    break
                combined_pnl = (credit_c_usd - call_ask * lot) + (credit_p_usd - put_ask * lot)
                if best_tp_pnl is None or combined_pnl > best_tp_pnl:
                    best_tp_pnl = combined_pnl

            if sl_tripped:
                # Force-close BOTH at this bar's close (single consistent spot)
                close_spot = bar["close"]
                call_now = bs.price("C", close_spot, strike, T, sigma) * (1 + HALF_SPREAD)
                put_now = bs.price("P", close_spot, strike, T, sigma) * (1 + HALF_SPREAD)
                pnl = (credit_c_usd - call_now * lot) + (credit_p_usd - put_now * lot)
                day_pnl += pnl
                resolutions.append("forced_sl")
                consec_sl += 1
                sub_start = sub_start + 1 + bi + 1
                closed = True
                break
            if best_tp_pnl is not None and best_tp_pnl >= profit_target_usd:
                day_pnl += best_tp_pnl
                resolutions.append("quick_tp")
                consec_sl = 0
                sub_start = sub_start + 1 + bi + 1
                closed = True
                break

        if not closed:
            # Day-end time-stop: value both legs at the last available bar
            last = future[-1] if future else k5[sub_start]
            call_now = bs.price("C", last["close"], strike, 0.0, sigma) * (1 + HALF_SPREAD)
            put_now = bs.price("P", last["close"], strike, 0.0, sigma) * (1 + HALF_SPREAD)
            pnl = (credit_c_usd - call_now * lot) + (credit_p_usd - put_now * lot)
            day_pnl += pnl
            resolutions.append("day_end")
            break

    return {"day_pnl": day_pnl, "n_subtrades": n, "resolutions": resolutions,
             "cb_armed_count": cb_armed_count}


def build_days(coin):
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    sigs = build_periodic_signals(k5, CYCLE_H)
    day_idxs = sorted({s["idx_5m"] for s in sigs})
    return k5, k1h, day_idxs


def agg(vals):
    n = len(vals)
    if n == 0:
        return "n   0"
    avg = sum(vals) / n
    sd = st.stdev(vals) if n > 1 else 0
    sh = avg / sd if sd > 0 else 0
    worst = min(vals)
    return f"n{n:>4} avg{avg:>+7.2f} Sh{sh:>+6.2f} worst{worst:>+7.1f}"


def main():
    coin = sys.argv[1] if len(sys.argv) > 1 else "eth"
    strike_round = STRIKE_ROUND_BY_COIN[coin]
    lot = LOT_BY_COIN[coin]
    base_sl_frac = 0.15 if coin.startswith("eth") else 2.0

    k5, k1h, day_idxs = build_days(coin)
    print(f"{coin}: {len(day_idxs)} candidate days\n", flush=True)

    sl_grid = (0.10, 0.15, 0.20, 0.30, 0.50) if coin.startswith("eth") else (1.0, 1.5, 2.0, 3.0, 5.0)
    target_grid = (1, 2, 3, 5, 10)

    for sl_frac in sl_grid:
        for target in target_grid:
            rows = []  # (ts, day_pnl)
            sub_counts = []
            res_counts = {"quick_tp": 0, "forced_sl": 0, "day_end": 0}
            for day_idx in day_idxs:
                idx_1h = nearest_1h_idx(k1h, k5[day_idx]["start_ms"])
                if idx_1h is None:
                    continue
                sigma = trailing_sigma(k1h, idx_1h)
                if sigma is None:
                    continue
                r = simulate_quick_scalp_day(day_idx, k5, sigma, sl_frac, target, strike_round, lot)
                if r is None:
                    continue
                rows.append((k5[day_idx]["start_ms"], r["day_pnl"]))
                sub_counts.append(r["n_subtrades"])
                for res in r["resolutions"]:
                    res_counts[res] = res_counts.get(res, 0) + 1

            if not rows:
                continue
            ts_all = sorted(t for t, _ in rows)
            split_ts = ts_all[0] + 0.70 * (ts_all[-1] - ts_all[0])
            tr = [p for t, p in rows if t < split_ts]
            ho = [p for t, p in rows if t >= split_ts]
            avg_subs = sum(sub_counts) / len(sub_counts)
            tot_res = sum(res_counts.values())
            print(f"sl_frac={sl_frac:<5} target=${target:<3} avg_subtrades/day={avg_subs:<5.2f} "
                  f"quick_tp={res_counts['quick_tp']/tot_res*100:>5.1f}% "
                  f"forced_sl={res_counts['forced_sl']/tot_res*100:>5.1f}% "
                  f"day_end={res_counts['day_end']/tot_res*100:>5.1f}%  "
                  f"TRAIN {agg(tr)}  HOLD {agg(ho)}", flush=True)
        print(flush=True)


if __name__ == "__main__":
    main()

"""Re-sweep of eth_straddle_sl.py's SL_DOLLAR_FRAC on refreshed full history
(through 2026-06-23), prompted by the cycle #20627 Put SL (-$24.58, strike
$1725, 2026-06-23 08:17 UTC). Extends btc_straddle_dollar_stop.py's sweep
grid down below its 0.30 floor, and adds a no-SL counterfactual per leg to
measure whipsaw rate: of the cycles where the SL fired, what fraction would
have ended better (or less bad) WITHOUT it (i.e. the leg recovered after the
stop would have tripped).

Run: cd backend && PYTHONPATH=. python3 services/eth_straddle_sl_resweep.py
"""
import sys, math, statistics as st

COIN = sys.argv[1] if len(sys.argv) > 1 else "eth"  # "eth" (1y) or "eth_long" (4y, 2022-06+)

from services import backtest_bs as bs
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.btc_straddle_sweep import build_periodic_signals
from services.btc_straddle_dollar_stop import (
    trailing_sigma, nearest_1h_idx, CYCLE_H, TP2, HALF_SPREAD, IM_RATE,
    STRIKE_ROUND_BY_COIN, LOT_BY_COIN,
)

STRIKE_ROUND = STRIKE_ROUND_BY_COIN[COIN]
LOT = LOT_BY_COIN[COIN]


def simulate_leg_full(side, entry_idx, k5, sigma, sl_dollar_frac):
    """Like simulate_leg_dollar_stop, but ALSO returns the no-SL counterfactual
    (TP2/time-stop only) so we can tell 'SL saved us' from 'SL fired then market
    reverted and we'd have been fine (or even profited) without it' (whipsaw)."""
    spot0 = k5[entry_idx]["close"]
    strike = round(spot0 / STRIKE_ROUND) * STRIKE_ROUND
    T0 = CYCLE_H / (24 * 365)
    entry_mid = bs.price(side, spot0, strike, T0, sigma)
    if entry_mid <= 0.01:
        return None
    entry_credit = entry_mid * (1 - HALF_SPREAD)
    margin = (IM_RATE * strike + entry_mid) * LOT
    credit_dollars = entry_credit * LOT
    sl_dollar_trip = sl_dollar_frac * margin
    tp2_mid = entry_credit * (1 - TP2) / (1 + HALF_SPREAD) if (1 + HALF_SPREAD) else 0

    bars_limit = int(CYCLE_H * 12)
    future = k5[entry_idx + 1: entry_idx + 1 + bars_limit]
    if not future:
        return None

    sl_hit_at = None
    sl_pnl = None
    nosl_resolution = "time_stop"
    nosl_pnl = None

    for bi, bar in enumerate(future):
        elapsed_h = (bi + 1) * 5 / 60
        T = max(0.0, (CYCLE_H - elapsed_h) / (24 * 365))
        hi_spot, lo_spot = bar["high"], bar["low"]
        if side == "C":
            premium_high = bs.price(side, hi_spot, strike, T, sigma)
            premium_low = bs.price(side, lo_spot, strike, T, sigma)
        else:
            premium_high = bs.price(side, lo_spot, strike, T, sigma)
            premium_low = bs.price(side, hi_spot, strike, T, sigma)

        buyback_ask_high = premium_high * (1 + HALF_SPREAD)
        unrealized_loss_dollars = (buyback_ask_high - entry_credit) * LOT
        if sl_hit_at is None and unrealized_loss_dollars >= sl_dollar_trip:
            sl_hit_at = bi
            sl_pnl = credit_dollars - buyback_ask_high * LOT

        # no-SL track: TP2 only
        if nosl_pnl is None and premium_low <= tp2_mid:
            buyback_ask_low = premium_low * (1 + HALF_SPREAD)
            nosl_resolution = "tp2"
            nosl_pnl = credit_dollars - buyback_ask_low * LOT
            break  # both tracks done once TP2 hits (SL would've fired first if it was going to)

    if nosl_pnl is None:
        last = future[-1]
        elapsed_h = len(future) * 5 / 60
        T = max(0.0, (CYCLE_H - elapsed_h) / (24 * 365))
        final_mid = bs.price(side, last["close"], strike, T, sigma)
        buyback_ask = final_mid * (1 + HALF_SPREAD)
        nosl_pnl = credit_dollars - buyback_ask * LOT

    if sl_hit_at is not None:
        return {"resolution": "sl_dollar", "pnl_dollars": sl_pnl, "margin": margin,
                "nosl_resolution": nosl_resolution, "nosl_pnl_dollars": nosl_pnl,
                "whipsaw": nosl_pnl > sl_pnl}  # would NOT having an SL have done better?
    return {"resolution": nosl_resolution, "pnl_dollars": nosl_pnl, "margin": margin,
            "nosl_resolution": nosl_resolution, "nosl_pnl_dollars": nosl_pnl, "whipsaw": False}


def build_cycles_full(coin):
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    sigs = build_periodic_signals(k5, CYCLE_H)
    cycles_by_idx = {}
    for s in sigs:
        cycles_by_idx.setdefault(s["_cycle"], {})[s["side"]] = s["idx_5m"]
    return k5, k1h, cycles_by_idx


def _sweep_one_frac(args):
    """Top-level (picklable) worker — one frac's full cycle sweep, for Pool.map."""
    frac, k5, k1h, cycles_by_idx = args
    rows = []  # (ts, pct_of_margin, resolution, whipsaw)
    for cycle_idx, legs in sorted(cycles_by_idx.items()):
        if "C" not in legs or "P" not in legs:
            continue
        idx_1h = nearest_1h_idx(k1h, k5[cycle_idx]["start_ms"])
        if idx_1h is None:
            continue
        sigma = trailing_sigma(k1h, idx_1h)
        if sigma is None:
            continue
        legres = {}
        ok = True
        for side in ("C", "P"):
            r = simulate_leg_full(side, legs[side], k5, sigma, frac)
            if r is None:
                ok = False
                break
            legres[side] = r
        if not ok:
            continue
        tot_pnl = legres["C"]["pnl_dollars"] + legres["P"]["pnl_dollars"]
        tot_margin = legres["C"]["margin"] + legres["P"]["margin"]
        pct = tot_pnl / tot_margin * 100 if tot_margin else 0.0
        any_sl = legres["C"]["resolution"] == "sl_dollar" or legres["P"]["resolution"] == "sl_dollar"
        any_whipsaw = legres["C"].get("whipsaw") or legres["P"].get("whipsaw")
        ts = k5[cycle_idx]["start_ms"]
        rows.append((ts, pct, any_sl, any_whipsaw,
                    legres["C"]["resolution"], legres["P"]["resolution"]))
    return frac, rows


def main():
    from multiprocessing import Pool, cpu_count

    k5, k1h, cycles_by_idx = build_cycles_full(COIN)
    print(f"ETH straddle deep SL analysis — {len(cycles_by_idx)} candidate cycles\n")

    fracs = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.75, 1.00, 1.50, 2.00, 3.00)
    ncore = min(cpu_count(), len(fracs))
    print(f"[parallel] {ncore} workers across {len(fracs)} frac values", flush=True)

    with Pool(ncore) as pool:
        results = pool.map(_sweep_one_frac, [(frac, k5, k1h, cycles_by_idx) for frac in fracs])
    per_frac = dict(results)
    ts_all = sorted(t for t, *_ in per_frac[fracs[0]])

    split_ts = ts_all[0] + 0.70 * (ts_all[-1] - ts_all[0])

    print(f"{'frac':>6} {'n':>4} {'avg%':>8} {'Sharpe':>7} {'worst%':>8} {'maxDD-ish':>9} "
          f"{'SL-hit%':>8} {'whipsaw%':>9} {'TRAIN avg':>10} {'HOLD avg':>10}")
    best_train = None
    for frac in fracs:
        rows = per_frac[frac]
        if not rows:
            continue
        pcts = [p for _, p, *_ in rows]
        n = len(pcts)
        avg = sum(pcts)/n
        sd = st.stdev(pcts) if n > 1 else 0
        sh = avg/sd if sd else 0
        worst = min(pcts)
        # crude running-equity maxDD on cumulative pct (illustrative, not compounded $)
        cum = 0.0
        peak = 0.0
        maxdd = 0.0
        for p in pcts:
            cum += p
            peak = max(peak, cum)
            maxdd = max(maxdd, peak - cum)
        sl_hit_pct = 100*sum(1 for _,_,a,*_ in rows if a)/n
        whip_n = sum(1 for _,_,a,w,*_ in rows if a and w)
        sl_n = sum(1 for _,_,a,*_ in rows if a)
        whip_pct = 100*whip_n/sl_n if sl_n else 0.0
        tr = [p for t, p, *_ in rows if t < split_ts]
        ho = [p for t, p, *_ in rows if t >= split_ts]
        tr_avg = sum(tr)/len(tr) if tr else 0
        ho_avg = sum(ho)/len(ho) if ho else 0
        tr_sd = st.stdev(tr) if len(tr) > 1 else 0
        tr_sh = tr_avg/tr_sd if tr_sd else 0
        print(f"{frac:>6.2f} {n:>4} {avg:>+7.2f}% {sh:>+7.2f} {worst:>+7.1f}% {maxdd:>8.1f}% "
              f"{sl_hit_pct:>7.1f}% {whip_pct:>8.1f}% {tr_avg:>+9.2f}% {ho_avg:>+9.2f}%")
        if best_train is None or tr_sh > best_train[1]:
            best_train = (frac, tr_sh)

    print(f"\nBest by TRAIN-only Sharpe: frac={best_train[0]} (train Sharpe {best_train[1]:+.2f})")
    print("'whipsaw%' = of the cycles where SL fired, what fraction would have ended")
    print("better (or less bad) WITHOUT the SL — i.e. SL cut a leg that recovered.")


if __name__ == "__main__":
    main()

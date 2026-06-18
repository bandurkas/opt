#!/usr/bin/env python3
"""Futures complement to the validated short-CALL options strategy.

Tests the user's hypothesis: the options bot sells calls (short delta); it hits SL
exactly when ETH RISES through the strike. A parallel LONG perp — opposite the
option's delta — profits in that same scenario. Does adding it improve the
COMBINED result (cut the SL tail) and is it net positive after the premium it
gives back on the trades that stay in range?

Two perp variants per option trade, over the option's [entry, exit] window (5m walk):
  STATIC : hold long perp the whole window (full delta hedge). Cuts SL but bleeds
           on the option's winning (price-falls/flat) trades.
  TRIG   : synthetic long-gamma — enter long perp ONLY if price rises >= trig% above
           entry (the move that hurts the short call), then hold to exit. Aims to
           catch the SL breakout without bleeding when price stays in range.

Accounting in $ with the live sizing (contracts=0.3 ETH). Option credit & PnL from
the validated backtest; perp PnL = h*contracts*(dPrice) - taker/slip - funding.
Reports option-only vs +STATIC vs +TRIG: total, drawdown, and PnL split by the
option's exit_reason (sl vs win).
"""
import argparse
import hashlib
import json
import math
import os
import statistics as st
import sys
from functools import partial
from multiprocessing import Pool, cpu_count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.backtest_data import load_local_set            # noqa: E402
from services.backtest import simulate_signal_set            # noqa: E402
from services.strategy_registry import gen_sell_premium_iv_high  # noqa: E402
from services import backtest_bs as bs                        # noqa: E402

DATA = Path(os.path.expanduser("~/Desktop/options/data"))
WINNER_GEN = {"vol_threshold": 0.7, "regime_filter": ["range", "transition"], "side": "C",
              "adx_max": None, "mtf_direction_filter": "down", "cooldown_bars": 6}
WINNER_EXIT = {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24, "tsl_t": 0.0, "tsl_o": 0.0}
LOOKBACK = 2500   # vol_lookback 168h(=2016 bars) + history 240 + buffer

# ---- parallel signal generation across all cores (gen is the only heavy step) ----
_K5 = _K15 = _K1H = None


def _init(k5, k15, k1h):
    global _K5, _K15, _K1H
    _K5, _K15, _K1H = k5, k15, k1h


def _gen_chunk(task):
    a, b, kwargs = task
    lo = max(0, a - LOOKBACK)
    sub = _K5[lo:b]
    sigs = gen_sell_premium_iv_high(sub, _K15, _K1H, **kwargs)
    out = []
    for s in sigs:
        g = s["idx_5m"] + lo
        if a <= g < b:
            s = dict(s); s["idx_5m"] = g
            out.append(s)
    return out


def gen_parallel(k5, k15, k1h, kwargs, ncore):
    n = len(k5)
    step = math.ceil(n / ncore)
    tasks = [(a, min(a + step, n), kwargs) for a in range(0, n, step)]
    with Pool(ncore, initializer=_init, initargs=(k5, k15, k1h)) as pool:
        parts = pool.map(_gen_chunk, tasks)
    sigs = [s for p in parts for s in p]
    sigs.sort(key=lambda s: s["idx_5m"])
    return sigs


def fund_lookup():
    d = json.loads((DATA / "eth_funding.json").read_text()); d.sort(key=lambda r: r["ts_ms"])
    ts = [r["ts_ms"] for r in d]; rt = [float(r["funding_rate"]) for r in d]

    def f(t):
        lo, hi = 0, len(ts) - 1
        if t < ts[0]:
            return 0.0
        while lo < hi:
            m = (lo + hi + 1) // 2
            (lo := m) if ts[m] <= t else (hi := m - 1)
        return rt[lo] / 8.0
    return f


def perp_pnl(prices5, k_entry, k_exit, contracts, h, fund_f, ts5,
             fee=0.00035, slip=0.0002, trig=None):
    """Long-perp $ PnL over [k_entry,k_exit]. trig=None -> static; else stop-entry."""
    entry_px = prices5[k_entry]
    qty = h * contracts
    if qty <= 0:
        return 0.0
    k_in = k_entry
    if trig is not None:                       # wait for price to rise trig% (hurts short call)
        k_in = None
        for k in range(k_entry, k_exit + 1):
            if prices5[k] >= entry_px * (1 + trig):
                k_in = k; break
        if k_in is None:
            return 0.0
    fill = prices5[k_in]
    exit_px = prices5[k_exit]
    gross = qty * (exit_px - fill)
    cost = qty * fill * (fee + slip) + qty * exit_px * (fee + slip)   # in + out
    fund = sum(qty * prices5[k] * fund_f(ts5[k]) for k in range(k_in, k_exit))  # long pays funding
    return gross - cost - fund


def maxdd(curve):
    peak = curve[0]; dd = 0.0
    for v in curve:
        peak = max(peak, v); dd = min(dd, v - peak)
    return dd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=float, default=0.5, help="perp ETH per option ETH (ATM call delta~0.5)")
    ap.add_argument("--trig", type=float, default=0.004, help="stop-entry trigger (frac rise) for TRIG")
    ap.add_argument("--contracts", type=float, default=0.3)
    ap.add_argument("--regen", action="store_true", help="force regenerate signals (ignore cache)")
    args = ap.parse_args()

    ncore = cpu_count()
    cache_key = hashlib.md5(json.dumps([WINNER_GEN, WINNER_EXIT], sort_keys=True).encode()).hexdigest()[:10]
    cache = Path(f"/tmp/opt_sims_{cache_key}.json")
    print("[1] loading local klines...")
    _d = load_local_set(DATA)
    k5, k15, k1h = _d["5"], _d["15"], _d["60"]

    if cache.exists() and not args.regen:
        sims = json.loads(cache.read_text())
        print(f"    loaded {len(sims)} cached sims ({cache.name}) — skip gen")
    else:
        print(f"[1b] generating validated short-CALL signals on {ncore} cores...")
        signals = gen_parallel(k5, k15, k1h, WINNER_GEN, ncore)
        print(f"     {len(signals)} signals")
        sims = simulate_signal_set(signals, k5, sigma=0.6, expiry_hours=168.0,
                                   tp1_pct=WINNER_EXIT["tp1"], tp2_pct=WINNER_EXIT["tp2"],
                                   sl_pct=WINNER_EXIT["sl"], option_horizon_h=WINNER_EXIT["hold_h"],
                                   spread_pct=2.0, tsl_trigger_pct=0.0, tsl_offset_pct=0.0)
        # keep only what we need, cache it
        slim = [{"ts_ms": s["ts_ms"], "close": s["close"], "side": s["side"],
                 "option": {k: s.get("option", {}).get(k) for k in ("resolution", "pnl_pct", "bars_held")}}
                for s in sims]
        cache.write_text(json.dumps(slim))
        sims = slim
        print(f"     cached -> {cache.name}")
    # signals/exits use bar CLOSE time (start_ms + 5min) per backtest._walk
    ts5 = [c["start_ms"] + 5 * 60 * 1000 for c in k5]
    px5 = [float(c["close"]) for c in k5]
    idx = {t: i for i, t in enumerate(ts5)}
    fund_f = fund_lookup()

    T0 = 168.0 / 8760.0   # expiry_hours used in the backtest, in years
    rows = []
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt or opt.get("bars_held") is None:
            continue
        e_ts = s["ts_ms"]
        if e_ts not in idx:
            continue
        ke = idx[e_ts]
        kx = min(ke + int(opt["bars_held"]), len(px5) - 1)
        if kx <= ke:
            continue
        entry_px = s["close"]
        strike = round(entry_px / 25) * 25
        credit_usd = bs.price("C", entry_px, strike, T0, 0.6) * args.contracts  # premium collected
        opt_usd = (opt["pnl_pct"] / 100.0) * credit_usd
        ps = perp_pnl(px5, ke, kx, args.contracts, args.h, fund_f, ts5, trig=None)
        pt = perp_pnl(px5, ke, kx, args.contracts, args.h, fund_f, ts5, trig=args.trig)
        rows.append({"reason": opt.get("resolution"), "opt": opt_usd,
                     "static": ps, "trig": pt, "ret": px5[kx] / px5[ke] - 1})

    n = len(rows)
    if n == 0:
        print(f"NO ROWS: sims={len(sims)}, with pnl/exit_ts matched=0. "
              f"sample sim keys={list(sims[0].keys()) if sims else 'none'} "
              f"opt keys={list(sims[0].get('option',{}).keys()) if sims else 'none'}")
        return
    sl = [r for r in rows if r["reason"] == "sl"]
    win = [r for r in rows if r["reason"] != "sl"]
    print(f"\n[2] {n} option trades | {len(sl)} SL, {len(win)} non-SL "
          f"({100*len(win)/n:.0f}% win) | avg move/trade {100*st.fmean([r['ret'] for r in rows]):+.2f}%\n")

    def block(name, key_combo):
        tot = sum(key_combo(r) for r in rows)
        curve, c = [], 0.0
        for r in rows:
            c += key_combo(r); curve.append(c)
        dd = maxdd(curve)
        sl_pnl = sum(key_combo(r) for r in sl)
        win_pnl = sum(key_combo(r) for r in win)
        wr = 100 * sum(1 for r in rows if key_combo(r) > 0) / n
        print(f"  {name:18} total ${tot:+8.2f} | maxDD ${dd:8.2f} | win% {wr:4.0f} | "
              f"SL-trades ${sl_pnl:+7.2f} | non-SL ${win_pnl:+7.2f}")

    print(f"sizing: contracts={args.contracts} ETH, perp h={args.h} (qty {args.h*args.contracts} ETH), trig={args.trig*100:.1f}%")
    block("OPTION only", lambda r: r["opt"])
    block("+ STATIC perp", lambda r: r["opt"] + r["static"])
    block("+ TRIG perp", lambda r: r["opt"] + r["trig"])
    print("\n  --- perp leg ALONE (is the futures complement itself +EV?) ---")
    block("STATIC perp only", lambda r: r["static"])
    block("TRIG perp only", lambda r: r["trig"])
    print("\nKey: does + perp cut SL-trades loss & maxDD without killing non-SL profit? "
          "Is the perp leg +EV on its own (esp. on SL trades)?")


if __name__ == "__main__":
    main()

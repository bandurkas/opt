"""
Stage-2 Optimizer: Tests NEW signal dimensions that mega_optimizer doesn't cover:
  - IV surface adjustment: test sigma 0.45 / 0.50 / 0.55 / 0.60 / 0.70
  - Score MINIMUM filter (take only score >= threshold, e.g. only 6+)
  - Transition regime inclusion (not just trend)
  - Accelerating flag (only take signals where momentum is accelerating)
"""
import sys, os, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services import backtest_bs as bs
from services.backtest import generate_raw_signals
from services.backtest_data import fetch_set
from datetime import datetime, timezone


def simulate_with_tsl(signal, klines_5m, sigma, expiry_h, tp1, tp2, sl,
                      tsl_trigger, tsl_dist, spread_pct=2.0):
    start_ms = signal["ts_ms"]
    side = signal["side"]
    entry_spot = signal["close"]
    strike = round(entry_spot / 25) * 25

    T0 = expiry_h / (24.0 * 365.0)
    bs_mid = bs.price(side, entry_spot, strike, T0, sigma)
    if bs_mid <= 0.01:
        return None

    hs = spread_pct / 200.0
    entry = bs_mid * (1 + hs)
    tp1_p = entry * (1 + tp1) / (1 - hs)
    tp2_p = entry * (1 + tp2) / (1 - hs)
    tsl_floor = entry * (1 - sl) / (1 - hs)

    path = [c for c in klines_5m if start_ms < c["start_ms"] <= start_ms + int(expiry_h * 3600000)]
    if not path: return None

    pos1, pos2 = True, True
    e1 = e2 = 0.0

    for i, c in enumerate(path):
        rem_h = expiry_h - (i + 1) * 5 / 60.0
        T = max(0.0, rem_h / (24.0 * 365.0))
        hi_p = bs.price(side, c["low"] if side == "P" else c["high"], strike, T, sigma)
        lo_p = bs.price(side, c["high"] if side == "P" else c["low"], strike, T, sigma)

        hi_pnl = (hi_p * (1 - hs) - entry) / entry
        if tsl_trigger > 0 and hi_pnl >= tsl_trigger:
            new_floor = entry * (1 + hi_pnl - tsl_dist) / (1 - hs)
            if new_floor > tsl_floor: tsl_floor = new_floor

        if lo_p <= tsl_floor:
            exit_p = (tsl_floor * (1 - hs) - entry) / entry
            e1 = exit_p if pos1 else e1
            e2 = exit_p if pos2 else e2
            return ((e1 + e2) / 2.0) * 100.0

        if pos2 and hi_p >= tp2_p:
            e2 = (tp2_p * (1 - hs) - entry) / entry; pos2 = False
        if pos1 and hi_p >= tp1_p:
            e1 = (tp1_p * (1 - hs) - entry) / entry; pos1 = False
        if not pos1 and not pos2: break

    if pos1 or pos2:
        T_last = max(0.0, (expiry_h - len(path) * 5/60) / (24*365))
        final_mid = bs.price(side, path[-1]["close"], strike, T_last, sigma)
        fp = (final_mid * (1 - hs) - entry) / entry
        if pos1: e1 = fp
        if pos2: e2 = fp

    return ((e1 + e2) / 2.0) * 100.0


def run(sigs, klines_5m, sigma, expiry, tp1, tp2, sl, tsl_t, tsl_d):
    pnls = [p for s in sigs if (p := simulate_with_tsl(s, klines_5m, sigma, expiry, tp1, tp2, sl, tsl_t, tsl_d)) is not None]
    if not pnls: return None
    return {
        "n": len(pnls),
        "per_day": len(pnls) / 60.0,
        "wr": sum(1 for p in pnls if p > 0) / len(pnls),
        "avg": sum(pnls) / len(pnls),
        "ev": sum(pnls) / 60.0
    }


def main():
    print("Fetching 60d data...", flush=True)
    data = fetch_set("ETHUSDT", days=60, intervals=("5", "15", "60"))
    k5, k15, k1h = data["5"], data["15"], data["60"]

    # Champion fixed params from mega_optimizer
    EXPIRY = 120.0; TP1 = 0.25; TP2 = 0.50; SL = 0.40; TSL_T = 0.25; TSL_D = 0.20; CD = 6

    # Base signal pool (CD=6, MTF=2/3, Puts, Trend, Fade)
    base_sigs = generate_raw_signals(k5, k15, k1h, min_alignment=2, cooldown_bars=CD, fade=True)
    base_sigs = [s for s in base_sigs if s["side"] == "P" and s["regime"] == "trend" and s["mtf_aligned"] == 2]
    print(f"Base pool: {len(base_sigs)} signals", flush=True)

    results = []

    # ── EXPERIMENT 1: IV surface (sigma) sweep ──
    print("\n[EXP1] Sigma / IV sweep...", flush=True)
    print(f"{'Sigma':>7} | {'N/day':>5} {'WR':>6} {'P&L':>7}")
    for sigma in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
        r = run(base_sigs, k5, sigma, EXPIRY, TP1, TP2, SL, TSL_T, TSL_D)
        if r: print(f"{sigma:>7.2f} | {r['per_day']:>5.1f} {r['wr']*100:>5.1f}% {r['avg']:>+7.2f}%")
        results.append(("sigma", sigma, r))

    # ── EXPERIMENT 2: Score threshold (min and max) ──
    print("\n[EXP2] Score threshold sweep...", flush=True)
    print(f"{'Score Filter':>14} | {'N':>4} {'N/day':>5} {'WR':>6} {'P&L':>7}")
    for score_min, score_max in [(0, 10), (4, 8), (5, 8), (6, 8), (0, 8), (4, 9), (5, 9)]:
        filtered = [s for s in base_sigs if score_min <= s["score"] <= score_max]
        if len(filtered) < 20: continue
        r = run(filtered, k5, 0.60, EXPIRY, TP1, TP2, SL, TSL_T, TSL_D)
        if r: print(f"{'%d-%d' % (score_min, score_max):>14} | {r['n']:>4} {r['per_day']:>5.1f} {r['wr']*100:>5.1f}% {r['avg']:>+7.2f}%")

    # ── EXPERIMENT 3: Regime inclusion (add transition) ──
    print("\n[EXP3] Regime filter sweep...", flush=True)
    all_sigs = generate_raw_signals(k5, k15, k1h, min_alignment=2, cooldown_bars=CD, fade=True)
    all_sigs = [s for s in all_sigs if s["side"] == "P" and s["mtf_aligned"] == 2]
    print(f"{'Regime':>14} | {'N':>4} {'N/day':>5} {'WR':>6} {'P&L':>7}")
    for regime_filter in [["trend"], ["transition"], ["trend", "transition"], [None]]:
        if regime_filter == [None]:
            filtered = all_sigs
            label = "all"
        else:
            filtered = [s for s in all_sigs if s["regime"] in regime_filter]
            label = "+".join(regime_filter)
        if len(filtered) < 20: continue
        r = run(filtered, k5, 0.60, EXPIRY, TP1, TP2, SL, TSL_T, TSL_D)
        if r: print(f"{label:>14} | {r['n']:>4} {r['per_day']:>5.1f} {r['wr']*100:>5.1f}% {r['avg']:>+7.2f}%")

    # ── EXPERIMENT 4: Accelerating momentum filter ──
    print("\n[EXP4] Accelerating momentum filter...", flush=True)
    print(f"{'Accel Filter':>14} | {'N':>4} {'N/day':>5} {'WR':>6} {'P&L':>7}")
    for accel in [None, True, False]:
        if accel is None:
            filtered = base_sigs
            label = "all"
        else:
            filtered = [s for s in base_sigs if s.get("accelerating") == accel]
            label = "accel=True" if accel else "accel=False"
        if len(filtered) < 20: continue
        r = run(filtered, k5, 0.60, EXPIRY, TP1, TP2, SL, TSL_T, TSL_D)
        if r: print(f"{label:>14} | {r['n']:>4} {r['per_day']:>5.1f} {r['wr']*100:>5.1f}% {r['avg']:>+7.2f}%")

    # ── EXPERIMENT 5: OTM options (strike offset) ──
    print("\n[EXP5] Strike offset (OTM options)...", flush=True)
    print(f"{'Strike OTM':>12} | {'N/day':>5} {'WR':>6} {'P&L':>7}")
    for otm_pct in [0.0, 0.5, 1.0, 1.5, 2.0]:
        pnls = []
        for s in base_sigs:
            spot = s["close"]
            # For Put OTM: strike is BELOW spot
            custom_strike = round(spot * (1 - otm_pct / 100) / 25) * 25
            T0 = EXPIRY / (24.0 * 365.0)
            hs = spread_pct = 2.0 / 200.0
            bs_mid = bs.price("P", spot, custom_strike, T0, 0.60)
            if bs_mid <= 0.01: continue
            entry = bs_mid * (1 + hs)

            path = [c for c in k5 if s["ts_ms"] < c["start_ms"] <= s["ts_ms"] + int(EXPIRY * 3600000)]
            if not path: continue
            pos1, pos2, e1, e2 = True, True, 0.0, 0.0
            tp1_p = entry * (1 + TP1) / (1 - hs)
            tp2_p = entry * (1 + TP2) / (1 - hs)
            tsl_floor = entry * (1 - SL) / (1 - hs)
            for i, c in enumerate(path):
                T = max(0, (EXPIRY - (i+1)*5/60) / (24*365))
                hi_p = bs.price("P", c["low"], custom_strike, T, 0.60)
                lo_p = bs.price("P", c["high"], custom_strike, T, 0.60)
                hi_pnl = (hi_p*(1-hs) - entry) / entry
                if TSL_T > 0 and hi_pnl >= TSL_T:
                    new_f = entry*(1+hi_pnl-TSL_D)/(1-hs)
                    if new_f > tsl_floor: tsl_floor = new_f
                if lo_p <= tsl_floor:
                    ep = (tsl_floor*(1-hs) - entry)/entry
                    e1 = ep if pos1 else e1; e2 = ep if pos2 else e2
                    break
                if pos2 and hi_p >= tp2_p: e2=(tp2_p*(1-hs)-entry)/entry; pos2=False
                if pos1 and hi_p >= tp1_p: e1=(tp1_p*(1-hs)-entry)/entry; pos1=False
                if not pos1 and not pos2: break
            else:
                T_last = max(0,(EXPIRY - len(path)*5/60)/(24*365))
                fp = (bs.price("P",path[-1]["close"],custom_strike,T_last,0.60)*(1-hs)-entry)/entry
                if pos1: e1=fp
                if pos2: e2=fp
            pnls.append(((e1+e2)/2)*100)

        if pnls:
            wr = sum(1 for p in pnls if p > 0) / len(pnls)
            avg = sum(pnls) / len(pnls)
            print(f"OTM {otm_pct:>4.1f}% | {len(pnls)/60:>5.1f} {wr*100:>5.1f}% {avg:>+7.2f}%")

    print("\n✅ Stage-2 complete.", flush=True)


if __name__ == "__main__":
    main()

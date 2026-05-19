"""
Advanced Multi-Dimensional ETH Options Backtest Optimizer.
Iteratively tests all combinations of:
  - Trailing Stop Loss (trigger + distance)
  - Score filters (reject overheated 9-10 signals)
  - Hour-of-day filters (best trading windows for mean-reversion)
  - ATR volatility filters (only trade when market has enough range)
  - Cooldown bars
  - Expiry / TP / SL

Goal: Find the peak combination for ~4 trades/day with max Avg P&L.
"""
import sys, os, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services import backtest_bs as bs
from services.backtest import generate_raw_signals
from services.backtest_data import fetch_set
from services.indicators import atr
from datetime import datetime, timezone

# ─────────────────────────────────────────────
# Option simulation with trailing stop loss
# ─────────────────────────────────────────────
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
    sl_p  = entry * (1 - sl)  / (1 - hs)
    
    # Dynamic trailing SL floor (starts at initial SL level)
    tsl_floor = sl_p

    path = [c for c in klines_5m if start_ms < c["start_ms"] <= start_ms + int(expiry_h * 3600000)]
    if not path:
        return None

    pos1, pos2 = True, True
    e1 = e2 = 0.0

    for i, c in enumerate(path):
        rem_h = expiry_h - (i + 1) * 5 / 60.0
        T = max(0.0, rem_h / (24.0 * 365.0))
        hi_s, lo_s = c["high"], c["low"]

        # For Put: high spot → SL, low spot → TP
        hi_p = bs.price(side, lo_s, strike, T, sigma) if side == "P" else bs.price(side, hi_s, strike, T, sigma)
        lo_p = bs.price(side, hi_s, strike, T, sigma) if side == "P" else bs.price(side, lo_s, strike, T, sigma)

        hi_pnl = (hi_p * (1 - hs) - entry) / entry

        # Update Trailing SL
        if tsl_trigger > 0 and hi_pnl >= tsl_trigger:
            new_floor = entry * (1 + hi_pnl - tsl_dist) / (1 - hs)
            if new_floor > tsl_floor:
                tsl_floor = new_floor

        # SL / TSL check (worst case lo_p)
        if lo_p <= tsl_floor:
            exit_p = (tsl_floor * (1 - hs) - entry) / entry
            e1 = exit_p if pos1 else e1
            e2 = exit_p if pos2 else e2
            pos1 = pos2 = False
            break

        # TP2
        if pos2 and hi_p >= tp2_p:
            e2 = (tp2_p * (1 - hs) - entry) / entry
            pos2 = False

        # TP1
        if pos1 and hi_p >= tp1_p:
            e1 = (tp1_p * (1 - hs) - entry) / entry
            pos1 = False

        if not pos1 and not pos2:
            break

    # Time stop
    if pos1 or pos2:
        lc = path[-1]
        T_last = max(0.0, (expiry_h - len(path) * 5 / 60.0) / (24.0 * 365.0))
        final = bs.price(side, lc["close"], strike, T_last, sigma)
        final_pnl = (final * (1 - hs) - entry) / entry
        if pos1: e1 = final_pnl
        if pos2: e2 = final_pnl

    return ((e1 + e2) / 2.0) * 100.0


# ─────────────────────────────────────────────
# Score filter & ATR filter helpers
# ─────────────────────────────────────────────
def get_hour_utc(ts_ms: int) -> int:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour

def compute_atr(klines_5m, idx, window=14):
    if idx < window:
        return 0
    chunk = klines_5m[max(0, idx - window): idx]
    if len(chunk) < 2:
        return 0
    trs = [max(c["high"] - c["low"],
               abs(c["high"] - chunk[i]["close"]),
               abs(c["low"]  - chunk[i]["close"]))
           for i, c in enumerate(chunk[1:], 1)]
    return sum(trs) / len(trs) if trs else 0


# ─────────────────────────────────────────────
# MAIN OPTIMIZATION LOOP
# ─────────────────────────────────────────────
def main():
    sigma = 0.60
    spread_pct = 2.0

    print("Fetching 60d ETH data...", flush=True)
    data = fetch_set("ETHUSDT", days=60, intervals=("5", "15", "60"))
    klines_5m = data["5"]
    klines_15m = data["15"]
    klines_1h  = data["60"]

    # Build index for ATR lookups
    ts_to_idx = {c["start_ms"]: i for i, c in enumerate(klines_5m)}

    # ── Stage 1: Cooldown sweep to collect signal pools ──
    print("Generating signal pools...", flush=True)
    pools = {}
    for cd in [4, 6, 8, 12]:
        sigs = generate_raw_signals(klines_5m, klines_15m, klines_1h,
                                    min_alignment=2, cooldown_bars=cd, fade=True)
        sigs = [s for s in sigs if s["side"] == "P" and s["regime"] == "trend" and s["mtf_aligned"] == 2]
        # Enrich with ATR and hour
        for s in sigs:
            idx = ts_to_idx.get(s["ts_ms"], 0)
            s["atr_5m"] = compute_atr(klines_5m, idx, 14)
            s["hour_utc"] = get_hour_utc(s["ts_ms"])
        pools[cd] = sigs
        print(f"  cooldown={cd} bars: {len(sigs)} signals", flush=True)

    # ── Stage 2: Full grid search ──
    print("\nRunning full grid search...", flush=True)

    # Parameter space
    expiries_h    = [72.0, 96.0, 120.0]
    tp1s          = [0.15, 0.20, 0.25, 0.30]
    tp2s          = [0.40, 0.50, 0.60]
    sls           = [0.30, 0.35, 0.40]
    tsl_combos    = [(0.0, 0.0), (0.20, 0.15), (0.25, 0.20), (0.30, 0.15)]
    score_maxes   = [10, 8]       # 10 = no filter, 8 = exclude score 9-10
    # ATR thresholds (min ATR in $): 0 = no filter, 5 = require volatile market
    atr_mins      = [0, 5, 8]
    # Allowed UTC hours: None = all day, "session_a"=8-16, "session_b"=0-8 or 20-24
    hour_filters  = [None, list(range(8, 20)), list(range(20, 24)) + list(range(0, 8))]

    results = []
    total_combos = (len(pools) * len(expiries_h) * len(tp1s) * len(tp2s) * len(sls)
                    * len(tsl_combos) * len(score_maxes) * len(atr_mins) * len(hour_filters))
    print(f"Total combinations: {total_combos}", flush=True)

    count = 0
    for cd, expiry, tp1, tp2, sl, (tsl_t, tsl_d), score_max, atr_min, hour_filter in itertools.product(
            pools.keys(), expiries_h, tp1s, tp2s, sls, tsl_combos, score_maxes, atr_mins, hour_filters):
        if tp2 <= tp1:
            continue

        sigs = pools[cd]

        # Apply filters
        filtered = sigs
        if score_max < 10:
            filtered = [s for s in filtered if s["score"] <= score_max]
        if atr_min > 0:
            filtered = [s for s in filtered if s["atr_5m"] >= atr_min]
        if hour_filter is not None:
            filtered = [s for s in filtered if s["hour_utc"] in hour_filter]

        n = len(filtered)
        if n < 30:  # Not enough data for statistical significance
            continue

        pnls = []
        for s in filtered:
            pnl = simulate_with_tsl(s, klines_5m, sigma, expiry, tp1, tp2, sl, tsl_t, tsl_d, spread_pct)
            if pnl is not None:
                pnls.append(pnl)

        if not pnls:
            continue

        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        avg = sum(pnls) / len(pnls)
        n_signals = len(pnls)
        per_day = n_signals / 60.0

        # Save if meaningful
        results.append({
            "cd": cd, "expiry": expiry, "tp1": tp1, "tp2": tp2, "sl": sl,
            "tsl_t": tsl_t, "tsl_d": tsl_d, "score_max": score_max,
            "atr_min": atr_min, "hour_filter": "all" if hour_filter is None else ("day" if 8 in (hour_filter or []) else "night"),
            "n": n_signals, "per_day": per_day, "wr": wr, "avg_pnl": avg,
            "ev_per_day": avg * per_day
        })

        count += 1
        if count % 500 == 0:
            print(f"  ...{count} combos evaluated", flush=True)

    # ── Stage 3: Rank & Report ──
    # Sort by avg_pnl for raw profitability 
    top_by_pnl = sorted([r for r in results if 3.5 <= r["per_day"] <= 6.0], 
                         key=lambda x: x["avg_pnl"], reverse=True)[:10]
    
    # Sort by EV per day (frequency × profitability)
    top_by_ev = sorted([r for r in results if 3.0 <= r["per_day"] <= 6.0], 
                        key=lambda x: x["ev_per_day"], reverse=True)[:10]

    print("\n" + "=" * 100)
    print("TOP 10 BY AVERAGE P&L PER TRADE (4-6 trades/day filter)")
    print("=" * 100)
    print(f"{'CD':>3} {'EXP':>5} {'TP1':>5} {'TP2':>5} {'SL':>5} {'TSL_T':>6} {'TSL_D':>6} {'SCORE':>6} {'ATR':>4} {'HRS':>5} | {'N/day':>5} {'WR':>6} {'P&L':>7}")
    print("-" * 100)
    for r in top_by_pnl:
        print(f"{r['cd']:>3} {r['expiry']:>5.0f} {r['tp1']:>5.2f} {r['tp2']:>5.2f} {r['sl']:>5.2f} "
              f"{r['tsl_t']:>6.2f} {r['tsl_d']:>6.2f} {r['score_max']:>6} {r['atr_min']:>4} {r['hour_filter']:>5} | "
              f"{r['per_day']:>5.1f} {r['wr']*100:>5.1f}% {r['avg_pnl']:>+7.2f}%")

    print("\n" + "=" * 100)
    print("TOP 10 BY EV PER DAY = avg_pnl × trades_per_day (4-6 trades/day filter)")
    print("=" * 100)
    print(f"{'CD':>3} {'EXP':>5} {'TP1':>5} {'TP2':>5} {'SL':>5} {'TSL_T':>6} {'TSL_D':>6} {'SCORE':>6} {'ATR':>4} {'HRS':>5} | {'N/day':>5} {'WR':>6} {'P&L':>7} {'EV/day':>8}")
    print("-" * 100)
    for r in top_by_ev:
        print(f"{r['cd']:>3} {r['expiry']:>5.0f} {r['tp1']:>5.2f} {r['tp2']:>5.2f} {r['sl']:>5.2f} "
              f"{r['tsl_t']:>6.2f} {r['tsl_d']:>6.2f} {r['score_max']:>6} {r['atr_min']:>4} {r['hour_filter']:>5} | "
              f"{r['per_day']:>5.1f} {r['wr']*100:>5.1f}% {r['avg_pnl']:>+7.2f}% {r['ev_per_day']:>+8.2f}")

    # Best single result
    if results:
        best = max(results, key=lambda x: x["avg_pnl"])
        print(f"\n🏆 ABSOLUTE BEST P&L (no frequency filter):")
        print(f"   CD={best['cd']}, EXP={best['expiry']:.0f}h, TP1={best['tp1']:.0f}%, TP2={best['tp2']:.0f}%, SL={best['sl']:.0f}%")
        print(f"   TSL Trigger={best['tsl_t']*100:.0f}%, Dist={best['tsl_d']*100:.0f}%")
        print(f"   Score≤{best['score_max']}, ATR≥{best['atr_min']}, Hours={best['hour_filter']}")
        print(f"   → {best['n']} signals ({best['per_day']:.1f}/day), WR={best['wr']*100:.1f}%, Avg P&L={best['avg_pnl']:+.2f}%")

if __name__ == "__main__":
    main()

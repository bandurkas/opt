"""Research: Regime-specific exits and volatility targeting.

Instead of filtering signals (which kills frequency), optimize:
  1. Different TP/SL for different volatility regimes
  2. Different hold_h for different market conditions
  3. Volatility-adjusted position sizing
  4. Time-of-day / day-of-week patterns
  5. Weekly expiry cycle effects

Run:
    cd backend && PYTHONPATH=. python3 services/research_regime_exits.py
"""
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir, load_local
from services.check_asymmetric_thresholds import (
    generate_signals, PUT_EXIT, CALL_EXIT,
)
from services.strategy_config import PUT_RET_MAX, CALL_RET_MIN
from services.retest_asymmetric_365d import (
    apply_cb, sim_stats, BARS_7D, CONSISTENT_CD, PUT_GEN, CALL_GEN,
)
from services.indicators import ema, realized_vol


def classify_vol_regime(closes_1h, idx):
    """Classify volatility regime at a given index.
    Returns: 'low', 'medium', 'high'"""
    if idx < 50:
        return "unknown"
    rv = realized_vol(closes_1h[:idx+1], lookback=24)
    if rv is None:
        return "unknown"
    # Compare to rolling vol distribution
    rolling_vols = []
    for j in range(20, idx+1):
        r = realized_vol(closes_1h[:j+1], lookback=24)
        if r is not None:
            rolling_vols.append(r)
    if len(rolling_vols) < 30:
        return "unknown"
    sorted_vols = sorted(rolling_vols)
    threshold_33 = sorted_vols[int(len(sorted_vols) * 0.33)]
    threshold_67 = sorted_vols[int(len(sorted_vols) * 0.67)]
    if rv < threshold_33:
        return "low"
    elif rv < threshold_67:
        return "medium"
    else:
        return "high"


def classify_trend_regime(k5, idx):
    """Classify trend regime: 'uptrend', 'downtrend', 'range'"""
    if idx < 200:
        return "unknown"
    closes = [c["close"] for c in k5[max(0,idx-200):idx+1]]
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    if e50 and e200 and e200 > 0:
        ratio = e50 / e200
        if ratio > 1.03:
            return "uptrend"
        elif ratio < 0.97:
            return "downtrend"
        else:
            return "range"
    return "unknown"


def get_time_features(ts_ms):
    """Get time-based features."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return {
        "hour": dt.hour,
        "day_of_week": dt.weekday(),  # 0=Mon, 6=Sun
        "is_weekend": dt.weekday() >= 5,
        "is_asia_session": 0 <= dt.hour < 8,
        "is_europe_session": 7 <= dt.hour < 16,
        "is_us_session": 13 <= dt.hour < 22,
    }


def test_exit_variations(sigs, k5, k15, k1h, exit_configs):
    """Test different exit configurations on the SAME signals."""
    results = []
    
    for name, exit_kw in exit_configs:
        ps = [s for s in sigs if s["side"] == "P"]
        cs = [s for s in sigs if s["side"] == "C"]
        
        # Apply per-side exits
        psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=exit_kw.get("put_tp1", 0.50), tp2_pct=exit_kw.get("put_tp2", 0.70),
            sl_pct=exit_kw.get("put_sl", 1.50), option_horizon_h=exit_kw.get("put_hold", 96),
            spread_pct=2.0) if ps else []
        csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=exit_kw.get("call_tp1", 0.30), tp2_pct=exit_kw.get("call_tp2", 0.50),
            sl_pct=exit_kw.get("call_sl", 1.00), option_horizon_h=exit_kw.get("call_hold", 24),
            spread_pct=2.0) if cs else []
        
        pnls = [s["option"]["pnl_pct"] for s in psim + csim if "pnl_pct" in s.get("option", {})]
        if not pnls:
            continue
        
        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        avg = statistics.mean(pnls)
        st = statistics.stdev(pnls) if len(pnls) > 1 else 0
        sh = (avg / st) if st > 0 else 0
        mc = cl = 0
        for p in pnls:
            if p < 0: cl += 1; mc = max(mc, cl)
            else: cl = 0
        
        results.append({
            "name": name, "n": len(pnls), "wr": wr, "avg": avg,
            "sharpe": sh, "cl": mc,
        })
    
    return results


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Regime-Specific Exits & Vol Targeting Research ===", flush=True)
    
    k5, k15, k1h = load_local(data_dir)
    
    # Generate Config B signals
    print(f"\n[1] Generating Config B signals...", flush=True)
    sigs = generate_signals(k5, k15, k1h, 0, PUT_RET_MAX, CALL_RET_MIN)
    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]
    print(f"  Total: {len(sigs)} P={len(ps)} C={len(cs)}", flush=True)
    
    # ── Test 1: Exit variations ──
    print(f"\n[2] Testing exit variations...", flush=True)
    
    exit_configs = [
        ("Baseline", {"put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 96,
                      "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.00, "call_hold": 24}),
        ("Wider SL (Put)", {"put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 2.00, "put_hold": 96,
                            "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.00, "call_hold": 24}),
        ("Wider SL (Both)", {"put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 2.00, "put_hold": 96,
                             "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.50, "call_hold": 24}),
        ("Tighter TP (Put)", {"put_tp1": 0.40, "put_tp2": 0.60, "put_sl": 1.50, "put_hold": 96,
                              "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.00, "call_hold": 24}),
        ("Longer Hold (Put)", {"put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 120,
                               "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.00, "call_hold": 24}),
        ("Shorter Hold (Call)", {"put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 96,
                                 "call_tp1": 0.25, "call_tp2": 0.45, "call_sl": 0.75, "call_hold": 12}),
        ("No TP1 (Put)", {"put_tp1": 0.70, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 96,
                          "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.00, "call_hold": 24}),
        ("Very Wide SL", {"put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 3.00, "put_hold": 96,
                          "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 2.00, "call_hold": 24}),
        ("Max Theta (Put 168h)", {"put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 168,
                                  "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.00, "call_hold": 24}),
    ]
    
    results = test_exit_variations(sigs, k5, k15, k1h, exit_configs)
    
    print(f"\n{'Config':<25} {'n':>4} {'WR':>6} {'avg':>8} {'sh':>6} {'cl':>4}")
    print("-" * 55)
    baseline_avg = results[0]["avg"] if results else 0
    for r in results:
        delta = r["avg"] - baseline_avg
        flag = "✅" if delta > 2 else "⚠️" if delta > 0 else "❌"
        print(f"{flag} {r['name']:<23} {r['n']:>4} {r['wr']*100:>5.1f}% {r['avg']:>+7.2f}% "
              f"{r['sharpe']:>+5.2f} {r['cl']:>4}")
    
    # ── Test 2: Vol regime exits ──
    print(f"\n[3] Testing vol regime-specific exits...", flush=True)
    
    # Classify each signal by vol regime
    vol_sigs = {"low": [], "medium": [], "high": []}
    closes_1h = [c["close"] for c in k1h]
    
    for s in sigs:
        idx = s["idx_5m"]
        regime = classify_vol_regime(closes_1h, idx)
        if regime in vol_sigs:
            vol_sigs[regime].append(s)
    
    for regime, regime_sigs in vol_sigs.items():
        if len(regime_sigs) < 10:
            continue
        print(f"\n  Vol regime: {regime} ({len(regime_sigs)} signals)")
        
        regime_exits = [
            ("Baseline", {"put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 96,
                          "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.00, "call_hold": 24}),
            ("Wider SL", {"put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 2.50, "put_hold": 96,
                          "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 2.00, "call_hold": 24}),
            ("Tighter TP", {"put_tp1": 0.30, "put_tp2": 0.50, "put_sl": 1.50, "put_hold": 96,
                            "call_tp1": 0.20, "call_tp2": 0.40, "call_sl": 1.00, "call_hold": 24}),
            ("Longer Hold", {"put_tp1": 0.50, "put_tp2": 0.70, "put_sl": 1.50, "put_hold": 144,
                             "call_tp1": 0.30, "call_tp2": 0.50, "call_sl": 1.00, "call_hold": 48}),
        ]
        
        regime_results = test_exit_variations(regime_sigs, k5, k15, k1h, regime_exits)
        r_baseline = regime_results[0]["avg"] if regime_results else 0
        for r in regime_results:
            delta = r["avg"] - r_baseline
            flag = "✅" if delta > 3 else "⚠️" if delta > 0 else "❌"
            print(f"    {flag} {r['name']:<15} n={r['n']:>3} avg={r['avg']:+.2f}% (Δ{delta:+.2f}) "
                  f"WR={r['wr']*100:.1f}% sh={r['sharpe']:+.3f} cl={r['cl']}")
    
    # ── Test 3: Time-of-day patterns ──
    print(f"\n[4] Testing time-of-day patterns...", flush=True)
    
    # Simulate baseline
    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ps else []
    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if cs else []
    
    sig_pnl = {}
    for s in psim + csim:
        pnl = s["option"].get("pnl_pct")
        if pnl is not None:
            sig_pnl[s["ts_ms"]] = pnl
    
    # Group by hour
    hourly = {}
    for s in sigs:
        ts = s["ts_ms"]
        if ts in sig_pnl:
            hour = datetime.fromtimestamp(ts/1000, tz=timezone.utc).hour
            hourly.setdefault(hour, []).append(sig_pnl[ts])
    
    print(f"\n  {'Hour (UTC)':>12} {'n':>4} {'avg':>8} {'WR':>6}")
    print(f"  {'-'*32}")
    for hour in sorted(hourly):
        pnls = hourly[hour]
        if len(pnls) >= 5:
            avg = statistics.mean(pnls)
            wr = sum(1 for p in pnls if p > 0) / len(pnls)
            flag = "✅" if avg > 15 else "⚠️" if avg > 5 else "❌"
            print(f"  {flag} {hour:02d}:00       {len(pnls):>4} {avg:>+7.2f}% {wr*100:>5.1f}%")
    
    # Group by day of week
    daily = {}
    for s in sigs:
        ts = s["ts_ms"]
        if ts in sig_pnl:
            dow = datetime.fromtimestamp(ts/1000, tz=timezone.utc).weekday()
            daily.setdefault(dow, []).append(sig_pnl[ts])
    
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print(f"\n  {'Day':>6} {'n':>4} {'avg':>8} {'WR':>6}")
    print(f"  {'-'*26}")
    for dow in range(7):
        if dow in daily and len(daily[dow]) >= 5:
            pnls = daily[dow]
            avg = statistics.mean(pnls)
            wr = sum(1 for p in pnls if p > 0) / len(pnls)
            flag = "✅" if avg > 15 else "⚠️" if avg > 5 else "❌"
            print(f"  {flag} {day_names[dow]:>4}   {len(pnls):>4} {avg:>+7.2f}% {wr*100:>5.1f}%")
    
    print(f"\nDone ({round(time.time()-t0,1)}s)", flush=True)


if __name__ == "__main__":
    main()

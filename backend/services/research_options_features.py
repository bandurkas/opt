"""Research: Options chain features — IV skew, OI walls, gamma exposure, term structure.

We already poll the full options chain every 30s. This research extracts
alpha from options-specific features:

  1. IV Skew — Put IV vs Call IV at ATM. If Put IV > Call IV, market fears downside.
  2. IV Term Structure — Near-term IV vs far-term IV. Backwardation = near-term stress.
  3. Max Pain — strike where total option value is minimized for holders.
  4. OI Walls — strikes with abnormally high OI act as support/resistance.
  5. Put/Call OI Ratio — sentiment indicator.
  6. Gamma Exposure — estimated dealer gamma by strike (requires delta chain).
  7. Realized vs Implied Vol spread — over/underpriced premium.
  8. Volume/OI divergence — unusual activity.
  9. Theta decay acceleration — non-linear near expiry.
  10. Dynamic exit — vol-adjusted TP/SL instead of fixed %.

Strategy: poll live chain → compute features → merge with signal timestamps →
test as filters on historical signals.

Since we don't have historical chain data, we'll:
  A) Generate synthetic chain features from kline-based proxies
  B) Test on the overlap window where we DO have chain snapshots in DB
  
Run on VPS (has DB access):
    cd /root/opt-app/backend && PYTHONPATH=. python3 services/research_options_features.py

Or locally with kline-only proxies:
    cd backend && PYTHONPATH=. python3 services/research_options_features.py --mode proxy
"""
import json
import math
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
from services.indicators import ema, realized_vol, rsi

MODE = "proxy"
if "--mode" in sys.argv:
    MODE = sys.argv[sys.argv.index("--mode") + 1]

# ──────────── Kline-based options proxies ────────────

def compute_options_proxies(k5, k15, k1h, idx):
    """Compute options-chain proxies from kline data.
    
    These approximate what we'd get from the options chain:
    1. IV proxy: 24h realized vol (market-makers price off RV)
    2. IV term structure: compare 1h vs 4h RV (near vs far vol)
    3. Put/Call sentiment: RSI extremity → implied fear/greed
    4. Gamma proxy: ATR / spot (high gamma = big expected moves)
    5. Theta decay: time-based theta acceleration
    6. Realized vs implied vol spread
    7. Volatility regime change (RV acceleration)
    """
    proxies = {}
    
    if idx < 50:
        return proxies
    
    closes_5m = [c["close"] for c in k5[max(0,idx-100):idx+1]]
    closes_1h = [c["close"] for c in k1h[max(0,idx-200):idx+1]]
    
    # 1. IV proxy (24h RV)
    if len(closes_1h) >= 25:
        rv_24h = realized_vol(closes_1h, lookback=24)
        if rv_24h is not None:
            proxies["iv_proxy"] = rv_24h
    
    # 2. IV term structure (1h RV vs 4h RV)
    if len(closes_1h) >= 100:
        rv_1h_short = realized_vol(closes_1h[-25:], lookback=24)
        rv_4h_long = realized_vol(closes_1h, lookback=96)
        if rv_1h_short and rv_4h_long and rv_4h_long > 0:
            # Positive = backwardation (near vol > far vol = stress)
            proxies["iv_ts_spread"] = (rv_1h_short - rv_4h_long) / rv_4h_long
    
    # 3. Put/Call sentiment proxy (RSI → implied positioning)
    if len(closes_5m) >= 15:
        rsi_val = rsi(closes_5m[-15:], 14)
        if rsi_val is not None:
            # RSI < 30 → fear (Put demand → Put IV high)
            # RSI > 70 → greed (Call demand → Call IV high)
            proxies["pc_sentiment"] = (rsi_val - 50) / 50  # -1 to +1
    
    # 4. Gamma proxy (ATR / spot)
    if len(k5) > idx - 14:
        from services.indicators import atr
        candles = k5[max(0,idx-14):idx+1]
        if len(candles) >= 15:
            atr_val = atr(candles, 14)
            if atr_val and k5[idx]["close"] > 0:
                proxies["gamma_proxy"] = atr_val / k5[idx]["close"]
    
    # 5. Theta decay acceleration (RV change rate)
    if len(closes_1h) >= 50:
        rv_now = realized_vol(closes_1h, lookback=24)
        rv_prev = realized_vol(closes_1h[:-24], lookback=24) if len(closes_1h) >= 48 else None
        if rv_now and rv_prev and rv_prev > 0:
            proxies["theta_accel"] = (rv_now - rv_prev) / rv_prev
    
    # 6. Realized vs Implied Vol spread
    # Implied vol of 7-day ATM options ≈ 0.6 (our BS assumption)
    # Compare to 24h realized vol
    if len(closes_1h) >= 25:
        rv_24h = realized_vol(closes_1h, lookback=24)
        if rv_24h:
            # Positive = IV > RV (options overpriced = good for selling)
            # Negative = IV < RV (options underpriced = bad for selling)
            proxies["rv_iv_spread"] = 0.6 - rv_24h  # BS sigma - RV
    
    # 7. Volatility regime change (RV acceleration)
    if len(closes_1h) >= 72:
        rv_short = realized_vol(closes_1h[-25:], lookback=24)
        rv_med = realized_vol(closes_1h[-49:], lookback=24)
        if rv_short and rv_med and rv_med > 0:
            proxies["vol_regime_change"] = (rv_short - rv_med) / rv_med
    
    # 8. Price momentum vs vol (Sharpe-like)
    if len(closes_1h) >= 25:
        returns_24h = (closes_1h[-1] - closes_1h[-25]) / closes_1h[-25] * 100
        rv_24h = realized_vol(closes_1h, lookback=24)
        if rv_24h and rv_24h > 0:
            proxies["return_per_vol"] = returns_24h / rv_24h
    
    # 9. Mean reversion pressure (distance from EMA20 / ATR)
    if len(closes_5m) >= 20:
        e20 = ema(closes_5m, 20)
        if e20:
            from services.indicators import atr
            candles = k5[max(0,idx-14):idx+1]
            if len(candles) >= 15:
                atr_val = atr(candles, 14)
                if atr_val and atr_val > 0:
                    dist = (closes_5m[-1] - e20) / atr_val
                    proxies["mean_rev_pressure"] = dist
    
    return proxies


def gen_signals_with_options_proxies(k5, k15, k1h, cutoff_ms):
    """Generate Config B signals with options proxies attached."""
    out = []
    last_idx = -10000
    i15 = 0
    i1h = 0
    HIST = 240
    
    for i, c5 in enumerate(k5):
        ts_end = c5["start_ms"] + 5 * 60 * 1000
        while i15 < len(k15) and k15[i15]["start_ms"] + 15 * 60 * 1000 <= ts_end:
            i15 += 1
        while i1h < len(k1h) and k1h[i1h]["start_ms"] + 60 * 60 * 1000 <= ts_end:
            i1h += 1
        
        if i < 60 or i < BARS_7D:
            continue
        
        s5 = k5[max(0, i + 1 - HIST):i + 1]
        s15 = k15[max(0, i15 - HIST):i15]
        s1h = k1h[max(0, i1h - HIST):i1h]
        if len(s5) < 50 or len(s15) < 50 or len(s1h) < 200:
            continue
        
        ret_7d = (k5[i]["close"] - k5[i - BARS_7D]["close"]) / k5[i - BARS_7D]["close"] * 100 if k5[i - BARS_7D]["close"] > 0 else 0
        
        if ret_7d < PUT_RET_MAX:
            side = "P"
        elif ret_7d > CALL_RET_MIN:
            side = "C"
        else:
            continue
        
        gen_kw = PUT_GEN if side == "P" else CALL_GEN
        
        closes_1h = [c["close"] for c in s1h]
        if len(closes_1h) < 188:
            continue
        rolling_vols = []
        for j in range(20, len(closes_1h)):
            rv = realized_vol(closes_1h[:j + 1], lookback=24)
            if rv is not None:
                rolling_vols.append(rv)
        if len(rolling_vols) < 30:
            continue
        current_vol = rolling_vols[-1]
        sorted_vols = sorted(rolling_vols)
        threshold = sorted_vols[int(len(sorted_vols) * gen_kw["vol_threshold"])]
        if current_vol < threshold:
            continue
        
        from services.momentum_mtf import analyze_tf, consensus
        from services.regime import detect_regime
        regime = detect_regime(s1h)
        rn = regime.get("regime", "unknown")
        if rn == "trend" or rn not in gen_kw["regime_filter"]:
            continue
        
        mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
        md = gen_kw["mtf_direction_filter"]
        if md == "up" and (mtf["direction"] != "up" or mtf["tfs_aligned"] < 2):
            continue
        if md == "down" and (mtf["direction"] != "down" or mtf["tfs_aligned"] < 2):
            continue
        
        if side == "P" and gen_kw["bull_market_ratio_max"] is not None and len(closes_1h) >= 200:
            e50 = ema(closes_1h, 50)
            e200 = ema(closes_1h, 200)
            if e50 and e200 and e200 > 0 and e50 / e200 > gen_kw["bull_market_ratio_max"]:
                continue
        
        if i - last_idx < CONSISTENT_CD:
            continue
        
        # Compute options proxies
        proxies = compute_options_proxies(k5, k15, k1h, i)
        
        sig = {
            "idx_5m": i, "ts_ms": ts_end, "close": c5["close"],
            "side": side, "position": "short_premium",
            "ret_7d": round(ret_7d, 2),
            **proxies,
        }
        if ts_end >= cutoff_ms:
            out.append(sig)
        last_idx = i
    
    return out


# ──────────── Dynamic exit optimization ────────────

def test_dynamic_exits(sigs_with_pnl, k5):
    """Test various dynamic exit strategies vs fixed TP/SL."""
    # For each signal, simulate with different exit rules
    results = []
    
    # Get baseline PnLs
    baseline_pnls = [pnl for _, pnl in sigs_with_pnl]
    baseline_avg = statistics.mean(baseline_pnls)
    
    # Test: trailing stop at various triggers
    for tsl_trigger in [0.10, 0.15, 0.20, 0.25, 0.30]:
        for tsl_offset in [0.05, 0.10, 0.15, 0.20]:
            # Simple trailing stop simulation
            adjusted_pnls = []
            for sig, pnl in sigs_with_pnl:
                # If pnl > tsl_trigger, lock in tsl_trigger - offset
                if pnl > tsl_trigger * 100:
                    adjusted_pnl = (tsl_trigger - tsl_offset) * 100
                else:
                    adjusted_pnl = pnl
                adjusted_pnls.append(adjusted_pnl)
            
            adj_avg = statistics.mean(adjusted_pnls)
            delta = adj_avg - baseline_avg
            results.append({
                "name": f"TSL trigger={tsl_trigger:.0%} offset={tsl_offset:.0%}",
                "avg": adj_avg, "delta": delta,
            })
    
    # Test: time-stop optimization
    for hold_h in [12, 24, 48, 72, 96, 120, 144, 168]:
        # This requires re-simulating — skip for proxy mode
        pass
    
    # Sort by delta
    results.sort(key=lambda x: x["delta"], reverse=True)
    return results


# ──────────── Main ────────────

def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Options Chain Features Research (mode={MODE}) ===", flush=True)
    
    k5, k15, k1h = load_local(data_dir)
    print(f"klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}", flush=True)
    
    # Generate signals with options proxies
    print(f"\n[1] Generating signals with options proxies...", flush=True)
    all_sigs = gen_signals_with_options_proxies(k5, k15, k1h, 0)
    print(f"  Total signals: {len(all_sigs)}", flush=True)
    
    # Count proxy availability
    proxy_counts = {}
    for sig in all_sigs:
        for key in sig:
            if key not in ["idx_5m", "ts_ms", "close", "side", "position", "ret_7d"]:
                proxy_counts[key] = proxy_counts.get(key, 0) + 1
    
    print(f"  Options proxies available:", flush=True)
    for feat, count in sorted(proxy_counts.items(), key=lambda x: -x[1]):
        print(f"    {feat}: {count}/{len(all_sigs)} ({count/len(all_sigs)*100:.0f}%)", flush=True)
    
    # Simulate
    print(f"\n[2] Simulating...", flush=True)
    ps = [s for s in all_sigs if s["side"] == "P"]
    cs = [s for s in all_sigs if s["side"] == "C"]
    
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
            sig_pnl[s["ts_ms"]] = (s, pnl)
    
    sigs_with_pnl = [(s, sig_pnl[s["ts_ms"]][1]) for s in all_sigs if s["ts_ms"] in sig_pnl]
    
    baseline_pnls = [p for _, p in sigs_with_pnl]
    baseline_avg = statistics.mean(baseline_pnls)
    baseline_wr = sum(1 for p in baseline_pnls if p > 0) / len(baseline_pnls)
    baseline_n = len(baseline_pnls)
    
    print(f"  Baseline: n={baseline_n} WR={baseline_wr*100:.1f}% avg={baseline_avg:+.2f}%")
    
    # ── Test options proxy filters ──
    print(f"\n[3] Testing options proxy filters...", flush=True)
    all_results = []
    
    # IV proxy (realized vol as proxy for implied vol)
    for feat in ["iv_proxy"]:
        for thresh in [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]:
            filtered = [pnl for s, pnl in sigs_with_pnl if s.get(feat) is not None and s.get(feat) > thresh]
            if len(filtered) >= 5:
                orig_avg = statistics.mean([p for _, p in sigs_with_pnl])
                filt_avg = statistics.mean(filtered)
                delta = filt_avg - orig_avg
                kept = len(filtered) / len(sigs_with_pnl) * 100
                wr = sum(1 for p in filtered if p > 0) / len(filtered)
                st = statistics.stdev(filtered) if len(filtered) > 1 else 0
                sh = (filt_avg / st) if st > 0 else 0
                mc = cl = 0
                for p in filtered:
                    if p < 0: cl += 1; mc = max(mc, cl)
                    else: cl = 0
                
                flag = "✅" if delta > 3 and kept > 40 else "⚠️" if delta > 0 else "❌"
                print(f"{flag} {feat} > {thresh:.2f}{'':>15} n={len(filtered):>3} ({kept:>5.0f}%)  "
                      f"avg={filt_avg:+.2f}% (Δ{delta:+.2f})  WR={wr*100:.1f}%  sh={sh:+.3f}  cl={mc}")
                
                all_results.append({
                    "name": f"{feat} > {thresh:.2f}", "n_filt": len(filtered),
                    "kept": kept, "filt_avg": filt_avg, "delta": delta,
                    "wr": wr, "sh": sh, "mc": mc,
                })
    
    # IV term structure spread
    for feat in ["iv_ts_spread"]:
        for thresh in [-0.5, -0.2, 0, 0.2, 0.5]:
            filtered = [pnl for s, pnl in sigs_with_pnl if s.get(feat) is not None and s.get(feat) > thresh]
            if len(filtered) >= 5:
                orig_avg = statistics.mean([p for _, p in sigs_with_pnl])
                filt_avg = statistics.mean(filtered)
                delta = filt_avg - orig_avg
                kept = len(filtered) / len(sigs_with_pnl) * 100
                wr = sum(1 for p in filtered if p > 0) / len(filtered)
                st = statistics.stdev(filtered) if len(filtered) > 1 else 0
                sh = (filt_avg / st) if st > 0 else 0
                flag = "✅" if delta > 3 and kept > 40 else "⚠️" if delta > 0 else "❌"
                print(f"{flag} {feat} > {thresh:+.2f}{'':>13} n={len(filtered):>3} ({kept:>5.0f}%)  "
                      f"avg={filt_avg:+.2f}% (Δ{delta:+.2f})  WR={wr*100:.1f}%  sh={sh:+.3f}")
                
                all_results.append({
                    "name": f"{feat} > {thresh:+.2f}", "n_filt": len(filtered),
                    "kept": kept, "filt_avg": filt_avg, "delta": delta,
                    "wr": wr, "sh": sh, "mc": 0,
                })
    
    # Put/Call sentiment
    for feat in ["pc_sentiment"]:
        for thresh in [-0.5, -0.3, 0, 0.3, 0.5]:
            filtered = [pnl for s, pnl in sigs_with_pnl if s.get(feat) is not None and s.get(feat) > thresh]
            if len(filtered) >= 5:
                orig_avg = statistics.mean([p for _, p in sigs_with_pnl])
                filt_avg = statistics.mean(filtered)
                delta = filt_avg - orig_avg
                kept = len(filtered) / len(sigs_with_pnl) * 100
                wr = sum(1 for p in filtered if p > 0) / len(filtered)
                st = statistics.stdev(filtered) if len(filtered) > 1 else 0
                sh = (filt_avg / st) if st > 0 else 0
                flag = "✅" if delta > 3 and kept > 40 else "⚠️" if delta > 0 else "❌"
                print(f"{flag} {feat} > {thresh:+.2f}{'':>13} n={len(filtered):>3} ({kept:>5.0f}%)  "
                      f"avg={filt_avg:+.2f}% (Δ{delta:+.2f})  WR={wr*100:.1f}%  sh={sh:+.3f}")
    
    # Gamma proxy (ATR/spot)
    for feat in ["gamma_proxy"]:
        for thresh in [0.005, 0.01, 0.015, 0.02, 0.03]:
            filtered = [pnl for s, pnl in sigs_with_pnl if s.get(feat) is not None and s.get(feat) > thresh]
            if len(filtered) >= 5:
                orig_avg = statistics.mean([p for _, p in sigs_with_pnl])
                filt_avg = statistics.mean(filtered)
                delta = filt_avg - orig_avg
                kept = len(filtered) / len(sigs_with_pnl) * 100
                wr = sum(1 for p in filtered if p > 0) / len(filtered)
                st = statistics.stdev(filtered) if len(filtered) > 1 else 0
                sh = (filt_avg / st) if st > 0 else 0
                flag = "✅" if delta > 3 and kept > 40 else "⚠️" if delta > 0 else "❌"
                print(f"{flag} {feat} > {thresh:.4f}{'':>10} n={len(filtered):>3} ({kept:>5.0f}%)  "
                      f"avg={filt_avg:+.2f}% (Δ{delta:+.2f})  WR={wr*100:.1f}%  sh={sh:+.3f}")
    
    # RV vs IV spread (sell when IV > RV)
    for feat in ["rv_iv_spread"]:
        for thresh in [-0.2, -0.1, 0, 0.1, 0.2, 0.3]:
            filtered = [pnl for s, pnl in sigs_with_pnl if s.get(feat) is not None and s.get(feat) > thresh]
            if len(filtered) >= 5:
                orig_avg = statistics.mean([p for _, p in sigs_with_pnl])
                filt_avg = statistics.mean(filtered)
                delta = filt_avg - orig_avg
                kept = len(filtered) / len(sigs_with_pnl) * 100
                wr = sum(1 for p in filtered if p > 0) / len(filtered)
                st = statistics.stdev(filtered) if len(filtered) > 1 else 0
                sh = (filt_avg / st) if st > 0 else 0
                flag = "✅" if delta > 3 and kept > 40 else "⚠️" if delta > 0 else "❌"
                print(f"{flag} {feat} > {thresh:+.2f}{'':>13} n={len(filtered):>3} ({kept:>5.0f}%)  "
                      f"avg={filt_avg:+.2f}% (Δ{delta:+.2f})  WR={wr*100:.1f}%  sh={sh:+.3f}")
    
    # Vol regime change
    for feat in ["vol_regime_change"]:
        for thresh in [-0.5, -0.2, 0, 0.2, 0.5]:
            filtered = [pnl for s, pnl in sigs_with_pnl if s.get(feat) is not None and s.get(feat) > thresh]
            if len(filtered) >= 5:
                orig_avg = statistics.mean([p for _, p in sigs_with_pnl])
                filt_avg = statistics.mean(filtered)
                delta = filt_avg - orig_avg
                kept = len(filtered) / len(sigs_with_pnl) * 100
                wr = sum(1 for p in filtered if p > 0) / len(filtered)
                st = statistics.stdev(filtered) if len(filtered) > 1 else 0
                sh = (filt_avg / st) if st > 0 else 0
                flag = "✅" if delta > 3 and kept > 40 else "⚠️" if delta > 0 else "❌"
                print(f"{flag} {feat} > {thresh:+.2f}{'':>13} n={len(filtered):>3} ({kept:>5.0f}%)  "
                      f"avg={filt_avg:+.2f}% (Δ{delta:+.2f})  WR={wr*100:.1f}%  sh={sh:+.3f}")
    
    # Return per vol (Sharpe-like)
    for feat in ["return_per_vol"]:
        for thresh in [-2, -1, 0, 1, 2]:
            filtered = [pnl for s, pnl in sigs_with_pnl if s.get(feat) is not None and s.get(feat) > thresh]
            if len(filtered) >= 5:
                orig_avg = statistics.mean([p for _, p in sigs_with_pnl])
                filt_avg = statistics.mean(filtered)
                delta = filt_avg - orig_avg
                kept = len(filtered) / len(sigs_with_pnl) * 100
                wr = sum(1 for p in filtered if p > 0) / len(filtered)
                st = statistics.stdev(filtered) if len(filtered) > 1 else 0
                sh = (filt_avg / st) if st > 0 else 0
                flag = "✅" if delta > 3 and kept > 40 else "⚠️" if delta > 0 else "❌"
                print(f"{flag} {feat} > {thresh:+.1f}{'':>14} n={len(filtered):>3} ({kept:>5.0f}%)  "
                      f"avg={filt_avg:+.2f}% (Δ{delta:+.2f})  WR={wr*100:.1f}%  sh={sh:+.3f}")
    
    # Mean reversion pressure
    for feat in ["mean_rev_pressure"]:
        for thresh in [-3, -2, -1, 0, 1, 2, 3]:
            filtered = [pnl for s, pnl in sigs_with_pnl if s.get(feat) is not None and s.get(feat) > thresh]
            if len(filtered) >= 5:
                orig_avg = statistics.mean([p for _, p in sigs_with_pnl])
                filt_avg = statistics.mean(filtered)
                delta = filt_avg - orig_avg
                kept = len(filtered) / len(sigs_with_pnl) * 100
                wr = sum(1 for p in filtered if p > 0) / len(filtered)
                st = statistics.stdev(filtered) if len(filtered) > 1 else 0
                sh = (filt_avg / st) if st > 0 else 0
                flag = "✅" if delta > 3 and kept > 40 else "⚠️" if delta > 0 else "❌"
                print(f"{flag} {feat} > {thresh:+.1f}{'':>14} n={len(filtered):>3} ({kept:>5.0f}%)  "
                      f"avg={filt_avg:+.2f}% (Δ{delta:+.2f})  WR={wr*100:.1f}%  sh={sh:+.3f}")
    
    # ── Dynamic exit optimization ──
    print(f"\n[4] Testing dynamic exits (trailing stop)...", flush=True)
    tsl_results = test_dynamic_exits(sigs_with_pnl, k5)
    for r in tsl_results[:10]:
        flag = "✅" if r["delta"] > 2 else "⚠️" if r["delta"] > 0 else "❌"
        print(f"{flag} {r['name']:<35} avg={r['avg']:+.2f}% (Δ{r['delta']:+.2f})")
    
    # ── Summary ──
    print(f"\n{'='*100}")
    print(f"TOP OPTIONS FEATURES by score (delta * kept/100)")
    print(f"{'='*100}")
    
    scored = [(r, r.get("delta", 0) * r.get("kept", 50) / 100) for r in all_results if r.get("delta", 0) > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    
    for r, score in scored[:15]:
        print(f"{'✅' if score > 5 else '⭐'} {r['name']:<35} score={score:+.2f}  "
              f"n={r['n_filt']:>3} avg={r['filt_avg']:+.2f}% "
              f"WR={r['wr']*100:.1f}% sh={r['sh']:+.3f} cl={r['mc']}")
    
    if not scored:
        print("  No options feature filter improved the baseline.")
    
    print(f"\nDone ({round(time.time()-t0,1)}s)", flush=True)


if __name__ == "__main__":
    main()

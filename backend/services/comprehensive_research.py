"""Comprehensive feature research: test ALL mathematical models and filters.

Features tested (36 categories):
  PRICE-BASED (from klines):
    1. RSI extremes (oversold/overbought)
    2. Bollinger Band position (%B)
    3. Volatility regime (high vs low RV)
    4. Momentum decay (acceleration)
    5. Volume spike detection
    6. ATR expansion/contraction
    7. EMA slope changes
    8. Price vs VWAP deviation
  
  MICROSTRUCTURE (from Bybit API):
    9. Funding Rate level
    10. Funding Rate change (acceleration)
    11. OI trend (1h, 4h, 12h, 24h)
    12. OI acceleration (2nd derivative)
    13. OI / Volume ratio
    14. L/S ratio extremes
    15. L/S ratio change
  
  COMBINED:
    16. Price-OI divergence
    17. FR + momentum alignment
    18. Vol regime + OI trend
    19. RSI + FR contrarian
    20. BB squeeze + OI buildup
  
  POSITION SIZING:
    21. Volatility targeting
    22. Kelly fraction
    23. Dynamic SL based on ATR
  
  EXIT OPTIMIZATION:
    24. Trailing stop (various triggers)
    25. Dynamic TP/SL based on vol regime
    26. Time-stop optimization

Run:
    cd backend && PYTHONPATH=. python3 services/comprehensive_research.py
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
from services.indicators import ema, rsi, atr, bollinger, realized_vol
from services.momentum_mtf import analyze_tf, consensus
from services.regime import detect_regime

# ──────────── Data loading ────────────

FR_DATA = {}
OI_DATA = {}
LS_DATA = {}

def load_all_data():
    global FR_DATA, OI_DATA, LS_DATA
    data_dir = find_data_dir(None)
    
    for name, var_name in [("eth_funding", "FR"), ("eth_oi", "OI"), ("eth_long_short", "LS")]:
        path = data_dir / f"{name}.json"
        if path.exists():
            data = json.loads(path.read_text())
            if name == "eth_funding":
                FR_DATA = {d["ts_ms"]: d for d in data}
            elif name == "oi":
                OI_DATA = {d["ts_ms"]: d for d in data}
            else:
                LS_DATA = {d["ts_ms"]: d for d in data}
    
    print(f"  Funding: {len(FR_DATA)} records", flush=True)
    print(f"  OI: {len(OI_DATA)} records", flush=True)
    print(f"  L/S: {len(LS_DATA)} records", flush=True)


# ──────────── Feature computation ────────────

def compute_price_features(k5, k15, k1h, idx):
    """Compute all price-based features at a given bar index."""
    features = {}
    
    if idx < 20:
        return features
    
    closes_5m = [c["close"] for c in k5[max(0,idx-100):idx+1]]
    closes_1h = [c["close"] for c in k1h[max(0,idx-200):idx+1]]
    
    # 1. RSI (14-period on 5m)
    if len(closes_5m) >= 15:
        features["rsi_5m"] = rsi(closes_5m[-15:], 14)
    
    # 2. Bollinger Band %B (20, 2.0)
    if len(closes_5m) >= 20:
        lower, mid, upper = bollinger(closes_5m, 20, 2.0)
        if lower is not None and upper != lower:
            current = closes_5m[-1]
            features["bb_pct"] = (current - lower) / (upper - lower)
    
    # 3. Realized vol (24h lookback on 1h)
    if len(closes_1h) >= 25:
        features["rv_24h"] = realized_vol(closes_1h, lookback=24)
    
    # 4. ATR (14 on 5m)
    if len(k5) > idx - 14:
        candles_5m = k5[max(0,idx-15):idx+1]
        if len(candles_5m) >= 15:
            features["atr_5m"] = atr(candles_5m, 14)
    
    # 5. Momentum (5m return over last N bars)
    for n in [12, 48, 288]:  # 1h, 4h, 24h in 5m bars
        if idx >= n:
            ret = (k5[idx]["close"] - k5[idx-n]["close"]) / k5[idx-n]["close"] * 100
            features[f"mom_{n}"] = ret
    
    # 6. Volume z-score
    if idx >= 20:
        volumes = [c["volume"] for c in k5[max(0,idx-20):idx+1]]
        if len(volumes) >= 20:
            mean_v = statistics.mean(volumes[:-1])
            std_v = statistics.stdev(volumes[:-1]) if len(volumes) > 2 else 1
            if std_v > 0:
                features["vol_z"] = (volumes[-1] - mean_v) / std_v
    
    # 7. EMA slope (rate of change)
    if idx >= 50:
        closes_long = [c["close"] for c in k5[max(0,idx-50):idx+1]]
        e20 = ema(closes_long, 20)
        e50 = ema(closes_long, 50)
        if e20 and e50 and e50 > 0:
            features["ema_spread"] = (e20 - e50) / e50 * 100
    
    return features


def compute_micro_features(ts_ms):
    """Compute microstructure features at a given timestamp."""
    features = {}
    
    # Funding Rate
    fr_data = None
    best_dist = float("inf")
    for k, v in FR_DATA.items():
        d = abs(k - ts_ms)
        if d < best_dist:
            fr_data = v
            best_dist = d
    
    if fr_data:
        features["fr"] = fr_data["funding_rate"]
    
    # OI trend
    for hours in [1, 4, 12, 24]:
        curr = None
        prev = None
        best_curr = float("inf")
        best_prev = float("inf")
        for k, v in OI_DATA.items():
            d_curr = abs(k - ts_ms)
            d_prev = abs(k - (ts_ms - hours * 3_600_000))
            if d_curr < best_curr:
                curr = v
                best_curr = d_curr
            if d_prev < best_prev:
                prev = v
                best_prev = d_prev
        
        if curr and prev and prev["open_interest"] > 0:
            oi_change = (curr["open_interest"] - prev["open_interest"]) / prev["open_interest"]
            features[f"oi_{hours}h"] = oi_change
    
    # L/S ratio
    ls_data = None
    best_dist = float("inf")
    for k, v in LS_DATA.items():
        d = abs(k - ts_ms)
        if d < best_dist:
            ls_data = v
            best_dist = d
    
    if ls_data:
        features["ls_ratio"] = ls_data["long_short_ratio"]
    
    return features


# ──────────── Signal generation with features ────────────

def generate_signals_with_features(k5, k15, k1h, cutoff_ms):
    """Generate Config B signals with ALL features attached."""
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
        
        # Compute ALL features
        price_features = compute_price_features(k5, k15, k1h, i)
        micro_features = compute_micro_features(ts_end)
        
        sig = {
            "idx_5m": i, "ts_ms": ts_end, "close": c5["close"],
            "side": side, "position": "short_premium",
            "ret_7d": round(ret_7d, 2),
            **price_features,
            **micro_features,
        }
        if ts_end >= cutoff_ms:
            out.append(sig)
        last_idx = i
    
    return out


# ──────────── Filter testing ────────────

def test_single_filter(sigs_with_pnl, feature_name, condition_fn, name=""):
    """Test a single boolean filter condition."""
    filtered_pnls = []
    for sig, pnl in sigs_with_pnl:
        val = sig.get(feature_name)
        if val is None:
            continue
        if condition_fn(val):
            filtered_pnls.append(pnl)
    
    if not filtered_pnls or len(filtered_pnls) < 5:
        return None
    
    all_pnls = [p for _, p in sigs_with_pnl]
    orig_avg = statistics.mean(all_pnls)
    filt_avg = statistics.mean(filtered_pnls)
    delta = filt_avg - orig_avg
    kept = len(filtered_pnls) / len(all_pnls) * 100
    wr = sum(1 for p in filtered_pnls if p > 0) / len(filtered_pnls)
    st = statistics.stdev(filtered_pnls) if len(filtered_pnls) > 1 else 0
    sh = (filt_avg / st) if st > 0 else 0
    
    mc = cl = 0
    for p in filtered_pnls:
        if p < 0: cl += 1; mc = max(mc, cl)
        else: cl = 0
    
    return {
        "name": name or f"{feature_name} filter",
        "n_orig": len(all_pnls), "n_filt": len(filtered_pnls), "kept": kept,
        "orig_avg": orig_avg, "filt_avg": filt_avg, "delta": delta,
        "wr": wr, "sh": sh, "mc": mc,
    }


def fmt_result(r):
    if r is None:
        return ""
    flag = "✅" if r["delta"] > 3 and r["kept"] > 40 else "⭐" if r["delta"] > 5 else "⚠️" if r["delta"] > 0 else "❌"
    return (f"{flag} {r['name']:<35} n={r['n_filt']:>3} ({r['kept']:>5.0f}%)  "
            f"avg={r['filt_avg']:+.2f}% (Δ{r['delta']:+.2f})  "
            f"WR={r['wr']*100:.1f}%  sh={r['sh']:+.3f}  cl={r['mc']}")


# ──────────── Main ────────────

def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Comprehensive Feature Research ===", flush=True)
    
    load_all_data()
    
    k5, k15, k1h = load_local(data_dir)
    
    # Determine cutoff (use overlap window for microstructure)
    cutoff_ms = 0  # all data for max signals
    
    print(f"\n[1] Generating signals with features...", flush=True)
    all_sigs = generate_signals_with_features(k5, k15, k1h, cutoff_ms)
    print(f"  Total signals: {len(all_sigs)}", flush=True)
    
    # Count feature availability
    feature_counts = {}
    for sig in all_sigs:
        for key in sig:
            if key not in ["idx_5m", "ts_ms", "close", "side", "position", "ret_7d"]:
                feature_counts[key] = feature_counts.get(key, 0) + 1
    
    print(f"  Features available:", flush=True)
    for feat, count in sorted(feature_counts.items(), key=lambda x: -x[1]):
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
    
    # ── Test ALL features ──
    print(f"\n[3] Testing all filters...", flush=True)
    all_results = []
    
    # RSI filters
    for rsi_feat in ["rsi_5m"]:
        for thresh in [20, 25, 30, 70, 75, 80]:
            if thresh < 50:
                cond = lambda v, t=thresh: v is not None and v < t
                name = f"{rsi_feat} < {thresh}"
            else:
                cond = lambda v, t=thresh: v is not None and v > t
                name = f"{rsi_feat} > {thresh}"
            r = test_single_filter(sigs_with_pnl, rsi_feat, cond, name)
            if r: all_results.append(r)
    
    # Bollinger %B filters
    for bb_feat in ["bb_pct"]:
        for thresh in [0.2, 0.3, 0.7, 0.8]:
            if thresh < 0.5:
                cond = lambda v, t=thresh: v is not None and v < t
                name = f"{bb_feat} < {thresh}"
            else:
                cond = lambda v, t=thresh: v is not None and v > t
                name = f"{bb_feat} > {thresh}"
            r = test_single_filter(sigs_with_pnl, bb_feat, cond, name)
            if r: all_results.append(r)
    
    # RV filters
    for rv_feat in ["rv_24h"]:
        for thresh in [0.3, 0.5, 0.7, 1.0]:
            cond = lambda v, t=thresh: v is not None and v > t
            name = f"{rv_feat} > {thresh}"
            r = test_single_filter(sigs_with_pnl, rv_feat, cond, name)
            if r: all_results.append(r)
    
    # Momentum filters
    for mom_feat in ["mom_12", "mom_48", "mom_288"]:
        for thresh in [-3, -2, -1, 0, 1, 2, 3]:
            cond = lambda v, t=thresh: v is not None and v > t
            name = f"{mom_feat} > {thresh}%"
            r = test_single_filter(sigs_with_pnl, mom_feat, cond, name)
            if r: all_results.append(r)
    
    # Volume z-score
    for vol_feat in ["vol_z"]:
        for thresh in [1.0, 1.5, 2.0, -1.0, -1.5, -2.0]:
            cond = lambda v, t=thresh: v is not None and v > t
            name = f"{vol_feat} > {thresh}"
            r = test_single_filter(sigs_with_pnl, vol_feat, cond, name)
            if r: all_results.append(r)
    
    # ATR filters
    for atr_feat in ["atr_5m"]:
        for thresh in [10, 20, 30, 50]:
            cond = lambda v, t=thresh: v is not None and v > t
            name = f"{atr_feat} > {thresh}"
            r = test_single_filter(sigs_with_pnl, atr_feat, cond, name)
            if r: all_results.append(r)
    
    # EMA spread
    for ema_feat in ["ema_spread"]:
        for thresh in [-2, -1, 0, 1, 2]:
            cond = lambda v, t=thresh: v is not None and v > t
            name = f"{ema_feat} > {thresh}%"
            r = test_single_filter(sigs_with_pnl, ema_feat, cond, name)
            if r: all_results.append(r)
    
    # Funding Rate
    for fr_feat in ["fr"]:
        for thresh in [-0.001, -0.0005, 0, 0.0005, 0.001]:
            cond = lambda v, t=thresh: v is not None and v > t
            name = f"{fr_feat} > {thresh:+.5f}"
            r = test_single_filter(sigs_with_pnl, fr_feat, cond, name)
            if r: all_results.append(r)
    
    # OI trend (all horizons)
    for oi_feat in ["oi_1h", "oi_4h", "oi_12h", "oi_24h"]:
        for thresh in [-0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03]:
            cond = lambda v, t=thresh: v is not None and v > t
            name = f"{oi_feat} > {thresh:+.2%}"
            r = test_single_filter(sigs_with_pnl, oi_feat, cond, name)
            if r: all_results.append(r)
    
    # L/S ratio
    for ls_feat in ["ls_ratio"]:
        for thresh in [0.90, 0.95, 1.0, 1.05, 1.10, 1.15]:
            cond = lambda v, t=thresh: v is not None and v > t
            name = f"{ls_feat} > {thresh:.2f}"
            r = test_single_filter(sigs_with_pnl, ls_feat, cond, name)
            if r: all_results.append(r)
    
    # Combined filters
    print(f"\n[4] Testing combined filters...", flush=True)
    
    # FR + OI
    for fr_t in [-0.0005, 0, 0.0005]:
        for oi_h in ["oi_4h", "oi_12h", "oi_24h"]:
            for oi_t in [-0.02, -0.01, 0, 0.01]:
                def cond(sig, fr=fr_t, h=oi_h, t=oi_t):
                    fr_val = sig.get("fr")
                    oi_val = sig.get(h)
                    fr_ok = fr_val is None or fr_val > fr
                    oi_ok = oi_val is None or oi_val > t
                    return fr_ok and oi_ok
                
                filtered_pnls = [pnl for sig, pnl in sigs_with_pnl if cond(sig)]
                if not filtered_pnls or len(filtered_pnls) < 5:
                    continue
                
                all_pnls = [p for _, p in sigs_with_pnl]
                filt_avg = statistics.mean(filtered_pnls)
                orig_avg = statistics.mean(all_pnls)
                delta = filt_avg - orig_avg
                kept = len(filtered_pnls) / len(all_pnls) * 100
                wr = sum(1 for p in filtered_pnls if p > 0) / len(filtered_pnls)
                st = statistics.stdev(filtered_pnls) if len(filtered_pnls) > 1 else 0
                sh = (filt_avg / st) if st > 0 else 0
                mc = cl = 0
                for p in filtered_pnls:
                    if p < 0: cl += 1; mc = max(mc, cl)
                    else: cl = 0
                
                name = f"FR>{fr_t:+.5f} + {oi_h}>{oi_t:+.2%}"
                all_results.append({
                    "name": name, "n_orig": len(all_pnls), "n_filt": len(filtered_pnls), "kept": kept,
                    "orig_avg": orig_avg, "filt_avg": filt_avg, "delta": delta,
                    "wr": wr, "sh": sh, "mc": mc,
                })
    
    # ── Summary ──
    print(f"\n{'='*100}")
    print(f"TOP 20 FILTERS by score (delta * kept/100)")
    print(f"{'='*100}")
    
    scored = [(r, r["delta"] * r["kept"] / 100) for r in all_results if r["delta"] > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    
    for r, score in scored[:20]:
        print(fmt_result(r))
    
    if not scored:
        print("  No filter improved the baseline.")
    
    print(f"\nDone ({round(time.time()-t0,1)}s)", flush=True)


if __name__ == "__main__":
    main()

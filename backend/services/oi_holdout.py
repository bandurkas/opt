"""Holdout validation for microstructure features.

Overlap window: ~66 days (Mar 27 → Jun 1, 2026)
Split: first 44d = train, last 22d = test (time-ordered, no leakage)

Process:
  1. Find best OI filter on train set
  2. Test it on test set
  3. Report if it holds or is overfit

Run:
    cd backend && PYTHONPATH=. python3 services/oi_holdout.py
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
from services.retest_asymmetric_365d import apply_cb, sim_stats, BARS_7D, CONSISTENT_CD, PUT_GEN, CALL_GEN

FR_DATA = {}
OI_DATA = {}
LS_DATA = {}

def load_micro_data():
    global FR_DATA, OI_DATA, LS_DATA
    data_dir = find_data_dir(None)
    
    for name, var in [("eth_funding", "FR"), ("eth_oi", "OI"), ("eth_long_short", "LS")]:
        path = data_dir / f"{name}.json"
        if path.exists():
            data = json.loads(path.read_text())
            if name == "eth_funding":
                FR_DATA = {d["ts_ms"]: d for d in data}
            elif name == "eth_oi":
                OI_DATA = {d["ts_ms"]: d for d in data}
            else:
                LS_DATA = {d["ts_ms"]: d for d in data}
    
    # Show coverage
    if OI_DATA:
        oi_ts = sorted(OI_DATA.keys())
        first = datetime.fromtimestamp(oi_ts[0]/1000, tz=timezone.utc).strftime("%Y-%m-%d")
        last = datetime.fromtimestamp(oi_ts[-1]/1000, tz=timezone.utc).strftime("%Y-%m-%d")
        print(f"  OI data: {len(OI_DATA)} records | {first} → {last}", flush=True)


def nearest_oi(ts_ms, window_ms=15*60*1000):
    """Find nearest OI data point."""
    if ts_ms in OI_DATA:
        return OI_DATA[ts_ms]
    best = None
    best_dist = float("inf")
    for k, v in OI_DATA.items():
        d = abs(k - ts_ms)
        if d < window_ms and d < best_dist:
            best = v
            best_dist = d
    return best


def get_oi_change(ts_ms, hours_back):
    """OI change over N hours before ts_ms."""
    curr = nearest_oi(ts_ms)
    prev = nearest_oi(ts_ms - hours_back * 3_600_000)
    if curr is None or prev is None:
        return None
    if prev["open_interest"] == 0:
        return 0
    return (curr["open_interest"] - prev["open_interest"]) / prev["open_interest"]


def get_fr(ts_ms):
    """Funding rate at ts_ms."""
    if ts_ms in FR_DATA:
        return FR_DATA[ts_ms]["funding_rate"]
    best = None
    best_dist = float("inf")
    for k, v in FR_DATA.items():
        d = abs(k - ts_ms)
        if d < best_dist:
            best = v
            best_dist = d
    return best["funding_rate"] if best else None


def gen_signals_with_oi(k5, k15, k1h, cutoff_ms):
    """Generate Config B signals with OI data attached."""
    from services.indicators import ema, realized_vol
    from services.momentum_mtf import analyze_tf, consensus
    from services.regime import detect_regime
    
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
        
        # Attach microstructure data
        oi_4h = get_oi_change(ts_end, 4)
        oi_12h = get_oi_change(ts_end, 12)
        oi_24h = get_oi_change(ts_end, 24)
        fr = get_fr(ts_end)
        
        sig = {
            "idx_5m": i, "ts_ms": ts_end, "close": c5["close"],
            "side": side, "position": "short_premium",
            "ret_7d": round(ret_7d, 2),
            "oi_4h": oi_4h, "oi_12h": oi_12h, "oi_24h": oi_24h, "fr": fr,
        }
        if ts_end >= cutoff_ms:
            out.append(sig)
        last_idx = i
    
    return out


def test_oi_filter(sigs_with_pnl, oi_field, threshold):
    """Keep signals where OI change > threshold."""
    filtered = []
    for sig, pnl in sigs_with_pnl:
        oi_val = sig.get(oi_field)
        if oi_val is None:
            continue  # skip signals without OI data
        if oi_val > threshold:
            filtered.append(pnl)
    return filtered


def fmt_stats(pnls, name=""):
    if not pnls:
        return f"  {name:<25} NO SIGNALS"
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    avg = statistics.mean(pnls)
    st = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sh = (avg / st) if st > 0 else 0
    mc = cl = 0
    for p in pnls:
        if p < 0: cl += 1; mc = max(mc, cl)
        else: cl = 0
    return f"  {name:<25} n={len(pnls):>3} WR={wr*100:>5.1f}% avg={avg:+.2f}% sh={sh:+.3f} cl={mc}"


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== OI Feature Holdout Validation ===", flush=True)
    
    load_micro_data()
    
    k5, k15, k1h = load_local(data_dir)
    
    # Determine train/test split based on OI data coverage
    oi_ts = sorted(OI_DATA.keys())
    if not oi_ts:
        print("ERROR: No OI data!", flush=True)
        return
    
    # Split: first 66% = train, last 34% = test (time-ordered)
    split_idx = int(len(oi_ts) * 0.66)
    train_cutoff = oi_ts[split_idx]
    
    train_start = datetime.fromtimestamp(oi_ts[0]/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    train_end = datetime.fromtimestamp(train_cutoff/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    test_start = datetime.fromtimestamp(train_cutoff/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    test_end = datetime.fromtimestamp(oi_ts[-1]/1000, tz=timezone.utc).strftime("%Y-%m-%d")
    
    print(f"\nTrain: {train_start} → {train_end} (~{split_idx} OI points)", flush=True)
    print(f"Test:  {test_start} → {test_end} (~{len(oi_ts)-split_idx} OI points)", flush=True)
    
    # Generate signals for both periods
    print("\n[1] Generating signals...", flush=True)
    all_sigs = gen_signals_with_oi(k5, k15, k1h, 0)  # all signals
    
    train_sigs = [s for s in all_sigs if s["ts_ms"] < train_cutoff]
    test_sigs = [s for s in all_sigs if s["ts_ms"] >= train_cutoff]
    print(f"  Train signals: {len(train_sigs)}", flush=True)
    print(f"  Test signals: {len(test_sigs)}", flush=True)
    
    # Simulate
    print("\n[2] Simulating...", flush=True)
    
    def simulate(sig_list):
        ps = [s for s in sig_list if s["side"] == "P"]
        cs = [s for s in sig_list if s["side"] == "C"]
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
        
        return [(s, sig_pnl.get(s["ts_ms"])) for s in sig_list if s["ts_ms"] in sig_pnl]
    
    train_with_pnl = simulate(train_sigs)
    test_with_pnl = simulate(test_sigs)
    
    train_pnls = [p for _, p in train_with_pnl]
    test_pnls = [p for _, p in test_with_pnl]
    
    print(f"  Train: {fmt_stats(train_pnls, 'baseline')}", flush=True)
    print(f"  Test:  {fmt_stats(test_pnls, 'baseline')}", flush=True)
    
    # ── Find best OI filter on train ──
    print(f"\n[3] Finding best OI filter on TRAIN set...", flush=True)
    
    best_score = -999
    best_config = None
    
    for oi_field in ["oi_4h", "oi_12h", "oi_24h"]:
        for threshold in [-0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03]:
            filtered = test_oi_filter(train_with_pnl, oi_field, threshold)
            if not filtered or len(filtered) < 5:
                continue
            avg = statistics.mean(filtered)
            n = len(filtered)
            kept = n / len(train_pnls) * 100
            # Score: reward avg * kept, penalize low sample
            score = avg * min(1.0, kept / 50)
            if score > best_score:
                best_score = score
                best_config = (oi_field, threshold, filtered, avg, n, kept)
    
    if best_config:
        oi_field, threshold, filtered_pnls, avg, n, kept = best_config
        print(f"  Best on train: {oi_field} > {threshold:+.2%}", flush=True)
        print(f"    n={n} ({kept:.0f}% kept) avg={avg:+.2f}%", flush=True)
        
        # ── Test on holdout ──
        print(f"\n[4] Testing on HOLDOUT set...", flush=True)
        test_filtered = test_oi_filter(test_with_pnl, oi_field, threshold)
        
        print(f"  Train: {fmt_stats(filtered_pnls, f'{oi_field}>{threshold:+.2%}')}", flush=True)
        print(f"  Test:  {fmt_stats(test_filtered, f'{oi_field}>{threshold:+.2%}')}", flush=True)
        
        # Compare
        train_avg = statistics.mean(filtered_pnls)
        test_avg = statistics.mean(test_filtered) if test_filtered else 0
        gap = test_avg - train_avg
        
        print(f"\n{'='*60}")
        print(f"HOLDOUT VERDICT:")
        if test_filtered and test_avg > 5 and gap > -10:
            print(f"  ✅ PASS: Test avg={test_avg:+.2f}% (train={train_avg:+.2f}%, gap={gap:+.2f}%)")
            print(f"     Filter generalizes — not overfit!")
        elif test_filtered and test_avg > 0:
            print(f"  ⚠️ PARTIAL: Test avg={test_avg:+.2f}% but gap={gap:+.2f}%")
            print(f"     Some degradation — possible mild overfit")
        else:
            print(f"  ❌ FAIL: Test avg={test_avg:+.2f}% (train={train_avg:+.2f}%, gap={gap:+.2f}%)")
            print(f"     Filter is overfit to training data")
    else:
        print(f"  No filter found that improves baseline on train set", flush=True)
    
    print(f"\nDone ({round(time.time()-t0,1)}s)", flush=True)


if __name__ == "__main__":
    main()

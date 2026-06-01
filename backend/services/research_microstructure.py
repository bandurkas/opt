"""Research: Test microstructure features against Config B baseline.

Features tested as POST-SIGNAL filters:
  1. Funding Rate (FR) — positive = bullish consensus
     Variants: FR>0, FR>0.0001, FR<0, FR avg(24h)>0
  2. OI Trend — rising OI = strong trend
     Variants: OI_1h rising, OI_4h rising, OI_24h rising
  3. Long/Short Ratio — >1 = longs dominate
     Variants: LS>1, LS>1.05, LS<1, LS extreme
  4. Combined — FR + OI, LS + OI, FR + LS
  5. Price-OI Divergence — price up + OI down = weak trend

Data coverage:
  Funding: 66 days (limited)
  OI: 140 days
  L/S: 210 days

Run:
    cd backend && PYTHONPATH=. python3 services/research_microstructure.py
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

# Re-import needed constants
from services.retest_asymmetric_365d import apply_cb, sim_stats, BARS_7D, CONSISTENT_CD, PUT_GEN, CALL_GEN

# Feature configuration
FR_DATA = []
OI_DATA = []
LS_DATA = []

def load_data():
    global FR_DATA, OI_DATA, LS_DATA
    data_dir = find_data_dir(None)
    
    fr_path = data_dir / "eth_funding.json"
    oi_path = data_dir / "eth_oi.json"
    ls_path = data_dir / "eth_long_short.json"
    
    FR_DATA = json.loads(fr_path.read_text()) if fr_path.exists() else []
    OI_DATA = json.loads(oi_path.read_text()) if oi_path.exists() else []
    LS_DATA = json.loads(ls_path.read_text()) if ls_path.exists() else []
    
    # Index by ts_ms
    FR_DATA = {d["ts_ms"]: d for d in FR_DATA}
    OI_DATA = {d["ts_ms"]: d for d in OI_DATA}
    LS_DATA = {d["ts_ms"]: d for d in LS_DATA}
    
    print(f"  Funding: {len(FR_DATA)} records", flush=True)
    print(f"  OI: {len(OI_DATA)} records", flush=True)
    print(f"  L/S: {len(LS_DATA)} records", flush=True)


def nearest(data_dict, ts_ms, window_ms=15*60*1000):
    """Find nearest data point within ±window_ms."""
    # Exact match
    if ts_ms in data_dict:
        return data_dict[ts_ms]
    # Within window
    best = None
    best_dist = float("inf")
    for k, v in data_dict.items():
        d = abs(k - ts_ms)
        if d < window_ms and d < best_dist:
            best = v
            best_dist = d
    return best


def get_fr(ts_ms, window_hours=24):
    """Get funding rate at ts_ms, with optional averaging."""
    # Nearest single point
    fr = nearest(FR_DATA, ts_ms)
    if fr is None:
        return None
    return fr["funding_rate"]


def get_oi_change(ts_ms, hours_back):
    """Get OI change over N hours before ts_ms."""
    curr = nearest(OI_DATA, ts_ms)
    prev = nearest(OI_DATA, ts_ms - hours_back * 3_600_000)
    if curr is None or prev is None:
        return None
    if prev["open_interest"] == 0:
        return 0
    return (curr["open_interest"] - prev["open_interest"]) / prev["open_interest"]


def get_ls_ratio(ts_ms):
    """Get long/short ratio at ts_ms."""
    ls = nearest(LS_DATA, ts_ms)
    if ls is None:
        return None
    return ls["long_short_ratio"]


def test_filter(name, keep_fn, sigs_with_pnl):
    """Test a filter function on signals."""
    filtered = [pnl for sig_ts, pnl in sigs_with_pnl if keep_fn(sig_ts)]
    orig_pnls = [pnl for _, pnl in sigs_with_pnl]
    
    if not filtered:
        return None
    
    n_orig = len(orig_pnls)
    n_filt = len(filtered)
    kept = n_filt / n_orig * 100
    orig_avg = statistics.mean(orig_pnls)
    filt_avg = statistics.mean(filtered)
    delta = filt_avg - orig_avg
    filt_wr = sum(1 for p in filtered if p > 0) / n_filt
    filt_st = statistics.stdev(filtered) if n_filt > 1 else 0
    filt_sh = (filt_avg / filt_st) if filt_st > 0 else 0
    
    mc = cl = 0
    for p in filtered:
        if p < 0: cl += 1; mc = max(mc, cl)
        else: cl = 0
    
    return {
        "name": name, "n_orig": n_orig, "n_filt": n_filt, "kept": kept,
        "orig_avg": orig_avg, "filt_avg": filt_avg, "delta": delta,
        "filt_wr": filt_wr, "filt_sh": filt_sh, "mc": mc,
    }


def fmt_result(r, baseline_avg):
    if r is None:
        return "  (no data)"
    flag = "✅" if r["delta"] > 2 and r["kept"] > 50 else "⚠️" if r["delta"] > 0 else "❌"
    return (f"{flag} {r['name']:<35} n={r['n_filt']:>3} ({r['kept']:>5.0f}%)  "
            f"avg={r['filt_avg']:+.2f}% (Δ{r['delta']:+.2f})  "
            f"WR={r['filt_wr']*100:.1f}%  sh={r['filt_sh']:+.3f}  cl={r['mc']}")


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Microstructure Feature Research ===", flush=True)
    
    load_data()

    k5, k15, k1h = load_local(data_dir)
    
    # Use cutoff based on available microstructure data
    # Funding has only 200 records, so use the overlap window
    all_ts_ms = list(FR_DATA.keys()) if FR_DATA else []
    if all_ts_ms:
        min_ts = min(all_ts_ms)
        max_ts = max(all_ts_ms)
        cutoff_ms = min_ts  # only test where we have microstructure data
        print(f"  Microstructure data window: {datetime.fromtimestamp(min_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d')} → {datetime.fromtimestamp(max_ts/1000, tz=timezone.utc).strftime('%Y-%m-%d')}", flush=True)
    else:
        cutoff_ms = 0  # all data

    # Generate Config B signals
    print("\n[1] Generating Config B signals...", flush=True)
    sigs = generate_signals(k5, k15, k1h, cutoff_ms, PUT_RET_MAX, CALL_RET_MIN)
    
    if not sigs:
        print("  No signals generated!", flush=True)
        return
    
    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]
    print(f"  Signals: {len(sigs)} P={len(ps)} C={len(cs)}", flush=True)
    
    # Simulate
    print("\n[2] Simulating...", flush=True)
    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ps else []
    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if cs else []
    
    # Build signal-to-pnl mapping
    sig_pnl_map = {}
    for s in psim + csim:
        pnl = s["option"].get("pnl_pct")
        if pnl is not None:
            sig_pnl_map[(s["ts_ms"], s["side"])] = pnl
    
    sigs_with_pnl = list(sig_pnl_map.items())
    baseline_avg = statistics.mean([p for _, p in sigs_with_pnl])
    baseline_n = len(sigs_with_pnl)
    baseline_wr = sum(1 for _, p in sigs_with_pnl if p > 0) / baseline_n
    
    print(f"\n{'='*90}")
    print(f"BASELINE: n={baseline_n} WR={baseline_wr*100:.1f}% avg={baseline_avg:+.2f}%")
    print(f"{'='*90}")
    
    results = []
    
    # ── Feature 1: Funding Rate ──
    print(f"\nFEATURE 1: Funding Rate Filter", flush=True)
    
    for fr_thresh in [0, 0.0001, -0.0001]:
        def keep(sig_ts, thresh=fr_thresh):
            ts, side = sig_ts
            fr = get_fr(ts)
            if fr is None: return True  # no data → keep
            return fr > thresh
        
        r = test_filter(f"FR > {fr_thresh:+.5f}", keep, sigs_with_pnl)
        if r: results.append(r)
        print(fmt_result(r, baseline_avg))
    
    # ── Feature 2: OI Trend ──
    print(f"\nFEATURE 2: OI Trend Filter", flush=True)
    
    for oi_h in [1, 4, 12, 24]:
        for oi_min in [-0.02, -0.01, 0, 0.01, 0.02]:
            def keep(sig_ts, h=oi_h, mn=oi_min):
                ts, side = sig_ts
                change = get_oi_change(ts, h)
                if change is None: return True
                return change > mn
            
            r = test_filter(f"OI_{oi_h}h Δ > {oi_min:+.2%}", keep, sigs_with_pnl)
            if r: results.append(r)
            print(fmt_result(r, baseline_avg))
    
    # ── Feature 3: L/S Ratio ──
    print(f"\nFEATURE 3: Long/Short Ratio Filter", flush=True)
    
    for ls_thresh in [0.95, 1.0, 1.05, 1.10]:
        def keep(sig_ts, thresh=ls_thresh):
            ts, side = sig_ts
            ls = get_ls_ratio(ts)
            if ls is None: return True
            return ls > thresh
        
        r = test_filter(f"L/S > {ls_thresh:.2f}", keep, sigs_with_pnl)
        if r: results.append(r)
        print(fmt_result(r, baseline_avg))
    
    # ── Feature 4: Combined ──
    print(f"\nFEATURE 4: Combined Filters", flush=True)
    
    # FR > 0 AND OI rising
    for fr_t in [0, 0.0001]:
        for oi_h in [4, 12]:
            for oi_min in [-0.01, 0, 0.01]:
                def keep(sig_ts, fr=fr_t, h=oi_h, mn=oi_min):
                    ts, side = sig_ts
                    fr_val = get_fr(ts)
                    oi_change = get_oi_change(ts, h)
                    fr_ok = fr_val is None or fr_val > fr
                    oi_ok = oi_change is None or oi_change > mn
                    return fr_ok and oi_ok
                
                r = test_filter(f"FR>{fr_t:+.5f} + OI_{oi_h}h>{oi_min:+.2%}", keep, sigs_with_pnl)
                if r: results.append(r)
                print(fmt_result(r, baseline_avg))
    
    # ── Feature 5: L/S + OI ──
    print(f"\nFEATURE 5: L/S + OI Combined", flush=True)
    
    for ls_t in [1.0, 1.05]:
        for oi_h in [4, 12]:
            for oi_min in [0, 0.01]:
                def keep(sig_ts, ls=ls_t, h=oi_h, mn=oi_min):
                    ts, side = sig_ts
                    ls_val = get_ls_ratio(ts)
                    oi_change = get_oi_change(ts, h)
                    ls_ok = ls_val is None or ls_val > ls
                    oi_ok = oi_change is None or oi_change > mn
                    return ls_ok and oi_ok
                
                r = test_filter(f"L/S>{ls_t:.2f} + OI_{oi_h}h>{oi_min:+.2%}", keep, sigs_with_pnl)
                if r: results.append(r)
                print(fmt_result(r, baseline_avg))
    
    # ── Summary ──
    print(f"\n{'='*90}")
    print(f"TOP FILTERS by delta (avg improvement)")
    print(f"{'='*90}")
    
    # Sort by delta * kept/100 (reward improvement, penalize signal loss)
    scored = [(r, r["delta"] * r["kept"] / 100) for r in results if r["delta"] > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    
    for r, score in scored[:15]:
        flag = "✅" if r["delta"] > 5 else "⭐"
        print(f"{flag} {r['name']:<35} score={score:+.2f}  "
              f"n={r['n_filt']:>3} avg={r['filt_avg']:+.2f}% "
              f"WR={r['filt_wr']*100:.1f}% sh={r['filt_sh']:+.3f} cl={r['mc']}")
    
    if not scored:
        print("  No filter improved the baseline.")
    
    print(f"\nDone ({round(time.time()-t0,1)}s)", flush=True)


if __name__ == "__main__":
    main()

"""Research: Can microstructure features EXPAND the signal universe beyond Config B?

Current Config B dead zone: -2.5% ≤ ret_7d ≤ +1.0% → no trade

Hypothesis: In the dead zone, there are still profitable opportunities when:
  - Funding rate shows consensus is wrong (contrarian signal)
  - OI is rising (new positions being built)
  - L/S ratio shows extreme positioning (squeeze potential)

Test:
  1. Generate Config B signals (baseline)
  2. Generate EXPANDED signals: dead zone + microstructure says "trade"
  3. Are expanded signals profitable?
  4. If yes → new strategy: Config B + microstructure expansion

Run:
    cd backend && PYTHONPATH=. python3 services/research_expansion.py
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
from services.retest_asymmetric_365d import (
    generate_signals as gen_baseline, PUT_EXIT, CALL_EXIT,
    BARS_7D, CONSISTENT_CD, PUT_GEN, CALL_GEN,
    apply_cb, sim_stats,
)
from services.research_microstructure import (
    load_data, get_fr, get_oi_change, get_ls_ratio, nearest,
    FR_DATA, OI_DATA, LS_DATA,
)

# Dead zone thresholds
PUT_RET_MAX = -2.5
CALL_RET_MIN = 1.0

# ──────────── Expanded signal generation ────────────

def determine_side_expanded(ret_7d, fr, oi_change, ls_ratio):
    """Expanded: use microstructure to trade in dead zone.
    
    Config B:
      ret < -2.5% → Put
      ret > +1.0% → Call
      -2.5%..+1.0% → dead zone
    
    Expanded dead zone:
      If ret in dead zone:
        - If FR < -0.0005 (strong bearish funding) → Put
        - If FR > +0.0010 (strong bullish funding) → Call
        - If OI rising > 1% in 4h AND ret > 0 → Call (momentum building)
        - If OI rising > 1% in 4h AND ret < 0 → Put (selling pressure)
        - If L/S > 1.15 → Call (extreme long squeeze risk)
        - If L/S < 0.85 → Put (extreme short squeeze risk)
      Otherwise: Config B rules
    """
    if ret_7d < PUT_RET_MAX:
        return "P"
    if ret_7d > CALL_RET_MIN:
        return "C"
    
    # Dead zone — check microstructure
    # 1. Funding rate extremes (contrarian)
    if fr is not None:
        if fr < -0.0005:
            return "P"  # bearish funding → Put
        if fr > 0.0010:
            return "C"  # bullish funding → Call
    
    # 2. OI momentum
    if oi_change is not None and abs(oi_change) > 0.01:
        if ret_7d > 0 and oi_change > 0:
            return "C"  # rising OI + positive ret → Call
        if ret_7d < 0 and oi_change > 0:
            return "P"  # rising OI + negative ret → Put
    
    # 3. L/S extremes
    if ls_ratio is not None:
        if ls_ratio > 1.15:
            return "C"  # extreme long → squeeze risk, sell Call
        if ls_ratio < 0.85:
            return "P"  # extreme short → squeeze risk, sell Put
    
    return None  # stay in dead zone


def generate_expanded_signals(k5, k15, k1h, cutoff_ms):
    """Generate expanded signals with microstructure in dead zone."""
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
        if ts_end < cutoff_ms:
            pass
        
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
        
        # Microstructure data
        fr = get_fr(ts_end)
        oi_change = get_oi_change(ts_end, 4)
        ls_ratio = get_ls_ratio(ts_end)
        
        active_side = determine_side_expanded(ret_7d, fr, oi_change, ls_ratio)
        
        if active_side is None:
            continue
        
        gen_kw = PUT_GEN if active_side == "P" else CALL_GEN
        
        # Vol check
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
        
        # Regime
        regime = detect_regime(s1h)
        rn = regime.get("regime", "unknown")
        if rn == "trend" or rn not in gen_kw["regime_filter"]:
            continue
        
        # MTF
        mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
        md = gen_kw["mtf_direction_filter"]
        if md == "up" and (mtf["direction"] != "up" or mtf["tfs_aligned"] < 2):
            continue
        if md == "down" and (mtf["direction"] != "down" or mtf["tfs_aligned"] < 2):
            continue
        
        # Bull filter
        if active_side == "P" and gen_kw["bull_market_ratio_max"] is not None and len(closes_1h) >= 200:
            from services.indicators import ema as ema_fn
            e50 = ema_fn(closes_1h, 50)
            e200 = ema_fn(closes_1h, 200)
            if e50 and e200 and e200 > 0 and e50 / e200 > gen_kw["bull_market_ratio_max"]:
                continue
        
        # Cooldown
        if i - last_idx < CONSISTENT_CD:
            continue
        
        sig = {
            "idx_5m": i, "ts_ms": ts_end, "close": c5["close"],
            "side": active_side, "position": "short_premium",
            "ret_7d": round(ret_7d, 2),
            "fr": fr, "oi_change": oi_change, "ls_ratio": ls_ratio,
        }
        if ts_end >= cutoff_ms:
            out.append(sig)
        last_idx = i
    
    return out


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Expansion Research: Microstructure in Dead Zone ===", flush=True)
    
    load_data()
    
    k5, k15, k1h = load_local(data_dir)
    last_ms = k5[-1]["start_ms"]
    
    # Use full data for max coverage
    cutoff_ms = 0  # all data
    
    # ── Baseline: Config B ──
    print("\n[1] Config B baseline (full data)...", flush=True)
    cfg = {"put_max": PUT_RET_MAX, "call_min": CALL_RET_MIN}
    base_sigs = gen_baseline(k5, k15, k1h, cfg)
    base_ps = [s for s in base_sigs if s["side"] == "P"]
    base_cs = [s for s in base_sigs if s["side"] == "C"]
    
    base_psim = simulate_signal_set(base_ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if base_ps else []
    base_csim = simulate_signal_set(base_cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if base_cs else []
    
    base_st = sim_stats(base_psim + base_csim)
    print(f"  Config B: n={base_st['n']} WR={base_st['wr']*100:.1f}% "
          f"avg={base_st['avg']:+.2f}% sh={base_st['sharpe']:+.3f} cl={base_st['max_consec_loss']}", flush=True)
    
    # ── Expanded: Config B + microstructure ──
    print(f"\n[2] Expanded signals (microstructure in dead zone)...", flush=True)
    exp_sigs = generate_expanded_signals(k5, k15, k1h, cutoff_ms)
    exp_ps = [s for s in exp_sigs if s["side"] == "P"]
    exp_cs = [s for s in exp_sigs if s["side"] == "C"]
    
    print(f"  Total: {len(exp_sigs)} P={len(exp_ps)} C={len(exp_cs)}", flush=True)
    
    # Count how many are from dead zone
    dead_zone_sigs = [s for s in exp_sigs if PUT_RET_MAX <= s["ret_7d"] <= CALL_RET_MIN]
    print(f"  From dead zone: {len(dead_zone_sigs)} ({len(dead_zone_sigs)/max(len(exp_sigs),1)*100:.0f}%)", flush=True)
    
    exp_psim = simulate_signal_set(exp_ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if exp_ps else []
    exp_csim = simulate_signal_set(exp_cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if exp_cs else []
    
    exp_st = sim_stats(exp_psim + exp_csim)
    print(f"  Expanded: n={exp_st['n']} WR={exp_st['wr']*100:.1f}% "
          f"avg={exp_st['avg']:+.2f}% sh={exp_st['sharpe']:+.3f} cl={exp_st['max_consec_loss']}", flush=True)
    
    # ── Dead zone only ──
    if dead_zone_sigs:
        print(f"\n[3] Dead zone signals only...", flush=True)
        dz_ps = [s for s in dead_zone_sigs if s["side"] == "P"]
        dz_cs = [s for s in dead_zone_sigs if s["side"] == "C"]
        
        dz_psim = simulate_signal_set(dz_ps, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
            option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if dz_ps else []
        dz_csim = simulate_signal_set(dz_cs, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
            option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if dz_cs else []
        
        dz_st = sim_stats(dz_psim + dz_csim)
        print(f"  Dead zone only: n={dz_st['n']} WR={dz_st['wr']*100:.1f}% "
              f"avg={dz_st['avg']:+.2f}% sh={dz_st['sharpe']:+.3f} cl={dz_st['max_consec_loss']}", flush=True)
        if dz_st.get("by_side"):
            for side, ss in dz_st["by_side"].items():
                print(f"    {side}: n={ss['n']} WR={ss['wr']*100:.1f}% avg={ss['avg']:+.2f}%", flush=True)
    
    # ── Comparison ──
    print(f"\n{'='*80}")
    print(f"COMPARISON")
    print(f"  Config B:        n={base_st['n']:>4} WR={base_st['wr']*100:>5.1f}% avg={base_st['avg']:+.2f}% sh={base_st['sharpe']:+.3f} cl={base_st['max_consec_loss']}")
    print(f"  Expanded:        n={exp_st['n']:>4} WR={exp_st['wr']*100:>5.1f}% avg={exp_st['avg']:+.2f}% sh={exp_st['sharpe']:+.3f} cl={exp_st['max_consec_loss']}")
    delta_n = exp_st['n'] - base_st['n']
    delta_avg = exp_st['avg'] - base_st['avg']
    print(f"  Delta:           n={delta_n:>+4} avg={delta_avg:+.2f}%")
    
    if exp_st['avg'] > base_st['avg'] and exp_st['n'] > base_st['n']:
        print(f"\n  ✅ EXPANSION IMPROVED: more signals AND better avg")
    elif exp_st['avg'] < base_st['avg'] and exp_st['n'] > base_st['n']:
        print(f"\n  ⚠️ EXPANSION ADDED SIGNALS but LOWERED avg — need better filters")
    elif exp_st['avg'] > base_st['avg']:
        print(f"\n  ✅ FEWER SIGNALS but HIGHER avg — selective improvement")
    else:
        print(f"\n  ❌ EXPANSION DID NOT IMPROVE")
    
    print(f"\nDone ({round(time.time()-t0,1)}s)", flush=True)


if __name__ == "__main__":
    main()

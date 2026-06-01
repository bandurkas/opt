"""Re-run backtest with all audit fixes applied.

Tests the V3 hybrid config with the actual code from modified files:
  - Consistent cooldown=6 (not 4)
  - BS fallback for TP/SL
  - 7d return switching

Run:
    cd backend && PYTHONPATH=. python3 services/retest_final.py
"""
import statistics, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from services.backtest import simulate_signal_set
from services.holdout_split import holdout_cutoff_ms, split_signals_by_holdout
from services.indicators import ema, realized_vol
from services.local_optimizer import find_data_dir, load_local
from services.momentum_mtf import analyze_tf, consensus
from services.regime import detect_regime
from services.strategy_registry import gen_sell_premium_iv_high

# Updated V3 config (audit round 1 fixes)
RET_THRESHOLD = 2.0
BARS_7D = 2016
CONSISTENT_CD = 6  # max(4, 6) — audit fix #4

PUT_GEN = {"vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
           "adx_max": None, "mtf_direction_filter": "up",
           "bull_market_ratio_max": None, "cooldown_bars": CONSISTENT_CD}
CALL_GEN = {"vol_threshold": 0.60, "regime_filter": ["range", "transition"], "side": "C",
            "adx_max": None, "mtf_direction_filter": "down",
            "bull_market_ratio_max": 1.05, "cooldown_bars": CONSISTENT_CD}
PUT_EXIT = {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 96}
CALL_EXIT = {"tp1": 0.30, "tp2": 0.50, "sl": 1.00, "hold_h": 24}


def compute_ret_7d(k5, idx):
    if idx < BARS_7D:
        return 0.0
    prev = k5[idx - BARS_7D]["close"]
    if prev <= 0:
        return 0.0
    return (k5[idx]["close"] - prev) / prev * 100


def determine_side(ret_7d):
    if abs(ret_7d) < RET_THRESHOLD:
        return "P"
    elif ret_7d > 0:
        return "C"
    else:
        return "P"


def generate_all_signals(k5, k15, k1h):
    """Generate hybrid signals with consistent cooldown=6."""
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

        ret_7d = compute_ret_7d(k5, i)
        active_side = determine_side(ret_7d)
        gen_kw = PUT_GEN if active_side == "P" else CALL_GEN

        # Vol check
        vol_thresh = gen_kw["vol_threshold"]
        closes_1h = [c["close"] for c in s1h]
        if len(closes_1h) < 168 + 20:
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
        threshold = sorted_vols[int(len(sorted_vols) * vol_thresh)]
        if current_vol < threshold:
            continue

        # Regime
        regime = detect_regime(s1h)
        regime_name = regime.get("regime", "unknown")
        if regime_name == "trend":
            continue
        if regime_name not in gen_kw["regime_filter"]:
            continue

        # MTF
        mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
        mtf_dir = gen_kw["mtf_direction_filter"]
        if mtf_dir == "up" and (mtf["direction"] != "up" or mtf["tfs_aligned"] < 2):
            continue
        if mtf_dir == "down" and (mtf["direction"] != "down" or mtf["tfs_aligned"] < 2):
            continue

        # Bull filter for Put
        if active_side == "P":
            bull_max = gen_kw["bull_market_ratio_max"]
            if bull_max is not None and len(closes_1h) >= 200:
                e50 = ema(closes_1h, 50)
                e200 = ema(closes_1h, 200)
                if e50 and e200 and e200 > 0:
                    if e50 / e200 > bull_max:
                        continue

        # Cooldown
        if i - last_idx < CONSISTENT_CD:
            continue

        out.append({
            "idx_5m": i, "ts_ms": ts_end, "close": c5["close"],
            "side": active_side, "position": "short_premium",
            "ret_7d": round(ret_7d, 2),
        })
        last_idx = i

    return out


def apply_cb(sims, consec_limit=5, pause_bars=576):
    sorted_sims = sorted(sims, key=lambda s: s["idx_5m"])
    result = []
    consec = 0
    skip_until = -1
    for s in sorted_sims:
        idx = s["idx_5m"]
        if idx < skip_until:
            continue
        result.append(s)
        pnl = s["option"]["pnl_pct"]
        if pnl < 0:
            consec += 1
            if consec >= consec_limit:
                skip_until = idx + pause_bars
                consec = 0
        else:
            consec = 0
    return result


def sim_stats(sims):
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return None
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    st = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sh = (statistics.mean(pnls) / st) if st > 0 else 0
    mc = cl = 0
    for p in pnls:
        if p < 0:
            cl += 1
            mc = max(mc, cl)
        else:
            cl = 0
    monthly = {}
    by_side = {}
    for s in sims:
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
        m = ts.strftime("%Y-%m")
        pnl = s["option"]["pnl_pct"]
        side = s["side"]
        if pnl is not None:
            monthly.setdefault(m, []).append(pnl)
            by_side.setdefault(side, []).append(pnl)
    losing_months = sum(1 for ps in monthly.values() if statistics.mean(ps) < 0)
    side_stats = {}
    for side, sp in by_side.items():
        side_stats[side] = {
            "n": len(sp), "wr": round(sum(1 for p in sp if p > 0) / len(sp), 3),
            "avg": round(statistics.mean(sp), 2),
        }
    return {
        "n": len(pnls), "wr": round(wr, 3),
        "avg": round(statistics.mean(pnls), 2),
        "sharpe": round(sh, 2), "total": round(sum(pnls), 1),
        "max_consec_loss": mc, "losing_months": losing_months,
        "total_months": len(monthly), "by_side": side_stats,
        "monthly": {m: {"n": len(ps), "avg": round(statistics.mean(ps), 2),
                         "wr": round(sum(1 for p in ps if p > 0) / len(ps), 3)}
                    for m, ps in sorted(monthly.items())},
    }


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Re-test: V3 hybrid with audit fixes ===", flush=True)
    k5, k15, k1h = load_local(data_dir)
    print(f"klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}", flush=True)

    # Generate signals with consistent cd=6
    print("\n[1/4] Generating signals (cd=6 consistent)...", flush=True)
    sigs = generate_all_signals(k5, k15, k1h)
    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]
    print(f"  Total={len(sigs)} P={len(ps)} C={len(cs)}", flush=True)

    # Simulate with per-side exits
    print("[2/4] Simulating Put signals...", flush=True)
    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ps else []
    print(f"  Put sims={len(psim)}", flush=True)

    print("[3/4] Simulating Call signals...", flush=True)
    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if cs else []
    print(f"  Call sims={len(csim)}", flush=True)

    # Apply CB
    print("[4/4] Applying circuit breaker (5 losses → 48h)...", flush=True)
    all_sims = psim + csim
    cb_sims = apply_cb(all_sims, consec_limit=5, pause_bars=576)

    st = sim_stats(cb_sims)
    print(f"\n{'='*70}")
    print(f"V3 Hybrid (audit fixes) — 365d, σ=0.6, spread=2%")
    print(f"{'='*70}")
    print(f"n={st['n']} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
          f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']} lm={st['losing_months']}")
    if st["by_side"]:
        for side, ss in st["by_side"].items():
            print(f"  {side}: n={ss['n']} WR={ss['wr']*100:.1f}% avg={ss['avg']:+.2f}%")

    # Monthly
    print(f"\n{'Month':<10} {'n':>4} {'WR':>6} {'avg':>8} {'sharpe':>7}")
    print("-" * 45)
    for m in sorted(st["monthly"]):
        mm = st["monthly"][m]
        m_avg = mm["avg"]
        m_wr = mm["wr"]
        # Approximate sharpe
        ps2 = []
        for s in cb_sims:
            pnl = s["option"].get("pnl_pct")
            if pnl is None:
                continue
            ts2 = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
            if ts2.strftime("%Y-%m") == m:
                ps2.append(pnl)
        m_std = statistics.stdev(ps2) if len(ps2) > 1 else 0
        m_sh = (statistics.mean(ps2) / m_std) if m_std > 0 else 0
        print(f"  {m}: n={mm['n']:>4} WR={m_wr*100:>5.1f}% avg={m_avg:>+7.2f}% sh={m_sh:+.3f}")

    # Holdout
    print(f"\n[Holdout] last 90d...", flush=True)
    cutoff = holdout_cutoff_ms(k5)
    ho_sigs = [s for s in sigs if s["ts_ms"] >= cutoff]
    ho_ps = [s for s in ho_sigs if s["side"] == "P"]
    ho_cs = [s for s in ho_sigs if s["side"] == "C"]
    ho_psim = simulate_signal_set(ho_ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ho_ps else []
    ho_csim = simulate_signal_set(ho_cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if ho_cs else []
    ho_cb = apply_cb(ho_psim + ho_csim, consec_limit=5, pause_bars=576)
    ho_st = sim_stats(ho_cb)
    print(f"  n={ho_st['n']} WR={ho_st['wr']*100:.1f}% avg={ho_st['avg']:+.2f}% "
          f"sh={ho_st['sharpe']:+.3f} cl={ho_st['max_consec_loss']} lm={ho_st['losing_months']}")

    # PASS/FAIL
    print(f"\n{'='*60}")
    print(f"PASS/FAIL:", flush=True)
    print(f"  {'✅' if st['avg'] > 10 else '❌'} 365d avg > +10%: {st['avg']:+.2f}%")
    print(f"  {'✅' if st['max_consec_loss'] < 20 else '❌'} Consec loss < 20: {st['max_consec_loss']}")
    print(f"  {'✅' if st['losing_months'] < 5 else '❌'} Losing months < 5: {st['losing_months']}")
    print(f"  {'✅' if ho_st['avg'] > 5 else '❌'} Holdout avg > +5%: {ho_st['avg']:+.2f}%")
    print(f"  {'✅' if ho_st['max_consec_loss'] < 20 else '❌'} Holdout cl < 20: {ho_st['max_consec_loss']}")

    # Compare with old results
    print(f"\n{'='*60}")
    print(f"COMPARISON vs previous run (cd=4):")
    print(f"  Previous: n=417 WR=76.5% avg=+22.64% sh=+0.45 cl=18")
    print(f"  Now:      n={st['n']} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
          f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']}")
    print(f"{'='*60}")
    print(f"Done ({round(time.time() - t0, 1)}s)")


if __name__ == "__main__":
    main()

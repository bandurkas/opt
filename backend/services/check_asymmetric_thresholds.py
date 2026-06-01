"""Test asymmetric thresholds: Call easier, Put harder.

Current: |ret|<2%→Put, ret>+2%→Call, ret<-2%→Put
Problem: 19 Put signals in May when |ret|<2% but market was slowly falling

Hypothesis: Put should ONLY fire on clear downtrend (ret<-2%).
Between -2% and +1% → NO TRADE (don't fight slow bleed).

Test multiple asymmetric configs:
  A: ret<-2%→Put, ret>+1%→Call (rest: skip)
  B: ret<-2.5%→Put, ret>+1%→Call (rest: skip)
  C: ret<-2%→Put, ret>+1.5%→Call (rest: skip)
  D: ret<-1.5%→Put, ret>+2%→Call (rest: skip)

Run:
    cd backend && PYTHONPATH=. python3 services/check_asymmetric_thresholds.py
"""
import statistics, sys, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")
from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir, load_local
from services.indicators import ema, realized_vol
from services.momentum_mtf import analyze_tf, consensus
from services.regime import detect_regime

BARS_7D = 2016
CONSISTENT_CD = 6

PUT_GEN = {"vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
           "adx_max": None, "mtf_direction_filter": "up",
           "bull_market_ratio_max": None, "cooldown_bars": CONSISTENT_CD}
CALL_GEN = {"vol_threshold": 0.60, "regime_filter": ["range", "transition"], "side": "C",
            "adx_max": None, "mtf_direction_filter": "down",
            "bull_market_ratio_max": 1.05, "cooldown_bars": CONSISTENT_CD}
PUT_EXIT = {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 96}
CALL_EXIT = {"tp1": 0.30, "tp2": 0.50, "sl": 1.00, "hold_h": 24}

MS_PER_DAY = 86_400_000

# Asymmetric configs to test
CONFIGS = [
    {"name": "A: ret<-2%→Put, ret>+1%→Call", "put_min": None, "put_max": -2.0, "call_min": 1.0, "call_max": None},
    {"name": "B: ret<-2.5%→Put, ret>+1%→Call", "put_min": None, "put_max": -2.5, "call_min": 1.0, "call_max": None},
    {"name": "C: ret<-2%→Put, ret>+1.5%→Call", "put_min": None, "put_max": -2.0, "call_min": 1.5, "call_max": None},
    {"name": "D: ret<-1.5%→Put, ret>+2%→Call", "put_min": None, "put_max": -1.5, "call_min": 2.0, "call_max": None},
    {"name": "E: ret<-3%→Put, ret>+1%→Call", "put_min": None, "put_max": -3.0, "call_min": 1.0, "call_max": None},
    {"name": "F: ret<-2%→Put, ret>+0.5%→Call", "put_min": None, "put_max": -2.0, "call_min": 0.5, "call_max": None},
]


def compute_ret_7d(k5, idx):
    if idx < BARS_7D:
        return 0.0
    prev = k5[idx - BARS_7D]["close"]
    if prev <= 0:
        return 0.0
    return (k5[idx]["close"] - prev) / prev * 100


def determine_side_asymmetric(ret_7d, put_max, call_min):
    """Asymmetric: ret < put_max → Put, ret > call_min → Call, else skip."""
    if ret_7d < put_max:
        return "P"
    elif ret_7d > call_min:
        return "C"
    return None


def generate_signals(k5, k15, k1h, cutoff_ms, put_max, call_min):
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
        active_side = determine_side_asymmetric(ret_7d, put_max, call_min)

        if active_side is None:
            continue  # skip — no trade zone

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

        sig = {
            "idx_5m": i, "ts_ms": ts_end, "close": c5["close"],
            "side": active_side, "position": "short_premium",
            "ret_7d": round(ret_7d, 2),
        }
        if ts_end >= cutoff_ms:
            out.append(sig)
        last_idx = i

    return out


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
    return {"n": len(pnls), "wr": round(wr, 3), "avg": round(statistics.mean(pnls), 2),
            "sharpe": round(sh, 2), "total": round(sum(pnls), 1), "max_consec_loss": mc}


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Asymmetric Threshold Test (Last 30d) ===", flush=True)
    k5, k15, k1h = load_local(data_dir)

    last_ms = k5[-1]["start_ms"]
    cutoff_ms = last_ms - 30 * MS_PER_DAY
    cutoff_date = datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last_date = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"Window: {cutoff_date} → {last_date}", flush=True)

    results = []

    for cfg in CONFIGS:
        name = cfg["name"]
        print(f"\n--- {name} ---", flush=True)
        sigs = generate_signals(k5, k15, k1h, cutoff_ms, cfg["put_max"], cfg["call_min"])
        ps = [s for s in sigs if s["side"] == "P"]
        cs = [s for s in sigs if s["side"] == "C"]
        print(f"  Signals: {len(sigs)} P={len(ps)} C={len(cs)}", flush=True)

        if not sigs:
            results.append({**cfg, "n": 0, "avg": None, "wr": None, "sh": None, "cl": 0})
            continue

        psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
            option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ps else []
        csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
            option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if cs else []

        all_sims = psim + csim
        st = sim_stats(all_sims)
        results.append({**cfg, **st})
        print(f"  n={st['n']} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
              f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']}", flush=True)

    # Summary table
    print(f"\n{'='*90}")
    print(f"{'Config':<40} {'n':>4} {'WR':>6} {'avg':>8} {'sh':>6} {'cl':>4}")
    print("-" * 90)
    for r in results:
        avg_str = f"{r['avg']:+.2f}%" if r['avg'] is not None else "N/A"
        sh_str = f"{r['sharpe']:+.3f}" if r['sharpe'] is not None else "N/A"
        wr_str = f"{r['wr']*100:.1f}%" if r['wr'] is not None else "N/A"
        print(f"{r['name']:<40} {r['n']:>4} {wr_str:>6} {avg_str:>8} {sh_str:>6} {r['max_consec_loss']:>4}")

    # Best config
    positive = [r for r in results if r['avg'] is not None and r['avg'] > 0]
    if positive:
        best = max(positive, key=lambda r: r['avg'] * max(0.1, r['sharpe'] or 0))
        print(f"\n  ✅ BEST: {best['name']} — n={best['n']} avg={best['avg']:+.2f}% sh={best['sharpe']:+.3f}")
    else:
        print(f"\n  ❌ All configs negative for last 30d")

    print(f"\nDone ({round(time.time() - t0, 1)}s)")


if __name__ == "__main__":
    main()

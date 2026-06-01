"""Check V3 hybrid strategy performance on the LAST 30 days.

This is the CRITICAL test — the previous Put-only config lost -50.6% in May.
With 7d-return switching to Call during uptrends, May 2026 should improve
since Call was performing +22.49% in that same period.

Run:
    cd backend && PYTHONPATH=. python3 services/check_last_30d.py
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

RET_THRESHOLD = 2.0
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


def generate_signals_last_30d(k5, k15, k1h, cutoff_ms):
    """Generate hybrid signals, but only keep those from the last 30 days."""
    out = []
    last_idx = -10000
    i15 = 0
    i1h = 0
    HIST = 240

    for i, c5 in enumerate(k5):
        ts_end = c5["start_ms"] + 5 * 60 * 1000
        if ts_end < cutoff_ms:
            # Still need to process for indicator history, but don't emit signals
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

        sig = {
            "idx_5m": i, "ts_ms": ts_end, "close": c5["close"],
            "side": active_side, "position": "short_premium",
            "ret_7d": round(ret_7d, 2),
        }

        # Only keep signals from last 30 days
        if ts_end >= cutoff_ms:
            out.append(sig)

        last_idx = i

    return out


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Last 30 Days Check: V3 Hybrid ===", flush=True)
    k5, k15, k1h = load_local(data_dir)

    # Calculate cutoff (30 days ago from last bar)
    last_ms = k5[-1]["start_ms"]
    cutoff_ms = last_ms - 30 * MS_PER_DAY
    cutoff_date = datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last_date = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    print(f"Data span: {datetime.fromtimestamp(k5[0]['start_ms']/1000).strftime('%Y-%m-%d')} → {last_date}")
    print(f"Testing window: {cutoff_date} → {last_date} (last 30 days)", flush=True)

    # ── Generate signals ──
    print(f"\n[1] Generating signals...", flush=True)
    sigs = generate_signals_last_30d(k5, k15, k1h, cutoff_ms)
    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]
    print(f"  Total={len(sigs)} P={len(ps)} C={len(cs)}", flush=True)

    if not sigs:
        print("  NO SIGNALS in last 30 days!", flush=True)
        return

    # ── Show 7d return distribution ──
    print(f"\n[2] 7d return distribution during this period:", flush=True)
    rets = [s["ret_7d"] for s in sigs]
    avg_ret = statistics.mean(rets)
    print(f"  Mean 7d ret: {avg_ret:+.2f}%")
    print(f"  Signals with |ret|<2% (Put): {sum(1 for r in rets if abs(r)<2)}")
    print(f"  Signals with ret>+2% (Call): {sum(1 for r in rets if r>2)}")
    print(f"  Signals with ret<-2% (Put): {sum(1 for r in rets if r<-2)}")

    # ── Simulate ──
    print(f"\n[3] Simulating Put signals...", flush=True)
    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ps else []

    print(f"[4] Simulating Call signals...", flush=True)
    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if cs else []

    all_sims = psim + csim

    # ── Stats ──
    print(f"\n{'='*70}")
    print(f"LAST 30 DAYS: {cutoff_date} → {last_date}")
    print(f"{'='*70}")

    pnls = [s["option"]["pnl_pct"] for s in all_sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        print("  No completed trades!", flush=True)
        return

    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    avg = statistics.mean(pnls)
    st = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sh = (avg / st) if st > 0 else 0
    mc = cl = 0
    for p in pnls:
        if p < 0:
            cl += 1
            mc = max(mc, cl)
        else:
            cl = 0

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    print(f"  n={len(pnls)} WR={wr*100:.1f}% avg={avg:+.2f}% sh={sh:+.3f}")
    print(f"  Max consec loss: {mc}")
    print(f"  Wins: {len(wins)} avg={statistics.mean(wins):+.2f}%")
    print(f"  Losses: {len(losses)} avg={statistics.mean(losses):+.2f}%")
    print(f"  Worst: {min(pnls):+.2f}% | Best: {max(pnls):+.2f}%")

    # Per-side breakdown
    print(f"\n  Per-side breakdown:")
    for side_name, side_sims in [("Put", psim), ("Call", csim)]:
        side_pnls = [s["option"]["pnl_pct"] for s in side_sims if "pnl_pct" in s.get("option", {})]
        if side_pnls:
            sw = sum(1 for p in side_pnls if p > 0) / len(side_pnls)
            sa = statistics.mean(side_pnls)
            print(f"    {side_name}: n={len(side_pnls)} WR={sw*100:.1f}% avg={sa:+.2f}%")

    # Per-week breakdown
    print(f"\n  Weekly breakdown:")
    weekly = {}
    for s in all_sims:
        pnl = s["option"].get("pnl_pct")
        if pnl is None:
            continue
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
        week = ts.strftime("W%W-%a")  # week number + start day
        weekly.setdefault(week, []).append(pnl)
    for w in sorted(weekly):
        ps2 = weekly[w]
        wa = statistics.mean(ps2)
        ww = sum(1 for p in ps2 if p > 0) / len(ps2)
        print(f"    {w}: n={len(ps2):>3} WR={ww*100:>5.1f}% avg={wa:+7.2f}%")

    # ── Compare with old Put-only config ──
    print(f"\n{'='*70}")
    print(f"COMPARISON: Put-only (old) vs V3 Hybrid (new) — Last 30 days")
    print(f"{'='*70}")
    print(f"  Put-only (old):  n=46  WR=10.9%  avg=-50.60%  (May 2026)")
    print(f"  Call-only:       n=139 WR=82.0%  avg=+22.49%  (May 2026)")
    print(f"  V3 Hybrid (new): n={len(pnls)} WR={wr*100:.1f}%  avg={avg:+.2f}%")

    if avg > 0:
        print(f"\n  ✅ V3 Hybrid FIXED the May crash!")
        print(f"  Switched to Call when 7d return was positive (ETH rallying)")
    else:
        print(f"\n  ❌ Still negative — needs further tuning")
        print(f"  Consider: higher 7d threshold, tighter SL, or different exits")

    print(f"\nDone ({round(time.time() - t0, 1)}s)")


if __name__ == "__main__":
    main()

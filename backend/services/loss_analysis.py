"""Root cause analysis: WHY are there 41+ consecutive losses?

Analyze every losing trade in Pure Put (cd=4, h=96) to find:
  1. Which months contain the consec loss clusters
  2. What regime/MTF/vol/ADX/7d-return conditions were present during losses
  3. What filter(s) would have prevented the most losses

Run:
    cd backend && PYTHONPATH=. python3 services/loss_analysis.py
"""
from __future__ import annotations

import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.indicators import adx, ema, realized_vol
from services.local_optimizer import find_data_dir, load_local
from services.momentum_mtf import analyze_tf, consensus
from services.regime import detect_regime
from services.strategy_registry import gen_sell_premium_iv_high


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Loss Analysis: WHY 41 consecutive losses? ===", flush=True)

    k5, k15, k1h = load_local(data_dir)
    print(f"klines: 5m={len(k5):,}", flush=True)

    put_gen = {
        "vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
        "adx_max": None, "mtf_direction_filter": "up",
        "bull_market_ratio_max": None, "cooldown_bars": 4,
    }

    sigs = gen_sell_premium_iv_high(k5, k15, k1h, **put_gen)
    print(f"Put signals: {len(sigs)}", flush=True)

    sims = simulate_signal_set(
        sigs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=0.50, tp2_pct=0.70, sl_pct=1.50, option_horizon_h=96, spread_pct=2.0,
    )

    # Separate wins and losses
    losses = []
    wins = []
    for s in sims:
        pnl = s.get("option", {}).get("pnl_pct")
        if pnl is None:
            continue
        if pnl < 0:
            losses.append(s)
        else:
            wins.append(s)

    print(f"\nTotal: {len(wins)} wins, {len(losses)} losses", flush=True)

    # ── 1. Find consecutive loss clusters ──
    print(f"\n{'='*80}")
    print(f"CONSECUTIVE LOSS CLUSTERS")
    print(f"{'='*80}", flush=True)

    all_trades = sorted(sims, key=lambda s: s["ts_ms"])
    clusters = []
    current_cluster = []
    for s in all_trades:
        pnl = s.get("option", {}).get("pnl_pct", 0)
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
        if pnl < 0:
            current_cluster.append({"trade": s, "pnl": pnl, "ts": ts, "month": ts.strftime("%Y-%m")})
        else:
            if len(current_cluster) >= 5:  # clusters of 5+
                clusters.append(current_cluster)
            current_cluster = []
    if len(current_cluster) >= 5:
        clusters.append(current_cluster)

    clusters.sort(key=lambda c: len(c), reverse=True)
    for i, cl in enumerate(clusters[:10], 1):
        start = cl[0]["ts"].strftime("%Y-%m-%d %H:%M")
        end = cl[-1]["ts"].strftime("%Y-%m-%d %H:%M")
        avg_pnl = statistics.mean(c["pnl"] for c in cl)
        months = set(c["month"] for c in cl)
        print(f"  Cluster #{i}: {len(cl)} losses, avg={avg_pnl:+.1f}%, {start} → {end}, months={months}", flush=True)

    # ── 2. Analyze market conditions at each loss ──
    print(f"\n{'='*80}")
    print(f"MARKET CONDITIONS AT EACH LOSS (top 10 largest clusters)")
    print(f"{'='*80}", flush=True)

    # Build index for fast lookup
    BARS_7D = 2016
    k5_idx = {int(k["start_ms"]): i for i, k in enumerate(k5)}

    for ci, cl in enumerate(clusters[:5], 1):
        print(f"\n--- Cluster #{ci}: {len(cl)} losses ({cl[0]['month']}) ---", flush=True)

        for trade in cl[:10]:  # first 10 trades in cluster
            s = trade["trade"]
            idx = s["idx_5m"]
            ts = trade["ts"]

            if idx < 60 or idx >= len(k5):
                continue

            # Get recent klines for indicators
            s5 = k5[max(0, idx - 240):idx + 1]
            s15 = []
            s1h = []
            # Find matching 15m/1h indices
            i15 = 0
            i1h = 0
            for j, c15 in enumerate(k15):
                if c15["start_ms"] + 15 * 60 * 1000 <= s["ts_ms"]:
                    i15 = j
            for j, c1h in enumerate(k1h):
                if c1h["start_ms"] + 60 * 60 * 1000 <= s["ts_ms"]:
                    i1h = j
            s15 = k15[max(0, i15 - 240):i15]
            s1h = k1h[max(0, i1h - 240):i1h]

            if len(s1h) < 50:
                continue

            # Regime
            regime = detect_regime(s1h)
            regime_name = regime.get("regime", "?")
            adx_val = regime.get("adx", 0)

            # MTF
            mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))

            # 7d return
            if idx >= BARS_7D:
                ret_7d = (k5[idx]["close"] - k5[idx - BARS_7D]["close"]) / k5[idx - BARS_7D]["close"] * 100
            else:
                ret_7d = 0

            # EMA50/200 ratio
            closes_1h = [c["close"] for c in s1h]
            if len(closes_1h) >= 200:
                e50 = ema(closes_1h, 50)
                e200 = ema(closes_1h, 200)
                ema_ratio = e50 / e200 if e200 else 0
            else:
                ema_ratio = 0

            # Vol percentile
            if len(closes_1h) >= 168 + 20:
                rolling_vols = []
                for j in range(20, len(closes_1h)):
                    rv = realized_vol(closes_1h[:j + 1], lookback=24)
                    if rv is not None:
                        rolling_vols.append(rv)
                if len(rolling_vols) >= 30:
                    current_vol = rolling_vols[-1]
                    sorted_vols = sorted(rolling_vols)
                    threshold_idx = int(len(sorted_vols) * 0.50)
                    threshold = sorted_vols[threshold_idx]
                    below = sum(1 for v in sorted_vols if v < current_vol)
                    vol_pctile = below / len(sorted_vols)
                else:
                    vol_pctile = 0
            else:
                vol_pctile = 0

            print(f"  {ts.strftime('%m-%d %H:%M')}: pnl={trade['pnl']:+.1f}%  "
                  f"regime={regime_name} adx={adx_val:.0f}  "
                  f"mtf={mtf['direction']}({mtf['tfs_aligned']})  "
                  f"7d_ret={ret_7d:+.2f}%  "
                  f"ema50/200={ema_ratio:.3f}  "
                  f"vol_pct={vol_pctile:.2f}", flush=True)

    # ── 3. Hypothesis testing: what filter would help? ──
    print(f"\n{'='*80}")
    print(f"HYPOTHESIS TEST: Which filter would reduce consec losses?")
    print(f"{'='*80}", flush=True)

    # Re-run with various filters and check max_consec_loss
    filters = [
        ("BASELINE (no extra filter)", put_gen, None, None, None),
        ("ADX < 25", {**put_gen, "regime_filter": ["range"]}, lambda r: (r.get("adx") or 999) < 25, None, None),
        ("ADX < 20", {**put_gen, "regime_filter": ["range"]}, lambda r: (r.get("adx") or 999) < 20, None, None),
        ("|7d_ret| < 3%", put_gen, None, 3.0, None),
        ("|7d_ret| < 2%", put_gen, None, 2.0, None),
        ("|7d_ret| < 1.5%", put_gen, None, 1.5, None),
        ("|7d_ret| < 1%", put_gen, None, 1.0, None),
        ("EMA50/200 < 1.05", put_gen, None, None, 1.05),
        ("EMA50/200 < 1.03", put_gen, None, None, 1.03),
        ("ADX<25 + |7d|<2%", {**put_gen, "regime_filter": ["range"]}, lambda r: (r.get("adx") or 999) < 25, 2.0, None),
        ("ADX<20 + |7d|<1.5%", {**put_gen, "regime_filter": ["range"]}, lambda r: (r.get("adx") or 999) < 20, 1.5, None),
        ("ADX<25 + EMA<1.05", {**put_gen, "regime_filter": ["range"]}, lambda r: (r.get("adx") or 999) < 25, None, 1.05),
    ]

    for fname, gen, adx_fn, ret7d_max, ema_max in filters:
        # Filter signals
        filtered_sigs = []
        BARS_7D = 2016
        for s in sigs:
            idx = s["idx_5m"]
            if idx < 60 or idx >= len(k5):
                continue

            # Get s1h for regime/ADX/EMA
            i1h = 0
            for j, c1h in enumerate(k1h):
                if c1h["start_ms"] + 60 * 60 * 1000 <= s["ts_ms"]:
                    i1h = j
            s1h = k1h[max(0, i1h - 240):i1h]
            if len(s1h) < 50:
                continue

            regime = detect_regime(s1h)
            if adx_fn and not adx_fn(regime):
                continue

            if ema_max is not None and len(s1h) >= 200:
                closes_1h = [c["close"] for c in s1h]
                e50 = ema(closes_1h, 50)
                e200 = ema(closes_1h, 200)
                if e50 and e200 and e200 > 0:
                    if e50 / e200 > ema_max:
                        continue

            if ret7d_max is not None and idx >= BARS_7D:
                ret_7d = (k5[idx]["close"] - k5[idx - BARS_7D]["close"]) / k5[idx - BARS_7D]["close"] * 100
                if abs(ret_7d) > ret7d_max:
                    continue

            filtered_sigs.append(s)

        if not filtered_sigs:
            print(f"  {fname}: 0 signals after filter", flush=True)
            continue

        filtered_sims = simulate_signal_set(
            filtered_sigs, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=0.50, tp2_pct=0.70, sl_pct=1.50, option_horizon_h=96, spread_pct=2.0,
        )

        pnls = [s["option"]["pnl_pct"] for s in filtered_sims if "pnl_pct" in s.get("option", {})]
        if not pnls:
            continue

        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        avg = statistics.mean(pnls)
        st = statistics.stdev(pnls) if len(pnls) > 1 else 0
        sh = (avg / st) if st > 0 else 0

        mc = cl = 0
        for p in pnls:
            cl = cl + 1 if p < 0 else 0
            mc = max(mc, cl)

        losses_n = sum(1 for p in pnls if p < 0)

        print(f"  {fname:<30} n={len(pnls):>4} WR={wr*100:5.1f}% avg={avg:+6.2f}% "
              f"sh={sh:+.3f} cl={mc:>2} losses={losses_n}", flush=True)

    print(f"\nAnalysis complete ({round(time.time() - t0, 1)}s)", flush=True)


if __name__ == "__main__":
    main()

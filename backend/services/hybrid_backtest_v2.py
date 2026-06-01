"""Hybrid v2: Add 7d return filter for better side selection.

Instead of pure MTF switching, use underlying trend bias:
  • 7d return > +1.5% → only Put (uptrend — Calls dangerous)
  • 7d return < -1.5% → only Call (downtrend — Puts dangerous)
  • Between -1.5% and +1.5% → MTF decides (range regime)

Also test multiple 7d thresholds: 0.5%, 1.0%, 1.5%, 2.0%

Run:
    cd backend && PYTHONPATH=. python3 services/hybrid_backtest_v2.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.indicators import ema, realized_vol
from services.local_optimizer import find_data_dir, load_local
from services.momentum_mtf import analyze_tf, consensus
from services.regime import detect_regime
from services.strategy_registry import gen_sell_premium_iv_high


def generate_hybrid_v2(k5, k15, k1h, *,
                       put_gen: dict, call_gen: dict,
                       ret_7d_threshold: float = 1.5,
                       history_window: int = 240) -> list[dict]:
    """Hybrid with 7d return filter.

    Per 5m bar:
      1. Compute 7d return
      2. 7d_ret > +threshold → ONLY Put allowed
      3. 7d_ret < -threshold → ONLY Call allowed
      4. |7d_ret| < threshold → both allowed (range market)
      5. Within allowed side(s), apply MTF + regime + vol filters
    """
    out: list[dict] = []
    last_idx = -10_000
    i15 = 0
    i1h = 0
    BARS_7D = 2016  # 7 * 24 * 12 = 2016 5m bars

    for i, c5 in enumerate(k5):
        ts_end = c5["start_ms"] + 5 * 60 * 1000
        while i15 < len(k15) and k15[i15]["start_ms"] + 15 * 60 * 1000 <= ts_end:
            i15 += 1
        while i1h < len(k1h) and k1h[i1h]["start_ms"] + 60 * 60 * 1000 <= ts_end:
            i1h += 1

        s5 = k5[max(0, i + 1 - history_window):i + 1]
        s15 = k15[max(0, i15 - history_window):i15]
        s1h = k1h[max(0, i1h - history_window):i1h]

        if i < 60 or i < BARS_7D or len(s5) < 50 or len(s15) < 50 or len(s1h) < 200:
            continue

        # 7d return
        prev_close = k5[i - BARS_7D]["close"]
        ret_7d = (c5["close"] - prev_close) / prev_close * 100

        # Determine allowed sides
        if ret_7d > ret_7d_threshold:
            allowed_sides = ["P"]  # uptrend → sell Put only
        elif ret_7d < -ret_7d_threshold:
            allowed_sides = ["C"]  # downtrend → sell Call only
        else:
            allowed_sides = ["P", "C"]  # range → both

        regime = detect_regime(s1h)
        regime_name = regime.get("regime", "unknown")

        # Skip trending regime
        if regime_name == "trend":
            continue

        mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
        direction = mtf["direction"]
        aligned = mtf["tfs_aligned"]

        # Vol check
        vol_thresh = put_gen["vol_threshold"]  # use Put threshold for all
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

        # Determine which side fires (if any)
        emitted_side = None
        for side in allowed_sides:
            gen = put_gen if side == "P" else call_gen
            side_vol_thresh = gen["vol_threshold"]
            threshold = sorted_vols[int(len(sorted_vols) * side_vol_thresh)]
            if current_vol < threshold:
                continue

            if side == "P" and regime_name not in gen.get("regime_filter", ["range"]):
                continue
            if side == "C" and regime_name not in gen.get("regime_filter", ["range", "transition"]):
                continue

            # MTF filter
            mtf_dir = gen.get("mtf_direction_filter")
            if mtf_dir == "up" and (direction != "up" or aligned < 2):
                continue
            if mtf_dir == "down" and (direction != "down" or aligned < 2):
                continue

            # Bull filter for Put
            if side == "P":
                bull_max = gen.get("bull_market_ratio_max")
                if bull_max is not None and len(closes_1h) >= 200:
                    ema50 = ema(closes_1h, 50)
                    ema200 = ema(closes_1h, 200)
                    if ema50 and ema200 and ema200 > 0:
                        if ema50 / ema200 > bull_max:
                            continue

            emitted_side = side
            break  # first matching side wins

        if emitted_side is None:
            continue

        # Cooldown
        cd = max(put_gen.get("cooldown_bars", 4), call_gen.get("cooldown_bars", 6))
        if i - last_idx < cd:
            continue

        out.append({
            "idx_5m": i,
            "ts_ms": ts_end,
            "close": c5["close"],
            "side": emitted_side,
            "signal_type": f"hybrid_v2_ret{ret_7d_threshold}",
            "regime": regime_name,
            "mtf_direction": direction,
            "mtf_aligned": aligned,
            "ret_7d": round(ret_7d, 2),
            "position": "short_premium",
        })
        last_idx = i

    return out


def _sim_stats(sims):
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return None
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    st = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sh = (statistics.mean(pnls) / st) if st > 0 else 0
    mc = cl = 0
    for p in pnls:
        cl = cl + 1 if p < 0 else 0
        mc = max(mc, cl)

    monthly = {}
    by_side = {}
    for s in sims:
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
        m = ts.strftime("%Y-%m")
        pnl = s.get("option", {}).get("pnl_pct")
        side = s.get("side", "?")
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
        "sharpe": round(sh, 2),
        "total": round(sum(pnls), 1),
        "max_consec_loss": mc,
        "losing_months": losing_months,
        "total_months": len(monthly),
        "by_side": side_stats,
        "monthly": {m: {"n": len(ps), "avg": round(statistics.mean(ps), 2),
                         "wr": round(sum(1 for p in ps if p > 0) / len(ps), 3)}
                    for m, ps in sorted(monthly.items())},
    }


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    print(f"=== Hybrid v2: 7d return filter ===", flush=True)
    print(f"  data: {data_dir}", flush=True)

    k5, k15, k1h = load_local(data_dir)
    print(f"  klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}", flush=True)

    put_gen = {
        "vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
        "adx_max": None, "mtf_direction_filter": "up",
        "bull_market_ratio_max": None, "cooldown_bars": 4,
    }
    put_exit = {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 96}

    call_gen = {
        "vol_threshold": 0.60, "regime_filter": ["range", "transition"], "side": "C",
        "adx_max": None, "mtf_direction_filter": "down",
        "bull_market_ratio_max": 1.05, "cooldown_bars": 6,
    }
    call_exit = {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24}

    # Sweep over 7d thresholds
    thresholds = [0.5, 1.0, 1.5, 2.0, 3.0]
    results = {}

    # Baselines
    print("\n[0] Baselines...", flush=True)
    for name, gen, ex in [("Pure Put", put_gen, put_exit), ("Pure Call", call_gen, call_exit)]:
        sigs = gen_sell_premium_iv_high(k5, k15, k1h, **gen)
        sims = simulate_signal_set(sigs, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=ex["tp1"], tp2_pct=ex["tp2"], sl_pct=ex["sl"],
            option_horizon_h=ex["hold_h"], spread_pct=2.0)
        st = _sim_stats(sims)
        results[name] = st
        print(f"  {name}: n={st['n']} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
              f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']} lm={st['losing_months']}", flush=True)

    for thr in thresholds:
        print(f"\n[thr={thr}%] Generating hybrid signals...", flush=True)
        sigs = generate_hybrid_v2(k5, k15, k1h, put_gen=put_gen, call_gen=call_gen,
                                  ret_7d_threshold=thr)

        put_sigs = [s for s in sigs if s["side"] == "P"]
        call_sigs = [s for s in sigs if s["side"] == "C"]
        print(f"  Total={len(sigs)} Put={len(put_sigs)} Call={len(call_sigs)}", flush=True)

        put_sims = simulate_signal_set(put_sigs, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=put_exit["tp1"], tp2_pct=put_exit["tp2"], sl_pct=put_exit["sl"],
            option_horizon_h=put_exit["hold_h"], spread_pct=2.0) if put_sigs else []

        call_sims = simulate_signal_set(call_sigs, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=call_exit["tp1"], tp2_pct=call_exit["tp2"], sl_pct=call_exit["sl"],
            option_horizon_h=call_exit["hold_h"], spread_pct=2.0) if call_sigs else []

        all_sims = put_sims + call_sims
        st = _sim_stats(all_sims)
        name = f"Hybrid_7d_{thr}"
        results[name] = st

        print(f"  Hybrid: n={st['n']} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
              f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']} lm={st['losing_months']}", flush=True)
        if st.get("by_side"):
            for side, ss in st["by_side"].items():
                print(f"    {side}: n={ss['n']} WR={ss['wr']*100:.1f}% avg={ss['avg']:+.2f}%", flush=True)

    # Summary table
    print(f"\n{'='*100}")
    print(f"{'Config':<25} {'n':>5} {'WR':>6} {'avg':>8} {'sharpe':>7} "
          f"{'total':>10} {'cl':>4} {'lm':>4}")
    print("-" * 100)
    for name in ["Pure Put", "Pure Call"] + [f"Hybrid_7d_{t}" for t in thresholds]:
        st = results[name]
        print(f"{name:<25} {st['n']:>5} {st['wr']*100:>5.1f}% {st['avg']:>+7.2f}% "
              f"{st['sharpe']:>+6.3f} {st['total']:>+9.1f}% {st['max_consec_loss']:>4} {st['losing_months']:>4}")

    # Monthly comparison for best hybrid
    best_thr = max(thresholds, key=lambda t: results[f"Hybrid_7d_{t}"]["avg"])
    best_name = f"Hybrid_7d_{best_thr}"
    print(f"\n{'='*90}")
    print(f"Monthly breakdown — Best: {best_name}")
    print(f"{'Month':<10} {'Put_avg':>9} {'Call_avg':>9} {'Hyb_avg':>9} {'Hyb_WR':>7}")
    print("-" * 70)
    hyb_monthly = results[best_name].get("monthly", {})
    put_monthly = results["Pure Put"].get("monthly", {})
    call_monthly = results["Pure Call"].get("monthly", {})
    for m in sorted(hyb_monthly.keys()):
        pm = put_monthly.get(m, {})
        cm = call_monthly.get(m, {})
        hm = hyb_monthly.get(m, {})
        print(f"  {m}:  Put {pm.get('avg', 0):>+8.2f}%  Call {cm.get('avg', 0):>+8.2f}%  "
              f"Hyb {hm.get('avg', 0):>+8.2f}% {hm.get('wr', 0)*100:5.1f}%")

    # Save
    repo = Path(__file__).resolve().parents[2]
    out_path = repo / "sweep_results" / "hybrid_backtest_v2_7d_return.json"
    payload = {
        "results": {k: v for k, v in results.items()},
        "best_threshold": best_thr,
        "elapsed_s": round(time.time() - t0, 1),
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nSaved → {out_path} ({round(time.time() - t0, 1)}s)", flush=True)


if __name__ == "__main__":
    main()

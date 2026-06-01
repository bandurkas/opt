"""Switching Hybrid backtest — the key missing experiment.

Instead of pure Put or pure Call, switch sides based on MTF regime:
  • MTF up + regime=range → sell Put (LIVE winner config)
  • MTF down + regime ∈ {range, transition} → sell Call (BASELINE config)
  • regime=trend → skip (no entry)

This should capture the anti-correlation between Put/Call performance
across different ETH market phases.

Run:
    cd backend && PYTHONPATH=. python3 services/hybrid_backtest.py
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
from services.holdout_split import HOLDOUT_DAYS, holdout_cutoff_ms, split_signals_by_holdout
from services.indicators import ema
from services.local_optimizer import find_data_dir, load_local
from services.momentum_mtf import analyze_tf, consensus
from services.regime import detect_regime
from services.strategy_registry import gen_sell_premium_iv_high


def generate_hybrid_signals(k5, k15, k1h, *,
                            put_gen: dict, call_gen: dict,
                            history_window: int = 240) -> list[dict]:
    """Walk forward and emit Put OR Call signal based on MTF regime.

    Logic per 5m bar:
      1. Compute regime (1h ADX)
      2. Compute MTF consensus
      3. If regime=trend → skip
      4. If MTF up & regime ∈ put_regimes → emit Put
      5. If MTF down & regime ∈ call_regimes → emit Call
      6. If MTF neutral → skip
    """
    out: list[dict] = []
    last_idx = -10_000
    i15 = 0
    i1h = 0

    for i, c5 in enumerate(k5):
        ts_end = c5["start_ms"] + 5 * 60 * 1000
        while i15 < len(k15) and k15[i15]["start_ms"] + 15 * 60 * 1000 <= ts_end:
            i15 += 1
        while i1h < len(k1h) and k1h[i1h]["start_ms"] + 60 * 60 * 1000 <= ts_end:
            i1h += 1

        s5 = k5[max(0, i + 1 - history_window):i + 1]
        s15 = k15[max(0, i15 - history_window):i15]
        s1h = k1h[max(0, i1h - history_window):i1h]

        if i < 60 or len(s5) < 50 or len(s15) < 50 or len(s1h) < 200:
            continue

        regime = detect_regime(s1h)
        regime_name = regime.get("regime", "unknown")

        # Skip trending regime (neither side works well)
        if regime_name == "trend":
            continue

        mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
        direction = mtf["direction"]
        aligned = mtf["tfs_aligned"]

        # Decide which side to trade
        should_emit = None
        if direction == "up" and aligned >= 2:
            if regime_name in put_gen.get("regime_filter", ["range"]):
                should_emit = "P"
        elif direction == "down" and aligned >= 2:
            if regime_name in call_gen.get("regime_filter", ["range", "transition"]):
                should_emit = "C"

        if should_emit is None:
            continue

        # Cooldown check
        cd = max(put_gen.get("cooldown_bars", 4), call_gen.get("cooldown_bars", 6))
        if i - last_idx < cd:
            continue

        # Vol check — use the emitting side's threshold
        vol_thresh = put_gen["vol_threshold"] if should_emit == "P" else call_gen["vol_threshold"]
        closes_1h = [c["close"] for c in s1h]
        if len(closes_1h) < 168 + 20:
            continue
        from services.indicators import realized_vol
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

        # Bull filter for Put side
        if should_emit == "P":
            bull_max = put_gen.get("bull_market_ratio_max")
            if bull_max is not None and len(closes_1h) >= 200:
                ema50 = ema(closes_1h, 50)
                ema200 = ema(closes_1h, 200)
                if ema50 and ema200 and ema200 > 0:
                    if ema50 / ema200 > bull_max:
                        continue

        # Call side: check 7d return filter if specified
        if should_emit == "C":
            ret_7d_filter = call_gen.get("ret_7d_filter")
            if ret_7d_filter is not None and len(k5) > 2016:
                ret_7d = (c5["close"] - k5[max(0, i - 2016)]["close"]) / k5[max(0, i - 2016)]["close"] * 100
                if ret_7d > ret_7d_filter:  # uptrend — don't sell Call
                    continue

        # Emit signal
        out.append({
            "idx_5m": i,
            "ts_ms": ts_end,
            "close": c5["close"],
            "side": should_emit,
            "signal_type": f"hybrid_{should_emit}",
            "regime": regime_name,
            "mtf_direction": direction,
            "mtf_aligned": aligned,
            "position": "short_premium",
        })
        last_idx = i

    return out


def _sim_stats(sims, label=""):
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return None
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    st = statistics.stdev(pnls) if len(pnls) > 1 else 0
    sh = (statistics.mean(pnls) / st) if st > 0 else 0

    # Per-side breakdown
    by_side = {}
    for s in sims:
        side = s.get("side", "?")
        pnl = s.get("option", {}).get("pnl_pct")
        if pnl is not None:
            by_side.setdefault(side, []).append(pnl)

    # Monthly breakdown
    monthly = {}
    for s in sims:
        ts = datetime.fromtimestamp(s["ts_ms"] / 1000, tz=timezone.utc)
        m = ts.strftime("%Y-%m")
        pnl = s.get("option", {}).get("pnl_pct")
        if pnl is not None:
            monthly.setdefault(m, []).append(pnl)

    losing_months = sum(1 for ps in monthly.values() if statistics.mean(ps) < 0)
    mc = cl = 0
    for p in pnls:
        cl = cl + 1 if p < 0 else 0
        mc = max(mc, cl)

    side_stats = {}
    for side, side_pnls in by_side.items():
        s_wr = sum(1 for p in side_pnls if p > 0) / len(side_pnls)
        s_avg = statistics.mean(side_pnls)
        side_stats[side] = {"n": len(side_pnls), "wr": round(s_wr, 3), "avg": round(s_avg, 2)}

    return {
        "n": len(pnls), "wr": round(wr, 3),
        "avg": round(statistics.mean(pnls), 2),
        "median": round(statistics.median(pnls), 2),
        "stdev": round(st, 2),
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
    print(f"=== Switching Hybrid Backtest ===", flush=True)
    print(f"  data: {data_dir}", flush=True)

    k5, k15, k1h = load_local(data_dir)
    print(f"  klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}", flush=True)

    # Configs for each side
    put_gen = {
        "vol_threshold": 0.50,
        "regime_filter": ["range"],
        "side": "P",
        "adx_max": None,
        "mtf_direction_filter": "up",
        "bull_market_ratio_max": None,
        "cooldown_bars": 4,
    }
    put_exit = {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 96}

    call_gen = {
        "vol_threshold": 0.60,
        "regime_filter": ["range", "transition"],
        "side": "C",
        "adx_max": None,
        "mtf_direction_filter": "down",
        "bull_market_ratio_max": 1.05,
        "cooldown_bars": 6,
    }
    call_exit = {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24}

    # ── 1. Pure Put baseline ──
    print("\n[1] Pure Put baseline...", flush=True)
    put_sigs = gen_sell_premium_iv_high(k5, k15, k1h, **put_gen)
    put_sims = simulate_signal_set(
        put_sigs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=put_exit["tp1"], tp2_pct=put_exit["tp2"], sl_pct=put_exit["sl"],
        option_horizon_h=put_exit["hold_h"], spread_pct=2.0,
    )
    put_stats = _sim_stats(put_sims, "Put")
    print(f"  Put: n={put_stats['n']} WR={put_stats['wr']*100:.1f}% "
          f"avg={put_stats['avg']:+.2f}% sh={put_stats['sharpe']:+.3f} "
          f"cl={put_stats['max_consec_loss']} lm={put_stats['losing_months']}")

    # ── 2. Pure Call baseline ──
    print("\n[2] Pure Call baseline...", flush=True)
    call_sigs = gen_sell_premium_iv_high(k5, k15, k1h, **call_gen)
    call_sims = simulate_signal_set(
        call_sigs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=call_exit["tp1"], tp2_pct=call_exit["tp2"], sl_pct=call_exit["sl"],
        option_horizon_h=call_exit["hold_h"], spread_pct=2.0,
    )
    call_stats = _sim_stats(call_sims, "Call")
    print(f"  Call: n={call_stats['n']} WR={call_stats['wr']*100:.1f}% "
          f"avg={call_stats['avg']:+.2f}% sh={call_stats['sharpe']:+.3f} "
          f"cl={call_stats['max_consec_loss']} lm={call_stats['losing_months']}")

    # ── 3. Hybrid (MTF switching) ──
    print("\n[3] Hybrid (MTF switching)...", flush=True)
    hybrid_sigs = generate_hybrid_signals(k5, k15, k1h, put_gen=put_gen, call_gen=call_gen)
    print(f"  Hybrid signals: {len(hybrid_sigs)}", flush=True)

    # Simulate with per-side exit params
    # We need to run Put and Call signals separately since exits differ
    put_hybrid = [s for s in hybrid_sigs if s["side"] == "P"]
    call_hybrid = [s for s in hybrid_sigs if s["side"] == "C"]
    print(f"  Put signals: {len(put_hybrid)}, Call signals: {len(call_hybrid)}", flush=True)

    put_hybrid_sims = simulate_signal_set(
        put_hybrid, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=put_exit["tp1"], tp2_pct=put_exit["tp2"], sl_pct=put_exit["sl"],
        option_horizon_h=put_exit["hold_h"], spread_pct=2.0,
    ) if put_hybrid else []

    call_hybrid_sims = simulate_signal_set(
        call_hybrid, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=call_exit["tp1"], tp2_pct=call_exit["tp2"], sl_pct=call_exit["sl"],
        option_horizon_h=call_exit["hold_h"], spread_pct=2.0,
    ) if call_hybrid else []

    all_hybrid_sims = put_hybrid_sims + call_hybrid_sims
    hybrid_stats = _sim_stats(all_hybrid_sims, "Hybrid")
    print(f"  Hybrid: n={hybrid_stats['n']} WR={hybrid_stats['wr']*100:.1f}% "
          f"avg={hybrid_stats['avg']:+.2f}% sh={hybrid_stats['sharpe']:+.3f} "
          f"cl={hybrid_stats['max_consec_loss']} lm={hybrid_stats['losing_months']}")
    if hybrid_stats.get("by_side"):
        for side, ss in hybrid_stats["by_side"].items():
            print(f"    {side}: n={ss['n']} WR={ss['wr']*100:.1f}% avg={ss['avg']:+.2f}%")

    # ── Monthly comparison ──
    print(f"\n{'='*90}")
    print(f"MONTHLY COMPARISON: Put vs Call vs Hybrid")
    print(f"{'Month':<10} {'Put_avg':>10} {'Put_WR':>7} {'Call_avg':>10} {'Call_WR':>7} "
          f"{'Hyb_avg':>10} {'Hyb_WR':>7}")
    print("-" * 80)

    all_months = sorted(set(list(put_stats.get("monthly", {}).keys())
                            + list(call_stats.get("monthly", {}).keys())
                            + list(hybrid_stats.get("monthly", {}).keys())))

    for m in all_months:
        pm = put_stats.get("monthly", {}).get(m, {})
        cm = call_stats.get("monthly", {}).get(m, {})
        hm = hybrid_stats.get("monthly", {}).get(m, {})
        print(f"  {m}:  "
              f"Put {pm.get('avg', 0):>+8.2f}% {pm.get('wr', 0)*100:5.1f}%  "
              f"Call {cm.get('avg', 0):>+8.2f}% {cm.get('wr', 0)*100:5.1f}%  "
              f"Hyb {hm.get('avg', 0):>+8.2f}% {hm.get('wr', 0)*100:5.1f}%")

    # ── Summary ──
    print(f"\n{'='*90}")
    print(f"SUMMARY (365d, σ=0.6, spread=2%)")
    print(f"{'='*90}")
    for name, st in [("Pure Put", put_stats), ("Pure Call", call_stats), ("Hybrid", hybrid_stats)]:
        print(f"\n{name}:")
        print(f"  n={st['n']:>4}  WR={st['wr']*100:5.1f}%  avg={st['avg']:+7.2f}%  "
              f"sh={st['sharpe']:+.3f}  total={st['total']:+.1f}%")
        print(f"  max_consec_loss={st['max_consec_loss']}  "
              f"losing_months={st['losing_months']}/{st['total_months']}")

    # ── Save ──
    repo = Path(__file__).resolve().parents[2]
    out_path = repo / "sweep_results" / "hybrid_backtest_365d.json"
    payload = {
        "put": {"gen": put_gen, "exit": put_exit, "stats": put_stats},
        "call": {"gen": call_gen, "exit": call_exit, "stats": call_stats},
        "hybrid": {"put_gen": put_gen, "call_gen": call_gen,
                   "put_exit": put_exit, "call_exit": call_exit,
                   "stats": hybrid_stats},
        "elapsed_s": round(time.time() - t0, 1),
    }
    out_path.write_text(json.dumps(payload, indent=2))
    elapsed = round(time.time() - t0, 1)
    print(f"\nSaved → {out_path} ({elapsed}s)", flush=True)


if __name__ == "__main__":
    main()

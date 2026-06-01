"""Solution v3: 7d return guided switching + circuit breaker.

Key insight from loss_analysis.py:
  All major consec loss clusters happen when 7d return > +3% (ETH rallying).
  In these periods, selling Put is lethal — premium INCREASES (spot goes up).

Solution:
  1. When |7d_ret| < 2% → sell Put (normal range regime)
  2. When 7d_ret > +2% → sell Call instead (uptrend — Put is dangerous)
  3. When 7d_ret < -2% → sell Put (downtrend — Put profits from decay)
  4. Circuit breaker: after 3 consec losses → 48h pause
  5. Wider SL for Call (sl=100%) since it has different risk profile

Test various thresholds and measure max consec loss + total PnL.

Run:
    cd backend && PYTHONPATH=. python3 services/solution_v3.py
"""
from __future__ import annotations

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


def generate_solution_signals(k5, k15, k1h, *,
                              put_gen: dict, call_gen: dict,
                              ret_threshold: float = 2.0,
                              consec_loss_cb: int = 3,
                              cb_pause_bars: int = 576,  # 48h = 576 5m bars
                              history_window: int = 240) -> list[dict]:
    """7d return guided switching with circuit breaker."""
    out: list[dict] = []
    last_idx = -10_000
    i15 = 0
    i1h = 0
    BARS_7D = 2016
    consec_losses = 0
    cb_until = -1

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

        # Circuit breaker check
        if i < cb_until:
            continue

        # 7d return
        prev_close = k5[i - BARS_7D]["close"]
        ret_7d = (c5["close"] - prev_close) / prev_close * 100

        # Decide side based on 7d return
        if abs(ret_7d) < ret_threshold:
            # Range market → sell Put
            allowed_sides = ["P"]
        elif ret_7d > 0:
            # Uptrend → sell Call (Put is dangerous)
            allowed_sides = ["C"]
        else:
            # Downtrend → sell Put (Call is dangerous)
            allowed_sides = ["P"]

        regime = detect_regime(s1h)
        regime_name = regime.get("regime", "unknown")

        # Skip trending regime
        if regime_name == "trend":
            continue

        mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
        direction = mtf["direction"]
        aligned = mtf["tfs_aligned"]

        # Try each allowed side
        emitted_side = None
        for side in allowed_sides:
            gen = put_gen if side == "P" else call_gen

            # Vol check
            vol_thresh = gen["vol_threshold"]
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

            # Regime check
            if regime_name not in gen.get("regime_filter", ["range"]):
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
            break

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
            "signal_type": f"sol_v3_ret{ret_threshold}",
            "regime": regime_name,
            "mtf_direction": direction,
            "mtf_aligned": aligned,
            "ret_7d": round(ret_7d, 2),
            "position": "short_premium",
        })
        last_idx = i

        # Track for circuit breaker (done post-simulation)

    return out


def apply_circuit_breaker(sims, consec_limit: int = 3, pause_bars: int = 576):
    """Post-hoc circuit breaker: remove trades that would have been skipped
    after N consecutive losses + pause period."""
    sorted_sims = sorted(sims, key=lambda s: s["idx_5m"])
    result = []
    consec = 0
    skip_until_idx = -1

    for s in sorted_sims:
        idx = s["idx_5m"]
        pnl = s.get("option", {}).get("pnl_pct", 0)

        if idx < skip_until_idx:
            continue  # CB active — skip this trade

        result.append(s)

        if pnl < 0:
            consec += 1
            if consec >= consec_limit:
                skip_until_idx = idx + pause_bars
                consec = 0
        else:
            consec = 0

    return result


def _sim_stats(sims, label=""):
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
    print(f"=== Solution v3: 7d switching + CB ===", flush=True)

    k5, k15, k1h = load_local(data_dir)
    print(f"klines: 5m={len(k5):,}", flush=True)

    put_gen = {
        "vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
        "adx_max": None, "mtf_direction_filter": "up",
        "bull_market_ratio_max": None, "cooldown_bars": 4,
    }

    call_gen = {
        "vol_threshold": 0.60, "regime_filter": ["range", "transition"], "side": "C",
        "adx_max": None, "mtf_direction_filter": "down",
        "bull_market_ratio_max": 1.05, "cooldown_bars": 6,
    }

    put_exit = {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 96}
    call_exit = {"tp1": 0.30, "tp2": 0.50, "sl": 1.00, "hold_h": 24}

    # Test various 7d thresholds
    thresholds = [1.0, 1.5, 2.0, 2.5, 3.0]
    cb_limits = [3, 5, 7]

    results = {}

    # Baseline: Pure Put
    print("\n[BASELINE] Pure Put...", flush=True)
    put_sigs = gen_sell_premium_iv_high(k5, k15, k1h, **put_gen)
    put_sims = simulate_signal_set(put_sigs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=put_exit["tp1"], tp2_pct=put_exit["tp2"], sl_pct=put_exit["sl"],
        option_horizon_h=put_exit["hold_h"], spread_pct=2.0)
    st = _sim_stats(put_sims)
    results["Pure Put"] = st
    print(f"  n={st['n']} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
          f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']}", flush=True)

    # Pure Call
    print("\n[BASELINE] Pure Call...", flush=True)
    call_sigs = gen_sell_premium_iv_high(k5, k15, k1h, **call_gen)
    call_sims = simulate_signal_set(call_sigs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=call_exit["tp1"], tp2_pct=call_exit["tp2"], sl_pct=call_exit["sl"],
        option_horizon_h=call_exit["hold_h"], spread_pct=2.0)
    st = _sim_stats(call_sims)
    results["Pure Call"] = st
    print(f"  n={st['n']} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
          f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']}", flush=True)

    # Solution v3: 7d switching + CB
    for thr in thresholds:
        for cb_lim in cb_limits:
            name = f"V3_thr{thr}_cb{cb_lim}"
            print(f"\n[{name}] Generating...", flush=True)

            sigs = generate_solution_signals(k5, k15, k1h,
                put_gen=put_gen, call_gen=call_gen,
                ret_threshold=thr)

            ps = [s for s in sigs if s["side"] == "P"]
            cs = [s for s in sigs if s["side"] == "C"]

            # Simulate with per-side exits
            psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
                tp1_pct=put_exit["tp1"], tp2_pct=put_exit["tp2"], sl_pct=put_exit["sl"],
                option_horizon_h=put_exit["hold_h"], spread_pct=2.0) if ps else []

            csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
                tp1_pct=call_exit["tp1"], tp2_pct=call_exit["tp2"], sl_pct=call_exit["sl"],
                option_horizon_h=call_exit["hold_h"], spread_pct=2.0) if cs else []

            all_sims = psim + csim

            # Apply circuit breaker
            cb_sims = apply_circuit_breaker(all_sims, consec_limit=cb_lim, pause_bars=576)

            st = _sim_stats(cb_sims)
            results[name] = st

            print(f"  Raw={len(all_sims)} After CB={st['n']} "
                  f"WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
                  f"sh={st['sharpe']:+.3f} cl={st['max_consec_loss']} "
                  f"lm={st['losing_months']}", flush=True)
            if st.get("by_side"):
                for side, ss in st["by_side"].items():
                    print(f"    {side}: n={ss['n']} avg={ss['avg']:+.2f}%", flush=True)

    # Summary table
    print(f"\n{'='*100}")
    print(f"{'Config':<30} {'n':>5} {'WR':>6} {'avg':>8} {'sharpe':>7} "
          f"{'total':>10} {'cl':>4} {'lm':>4}")
    print("-" * 100)
    for name in sorted(results.keys()):
        st = results[name]
        print(f"{name:<30} {st['n']:>5} {st['wr']*100:>5.1f}% {st['avg']:>+7.2f}% "
              f"{st['sharpe']:>+6.3f} {st['total']:>+9.1f}% {st['max_consec_loss']:>4} {st['losing_months']:>4}")

    # Find best by score: avg * sharpe - consec_loss penalty
    def score(name):
        st = results[name]
        return st["avg"] * st["sharpe"] - st["max_consec_loss"] * 2 - st["losing_months"] * 5

    best = max(results.keys(), key=score)
    print(f"\nBest by composite score: {best}")
    print(f"  Score: {score(best):.1f}")

    # Save
    repo = Path(__file__).resolve().parents[2]
    out_path = repo / "sweep_results" / "solution_v3_switching_cb.json"
    payload = {
        "results": {k: v for k, v in results.items()},
        "best_config": best,
        "elapsed_s": round(time.time() - t0, 1),
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nSaved → {out_path} ({round(time.time() - t0, 1)}s)", flush=True)


if __name__ == "__main__":
    main()

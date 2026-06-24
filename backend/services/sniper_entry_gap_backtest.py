"""Sniper1 entry-gap backtest — quantifies "gauge shows ready, generator says
no signal" (finding_sniper1_entry_gap_suspected).

Mirrors variant_backtest.py's baseline generate() (the live-deployed logic)
bar-for-bar, but instead of silently `continue`-ing past a cooldown-blocked
bar, tags it as a "ready but suppressed by cooldown" event. The gauge
(paper_strategy.evaluate_conditions/entry_proximity) has no concept of
cooldown at all — only vol/regime/MTF/bull gates — so this is the prime
suspect for the gap the user observed live, NOT a bug in either function
individually.

Consecutive bars where conditions stay "ready" through a single cooldown
window are ONE missed opportunity, not several — deduped the same way the
live cooldown itself dedupes real fires (only count the first ready bar
per cooldown window per side), or every near-identical 5-min-apart entry
would be backtested as if it were independent.

Train/holdout split by time (70/30) — no threshold is tuned here (nothing
to leak), but split first regardless, same discipline as the Grogu fix.

Run: cd backend && PYTHONPATH=. python3 services/sniper_entry_gap_backtest.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.indicators import ema, realized_vol
from services.local_optimizer import find_data_dir, load_local
from services.momentum_mtf import analyze_tf, consensus
from services.strategy_config import PUT_GEN_KWARGS, CALL_GEN_KWARGS, RET_7D_THRESHOLD
from services.variant_backtest import regime_of, stats as variant_stats, sim_set

BARS_7D = 2016
TRAIN_FRAC = 0.70


def generate_with_gap_tracking(k5, k15, k1h, *, history_window: int = 240):
    """Returns (fired, gap_events) — fired matches variant_backtest's
    baseline exactly; gap_events are deduped "ready but cooldown-suppressed"
    bars (one per cooldown window, not one per 5m tick)."""
    put_gen, call_gen = PUT_GEN_KWARGS, CALL_GEN_KWARGS
    cd = max(put_gen.get("cooldown_bars", 6), call_gen.get("cooldown_bars", 6))

    fired: list[dict] = []
    gap_events: list[dict] = []
    last_fired_idx = -10_000
    last_gap_idx = -10_000
    i15 = i1h = 0

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

        prev_close = k5[i - BARS_7D]["close"]
        ret_7d = (c5["close"] - prev_close) / prev_close * 100
        if ret_7d > RET_7D_THRESHOLD:
            allowed = ["P"]
        elif ret_7d < -RET_7D_THRESHOLD:
            allowed = ["C"]
        else:
            allowed = ["P", "C"]

        regime_name, _ = regime_of(s1h)
        if regime_name == "trend":
            continue

        mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
        direction, aligned = mtf["direction"], mtf["tfs_aligned"]

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

        # Same gate sequence as variant_backtest.generate(variant="baseline"),
        # minus the cooldown check — that's evaluated separately below so we
        # can distinguish "gates failed" from "gates passed, cooldown blocked".
        passed_side = None
        for side in allowed:
            gen = put_gen if side == "P" else call_gen
            thr = sorted_vols[int(len(sorted_vols) * gen["vol_threshold"])]
            if current_vol < thr:
                continue
            rf = gen.get("regime_filter") or (["range"] if side == "P" else ["range", "transition"])
            if regime_name not in rf:
                continue
            mtf_dir = gen.get("mtf_direction_filter")
            if mtf_dir == "up" and (direction != "up" or aligned < 2):
                continue
            if mtf_dir == "down" and (direction != "down" or aligned < 2):
                continue
            if side == "P":
                bull_max = gen.get("bull_market_ratio_max")
                if bull_max is not None and len(closes_1h) >= 200:
                    e50, e200 = ema(closes_1h, 50), ema(closes_1h, 200)
                    if e50 and e200 and e200 > 0 and e50 / e200 > bull_max:
                        continue
            passed_side = side
            break

        if passed_side is None:
            continue  # gauge would not show "ready" here either — not part of the gap

        event = {"idx_5m": i, "ts_ms": ts_end, "close": c5["close"], "side": passed_side,
                 "regime": regime_name, "ret_7d": round(ret_7d, 2), "position": "short_premium"}

        if i - last_fired_idx >= cd:
            fired.append(event)
            last_fired_idx = i
        elif i - last_gap_idx >= cd:
            # All gates pass, but we're still within cooldown of the last
            # FIRE — exactly the live "ready=100% but no signal" case.
            gap_events.append(event)
            last_gap_idx = i

    return fired, gap_events


def main():
    t0 = time.time()
    k5, k15, k1h = load_local(find_data_dir(None))
    print(f"klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}\n", flush=True)

    fired, gap_events = generate_with_gap_tracking(k5, k15, k1h)
    total_ready = len(fired) + len(gap_events)
    print(f"Bars where ALL entry conditions pass (gauge would read 'ready'): {total_ready}")
    print(f"  Actually fired (cooldown clear):                {len(fired)}")
    print(f"  Suppressed by cooldown ('ready' but no signal): {len(gap_events)} "
          f"({len(gap_events) / total_ready * 100:.1f}% of ready windows)" if total_ready else "")

    ts_all = sorted(e["ts_ms"] for e in fired + gap_events)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])

    def split(events):
        return ([e for e in events if e["ts_ms"] < split_ts],
                [e for e in events if e["ts_ms"] >= split_ts])

    _, fired_hold = split(fired)
    _, gap_hold = split(gap_events)

    fired_sims = sim_set(fired, k5)
    gap_sims = sim_set(gap_events, k5)  # what-if these had been traded
    fired_hold_idx = {e["idx_5m"] for e in fired_hold}
    gap_hold_idx = {e["idx_5m"] for e in gap_hold}

    def report(label, sims):
        st = variant_stats(sims)
        if not st:
            print(f"  {label}: no trades")
            return
        print(f"  {label}: n={st['n']} WR={st['wr']*100:.1f}% avg={st['avg']:+.2f}% "
              f"sharpe={st['sharpe']:+.2f} total={st['total']:+.1f}%")

    print("\n=== Full period ===")
    report("Actually-fired (current live behavior)", fired_sims)
    report("Cooldown-suppressed (what-if traded)", gap_sims)

    print("\n=== HOLDOUT ONLY (last 30% by time) — the number that matters ===")
    report("Actually-fired", [s for s in fired_sims if s["idx_5m"] in fired_hold_idx])
    report("Cooldown-suppressed (what-if)", [s for s in gap_sims if s["idx_5m"] in gap_hold_idx])

    print(f"\nelapsed {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

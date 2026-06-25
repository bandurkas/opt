"""Sniper1 persistence/debounce-rule backtest.

Investigates a DIFFERENT mechanism from the already-resolved cooldown gap
(finding_sniper1_entry_gap_cooldown.md): paper_loop.py debounces entries by
requiring evaluate_conditions().ready to hold on EVERY one-minute check
across the 5-minute window (paper_loop.py ~L710-764) — a single flickering
minute disqualifies the WHOLE window, even if 4/5 minutes were ready. User
observed live conditions trigger fairly often but rarely survive 5
straight minutes unbroken.

Reconstructs, minute-by-minute, EXACTLY what the real evaluate_conditions()
(services/paper_strategy.py — same function paper_loop.py calls live) would
have seen, using real 1m OHLCV (data/eth_1m.json, fetched from VPS3 since
direct Bybit calls are blocked on this Mac) to build the "forming" 5m/15m/1h
candle at each whole-minute checkpoint, mirroring paper_loop's per-minute
polling.

For each 5m window: records the 5 per-minute ready-booleans + per-gate
booleans (vol_high/regime_ok/mtf_direction_ok/bull_filter_ok) + side at
candle close. The expensive reconstruction pass (one evaluate_conditions
call per minute, ~559k calls total) is parallelized across CPU cores.
Debounce-rule variants are then derived CHEAPLY from the cached per-window
record (no re-running evaluate_conditions per rule) and backtested with the
same cooldown dedup + train/holdout split discipline as
sniper_entry_gap_backtest.py.

Run: cd backend && PYTHONPATH=. .venv311/bin/python3 services/sniper_persistence_backtest.py
"""
from __future__ import annotations

import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir, load_local
from services.paper_strategy import evaluate_conditions
from services.variant_backtest import sim_set, stats as variant_stats

BARS_7D = 2016
COOLDOWN_BARS = 6  # max(PUT_GEN_KWARGS, CALL_GEN_KWARGS) cooldown_bars, matches live
TRAIN_FRAC = 0.70

_K5: list = []
_K15: list = []
_K1H: list = []
_K1M: list = []


def _init_worker(k5, k15, k1h, k1m):
    global _K5, _K15, _K1H, _K1M
    _K5, _K15, _K1H, _K1M = k5, k15, k1h, k1m


def _agg(bars: list[dict], start_ms: int) -> dict:
    return {
        "start_ms": start_ms,
        "open": bars[0]["open"],
        "high": max(b["high"] for b in bars),
        "low": min(b["low"] for b in bars),
        "close": bars[-1]["close"],
        "volume": sum(b["volume"] for b in bars),
    }


def _window_record(w: int) -> dict | None:
    """Replays the 5 per-minute evaluate_conditions() checks for 5m window w,
    reconstructing forming 5m/15m/1h candles from real 1m bars."""
    k5, k15, k1h, k1m = _K5, _K15, _K1H, _K1M
    if w < BARS_7D or w - 2016 < 0:
        return None

    ts0 = k5[w]["start_ms"]
    base1m = k1m[0]["start_ms"]
    idx0 = (ts0 - base1m) // 60_000
    if idx0 < 0 or idx0 + 4 >= len(k1m):
        return None

    hist5 = k5[w - 2016:w]  # 2016 real closed bars (BARS_7D depth for ret_7d)

    ready, vol_ok, reg_ok, mtf_ok, bull_ok, sides = [], [], [], [], [], []
    for p in range(5):
        minute_ts = ts0 + p * 60_000
        bars5 = k1m[idx0:idx0 + p + 1]
        forming5 = _agg(bars5, ts0)

        floor15 = minute_ts - (minute_ts % 900_000)
        idx15c = (floor15 - k15[0]["start_ms"]) // 900_000
        if idx15c < 240:
            return None
        gidx15_start = (floor15 - base1m) // 60_000
        gidx_now = idx0 + p
        bars15 = k1m[gidx15_start:gidx_now + 1]
        forming15 = _agg(bars15, floor15)
        k15_eval = k15[idx15c - 239:idx15c] + [forming15]

        floor60 = minute_ts - (minute_ts % 3_600_000)
        idx1hc = (floor60 - k1h[0]["start_ms"]) // 3_600_000
        if idx1hc < 240:
            return None
        gidx60_start = (floor60 - base1m) // 60_000
        bars60 = k1m[gidx60_start:gidx_now + 1]
        forming60 = _agg(bars60, floor60)
        k1h_eval = k1h[idx1hc - 239:idx1hc] + [forming60]

        k5_eval = hist5 + [forming5]

        ev = evaluate_conditions(k5_eval, k15_eval, k1h_eval)
        ready.append(bool(ev["ready"]))
        vol_ok.append(bool(ev["vol_high"]))
        reg_ok.append(bool(ev["regime_ok"]))
        mtf_ok.append(bool(ev["mtf_direction_ok"]))
        bull_ok.append(bool(ev["bull_filter_ok"]))
        sides.append(ev["active_side"])

    return {
        "idx_5m": w, "ts_ms": ts0 + 300_000, "close": k5[w]["close"],
        "ready": ready, "vol_ok": vol_ok, "reg_ok": reg_ok,
        "mtf_ok": mtf_ok, "bull_ok": bull_ok, "side": sides[-1],
        "side_stable": len(set(s for s in sides if s)) <= 1,
    }


def _worker_chunk(rng: tuple[int, int]) -> list[dict]:
    lo, hi = rng
    out = []
    for w in range(lo, hi):
        r = _window_record(w)
        if r is not None:
            out.append(r)
    return out


# ───────────── Rule definitions: derive a fired-or-not boolean from a
# cached per-window record (cheap — no evaluate_conditions re-run). ─────────────

def rule_current(r: dict) -> bool:
    """Live-deployed rule: ALL 5 per-minute checks must pass."""
    return all(r["ready"])


def rule_tol1(r: dict) -> bool:
    """At most 1 of 5 minutes allowed to flicker."""
    return sum(r["ready"]) >= 4


def rule_majority(r: dict) -> bool:
    """Majority (3 of 5) minutes ready."""
    return sum(r["ready"]) >= 3


def rule_close_only(r: dict) -> bool:
    """No persistence at all — only the close-tick (last minute) matters."""
    return r["ready"][-1]


def rule_last2(r: dict) -> bool:
    """Last 2 minutes both ready (short persistence, ignores earlier flicker)."""
    return r["ready"][-1] and r["ready"][-2]


def rule_selective(r: dict) -> bool:
    """Stable gates (vol/regime) must hold ALL 5 minutes; flicker-prone gates
    (MTF direction, bull filter) only need majority (3/5). Targets the
    hypothesis that vol/regime carry the real signal while MTF/bull flicker
    on noise near their threshold."""
    return (all(r["vol_ok"]) and all(r["reg_ok"])
            and sum(r["mtf_ok"]) >= 3 and sum(r["bull_ok"]) >= 3)


RULES = {
    "current (5/5, live)": rule_current,
    "tol1 (>=4/5)": rule_tol1,
    "majority (>=3/5)": rule_majority,
    "close_only (no persist)": rule_close_only,
    "last2": rule_last2,
    "selective (stable-all, flicker-maj)": rule_selective,
}


def events_for_rule(records: list[dict], rule) -> list[dict]:
    """Apply rule + cooldown_bars dedup PER SIDE independently — matches the
    real live generator (check_new_signal calls gen_sell_premium_iv_high
    separately per side each tick, each with its own internal last_idx;
    services/strategy_registry.py:198). A shared cross-side counter (as the
    older sniper_entry_gap_backtest.py used) silently drops legitimate
    same-tick fires on the other side and was the dominant bug in an earlier
    version of this harness — it cut real generator counts roughly in half
    and flipped measured PnL negative."""
    out = []
    last_idx = {"P": -10_000, "C": -10_000}
    for r in records:
        side = r["side"]
        if side is None or not rule(r):
            continue
        if r["idx_5m"] - last_idx[side] < COOLDOWN_BARS:
            continue
        last_idx[side] = r["idx_5m"]
        out.append({"idx_5m": r["idx_5m"], "ts_ms": r["ts_ms"],
                     "close": r["close"], "side": side,
                     "position": "short_premium"})
    return out


def report(label: str, events: list[dict], k5: list, holdout_idx: set[int] | None = None):
    sims = sim_set(events, k5)
    st = variant_stats(sims)
    if not st:
        print(f"  {label:<38} n=0")
        return
    line = (f"  {label:<38} n={st['n']:>4} WR={st['wr']*100:>5.1f}% "
            f"avg={st['avg']:>+6.2f}% sharpe={st['sharpe']:>+5.2f} total={st['total']:>+7.1f}%")
    if holdout_idx is not None:
        hold_sims = [s for s in sims if s["idx_5m"] in holdout_idx]
        hst = variant_stats(hold_sims)
        if hst:
            line += (f"   | holdout n={hst['n']:>3} WR={hst['wr']*100:>5.1f}% "
                      f"avg={hst['avg']:>+6.2f}%")
        else:
            line += "   | holdout: no trades"
    print(line)


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_local(data_dir)
    import json
    import pickle
    k1m = json.loads((data_dir / "eth_1m.json").read_text())
    print(f"klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,} 1m={len(k1m):,}\n", flush=True)

    cache_path = data_dir / "sniper_persistence_records_cache.pkl"
    if cache_path.exists():
        records = pickle.loads(cache_path.read_bytes())
        print(f"loaded {len(records):,} cached records from {cache_path}\n", flush=True)
    else:
        windows = list(range(BARS_7D, len(k5)))
        n_workers = mp.cpu_count()
        chunk = max(1, len(windows) // (n_workers * 4))
        chunks = [(windows[i], min(windows[i] + chunk, len(windows) + BARS_7D))
                  for i in range(0, len(windows), chunk)]
        print(f"reconstructing {len(windows):,} windows x 5 min-checks "
              f"across {n_workers} cores ({len(chunks)} chunks)...", flush=True)

        with mp.Pool(n_workers, initializer=_init_worker, initargs=(k5, k15, k1h, k1m)) as pool:
            results = pool.map(_worker_chunk, chunks)
        records = [r for chunk_res in results for r in chunk_res]
        cache_path.write_bytes(pickle.dumps(records))
        print(f"valid windows reconstructed: {len(records):,}  "
              f"(elapsed {time.time()-t0:.0f}s, cached to {cache_path})\n", flush=True)

    n_ready_any = sum(1 for r in records if any(r["ready"]))
    n_5of5 = sum(1 for r in records if sum(r["ready"]) == 5)
    n_4of5 = sum(1 for r in records if sum(r["ready"]) == 4)
    n_3of5 = sum(1 for r in records if sum(r["ready"]) == 3)
    print(f"Windows with >=1 ready minute: {n_ready_any:,}")
    print(f"  5/5 (current rule fires):  {n_5of5:,}")
    print(f"  4/5 (single-flicker-killed): {n_4of5:,}")
    print(f"  3/5: {n_3of5:,}\n")

    # Per-gate flicker diagnostic among 4/5 windows (which gate caused the
    # single failure most often?).
    flick_vol = flick_reg = flick_mtf = flick_bull = 0
    for r in records:
        if sum(r["ready"]) != 4:
            continue
        if not all(r["vol_ok"]):
            flick_vol += 1
        if not all(r["reg_ok"]):
            flick_reg += 1
        if not all(r["mtf_ok"]):
            flick_mtf += 1
        if not all(r["bull_ok"]):
            flick_bull += 1
    print(f"Among 4/5 windows, which gate flickered (non-exclusive):")
    print(f"  vol_high:   {flick_vol:,}\n  regime_ok:  {flick_reg:,}\n"
          f"  mtf_ok:     {flick_mtf:,}\n  bull_ok:    {flick_bull:,}\n")

    ts_all = sorted(r["ts_ms"] for r in records)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    holdout_idx = {r["idx_5m"] for r in records if r["ts_ms"] >= split_ts}
    print(f"Holdout: last {100*(1-TRAIN_FRAC):.0f}% by time, {len(holdout_idx):,} windows\n")

    print("=" * 110)
    print("Rule comparison (cooldown-deduped, same as live):")
    print("=" * 110)
    for label, rule in RULES.items():
        events = events_for_rule(records, rule)
        report(label, events, k5, holdout_idx)

    # What-if: windows the current rule REJECTS but tol1 RESCUES (the
    # "single flicker killed an otherwise-good setup" population).
    print("\n" + "=" * 110)
    print("Diagnostic: windows rescued by tol1 that current(5/5) rejects")
    print("=" * 110)
    rescued = [r for r in records if rule_tol1(r) and not rule_current(r)]
    rescued_events = events_for_rule(rescued, lambda r: True)
    report("tol1-only rescued (not cooldown-deduped vs current)", rescued_events, k5, holdout_idx)

    print(f"\nelapsed {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

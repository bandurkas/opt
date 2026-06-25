"""Sniper1 MTF-alignment threshold backtest.

User observed live: gauge shows MTF=67% (2/3 timeframes aligned) and the
window gets disqualified — close to firing but not quite. Question: does
loosening the MTF gate itself (currently requires tfs_aligned>=2 of 3,
hardcoded in momentum_mtf.consensus(), checked in paper_strategy
.evaluate_conditions() at L238-243 for active_side selection and L277-280
for mtf_direction_ok) let through MORE trades without hurting quality?

This is a DIFFERENT lever from FLICKER_TOLERANCE (already deployed, see
project_sniper1_persistence_tolerance.md) — that loosens how many of the 5
per-minute checks must agree on a fixed gate. This loosens the gate
definition itself (how many of 3 timeframes must align).

Reuses the same reconstruction approach as sniper_persistence_backtest.py
(real 1m OHLCV → forming 5m/15m/1h candles at each minute), but additionally
captures the raw per-TF directions (not just the current mtf_ok boolean) so
alternate alignment thresholds can be derived CHEAPLY from one cached pass
— no need to re-run the expensive reconstruction per variant.

Run: cd backend && PYTHONPATH=. .venv311/bin/python3 services/sniper_mtf_loosen_backtest.py
"""
from __future__ import annotations

import multiprocessing as mp
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir, load_local
from services.momentum_mtf import analyze_tf
from services.paper_strategy import (
    BARS_7D, compute_ret_7d, evaluate_conditions,
)
from services.strategy_config import RET_7D_THRESHOLD, PUT_GEN_KWARGS, CALL_GEN_KWARGS
from services.variant_backtest import sim_set, stats as variant_stats

COOLDOWN_BARS = 6
TRAIN_FRAC = 0.70
HIST = 240

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


def _allowed_sides(ret_7d: float) -> list[str]:
    if ret_7d > RET_7D_THRESHOLD:
        return ["P"]
    elif ret_7d < -RET_7D_THRESHOLD:
        return ["C"]
    return ["P", "C"]


def _window_record(w: int) -> dict | None:
    """Replays the 5 per-minute checks for 5m window w, capturing BOTH the
    real evaluate_conditions() outcome (vol/regime/bull, used unchanged) AND
    the raw per-TF directions (5m/15m/1h: up/down/neutral) so alternate MTF
    alignment thresholds can be re-derived without re-running the full
    reconstruction."""
    k5, k15, k1h, k1m = _K5, _K15, _K1H, _K1M
    if w < BARS_7D or w - 2016 < 0:
        return None

    ts0 = k5[w]["start_ms"]
    base1m = k1m[0]["start_ms"]
    idx0 = (ts0 - base1m) // 60_000
    if idx0 < 0 or idx0 + 4 >= len(k1m):
        return None

    hist5 = k5[w - 2016:w]
    ret_7d = compute_ret_7d(k5, w)
    sides_allowed = _allowed_sides(ret_7d)

    ready, vol_ok, reg_ok, bull_ok = [], [], [], []
    dir5, dir15, dir1h = [], [], []

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
        bull_ok.append(bool(ev["bull_filter_ok"]))

        s5 = k5_eval[-HIST:] if len(k5_eval) > HIST else k5_eval
        s15 = k15_eval[-HIST:] if len(k15_eval) > HIST else k15_eval
        s1h = k1h_eval[-HIST:] if len(k1h_eval) > HIST else k1h_eval
        dir5.append(analyze_tf(s5)["direction"])
        dir15.append(analyze_tf(s15)["direction"])
        dir1h.append(analyze_tf(s1h)["direction"])

    return {
        "idx_5m": w, "ts_ms": ts0 + 300_000, "close": k5[w]["close"],
        "ret_7d": ret_7d, "sides_allowed": sides_allowed,
        "ready": ready, "vol_ok": vol_ok, "reg_ok": reg_ok, "bull_ok": bull_ok,
        "dir5": dir5, "dir15": dir15, "dir1h": dir1h,
    }


def _worker_chunk(rng: tuple[int, int]) -> list[dict]:
    lo, hi = rng
    out = []
    for w in range(lo, hi):
        r = _window_record(w)
        if r is not None:
            out.append(r)
    return out


# ───────────── MTF gate variants: derive (mtf_ok, active_side) per minute
# from the raw per-TF directions, for a given side, under a custom rule. ─────

def _mtf_current(d5: str, d15: str, d1h: str, need: str) -> bool:
    """Live rule: >=2 of 3 timeframes must equal `need` (and that must be the
    consensus direction, i.e. need also beats the opposite count — equivalent
    here since >=2/3 already guarantees a majority)."""
    dirs = [d5, d15, d1h]
    return sum(1 for d in dirs if d == need) >= 2


def _mtf_tol1(d5: str, d15: str, d1h: str, need: str) -> bool:
    """Loosened: >=1 of 3 timeframes aligned with `need`, AND no timeframe
    actively opposes (i.e. the other two are 'neutral' or also `need`, never
    the opposite direction). Rejects only on active disagreement, not on
    silence."""
    dirs = [d5, d15, d1h]
    opp = "down" if need == "up" else "up"
    return any(d == need for d in dirs) and not any(d == opp for d in dirs)


def _mtf_1h_anchor(d5: str, d15: str, d1h: str, need: str) -> bool:
    """1h alone decides, ignoring 5m/15m noise entirely."""
    return d1h == need


def _mtf_any1_strict(d5: str, d15: str, d1h: str, need: str) -> bool:
    """Loosest: >=1 of 3 aligned, regardless of opposition elsewhere."""
    dirs = [d5, d15, d1h]
    return any(d == need for d in dirs)


MTF_VARIANTS = {
    "current (>=2/3, live)": _mtf_current,
    "tol1 (>=1/3, no active opposite)": _mtf_tol1,
    "1h_anchor (1h alone)": _mtf_1h_anchor,
    "any1 (>=1/3, opposition allowed)": _mtf_any1_strict,
}


def _gen_kw_for(side: str) -> dict:
    return PUT_GEN_KWARGS if side == "P" else CALL_GEN_KWARGS


def _need_for(side: str) -> str:
    return _gen_kw_for(side)["mtf_direction_filter"]


def _recompute_ready_for_variant(r: dict, mtf_rule) -> dict[str, list[bool]]:
    """Returns {side: [ready_bool x5]} for each side allowed at this window,
    using the given MTF gate variant but the SAME real vol/regime/bull gates
    already cached."""
    out: dict[str, list[bool]] = {}
    for side in r["sides_allowed"]:
        need = _need_for(side)
        per_minute = []
        for p in range(5):
            mtf_ok = mtf_rule(r["dir5"][p], r["dir15"][p], r["dir1h"][p], need)
            per_minute.append(r["vol_ok"][p] and r["reg_ok"][p] and mtf_ok and r["bull_ok"][p])
        out[side] = per_minute
    return out


def rule_debounce_tol1(readys: list[bool]) -> bool:
    """The currently-deployed debounce rule: at most 1 of 5 minutes flickers."""
    return sum(readys) >= 4


def events_for_variant(records: list[dict], mtf_rule, k5: list) -> list[dict]:
    out = []
    last_idx = {"P": -10_000, "C": -10_000}
    for r in records:
        per_side = _recompute_ready_for_variant(r, mtf_rule)
        for side, readys in per_side.items():
            if not rule_debounce_tol1(readys):
                continue
            if r["idx_5m"] - last_idx[side] < COOLDOWN_BARS:
                continue
            last_idx[side] = r["idx_5m"]
            out.append({"idx_5m": r["idx_5m"], "ts_ms": r["ts_ms"],
                         "close": r["close"], "side": side,
                         "position": "short_premium"})
    out.sort(key=lambda e: e["idx_5m"])
    return out


def report(label: str, events: list[dict], k5: list, holdout_idx: set[int] | None = None):
    sims = sim_set(events, k5)
    st = variant_stats(sims)
    if not st:
        print(f"  {label:<40} n=0")
        return
    line = (f"  {label:<40} n={st['n']:>4} WR={st['wr']*100:>5.1f}% "
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

    cache_path = data_dir / "sniper_mtf_loosen_records_cache.pkl"
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

    ts_all = sorted(r["ts_ms"] for r in records)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    holdout_idx = {r["idx_5m"] for r in records if r["ts_ms"] >= split_ts}
    print(f"Holdout: last {100*(1-TRAIN_FRAC):.0f}% by time, {len(holdout_idx):,} windows\n")

    print("=" * 110)
    print("MTF-gate variant comparison (debounce=tol1 deployed rule, cooldown-deduped):")
    print("=" * 110)
    for label, rule in MTF_VARIANTS.items():
        events = events_for_variant(records, rule, k5)
        report(label, events, k5, holdout_idx)

    print(f"\nelapsed {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

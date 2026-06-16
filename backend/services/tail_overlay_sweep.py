"""Tail-risk overlay research (iteration harness).

GOAL: shrink the bad-month / cluster tail of the live V2-hybrid+V3 strategy WITHOUT
losing the edge — and prove it OUT-OF-SAMPLE (train/holdout), not by in-sample fit.

Method:
  1. Generate the PRODUCTION signal set once (variant_backtest.generate(variant='v3')
     == live: V2 side-switching + ADX trend>35) and BS-simulate each trade. Cache to
     /tmp so overlay sweeps are instant.
  2. Build trade records with entry_ts + exit_ts (entry + bars_held·5m) so we can model
     concurrency and realized-loss timing.
  3. Event-driven portfolio replay (OPEN/CLOSE events in time order, no look-ahead) that
     applies risk overlays as parameters:
        - max_open            : concentration cap (skip if too many positions already open)
        - cb_k / cb_pause_h   : consecutive-loss breaker (pause new opens after K losses)
        - daily_loss_limit_pct: halt new opens for the rest of the UTC day after −X% realized
        - dyn_size            : halve size when last-10 WR < 0.40
  4. Split train (first part) / holdout (last HOLDOUT_DAYS by entry) and report BOTH so we
     only keep overlays that generalise.

Report per config: per-trade avg (edge), WR, worst-month (min monthly mean), losing-months,
max-DD (realized equity), annual return, trades taken/skipped.

Run: cd backend && PYTHONPATH=. python3 services/tail_overlay_sweep.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.adx_score import compute_adx_score
from services.adx_sizing_oos import SIZING_MODELS as SM
from services.local_optimizer import find_data_dir, load_local
from services.variant_backtest import generate, sim_set

CACHE = "/tmp/tail_trades_v3_adx.json"  # includes per-trade adx_score
FIVE_MIN_MS = 5 * 60 * 1000
HOLDOUT_DAYS = 90
START_EQUITY = 1000.0
RISK_FRAC = 0.10          # size each position at 10% of equity-at-open (compounding)


# ───────────────────────── trade source (cached) ─────────────────────────

def build_trades(force: bool = False) -> list[dict]:
    if os.path.exists(CACHE) and not force:
        return json.load(open(CACHE))
    t0 = time.time()
    k5, k15, k1h = load_local(find_data_dir(None))
    print(f"[gen] klines 5m={len(k5):,} — generating v3 (production) signals…", flush=True)
    sigs = generate(k5, k15, k1h, variant="v3")
    sims = sim_set(sigs, k5)
    trades = []
    for s in sims:
        opt = s.get("option", {})
        if "pnl_pct" not in opt:
            continue
        entry = int(s["ts_ms"])
        bars = int(opt.get("bars_held", 1) or 1)
        trades.append({
            "entry_ts": entry,
            "exit_ts": entry + bars * FIVE_MIN_MS,
            "side": s.get("side", "?"),
            "pnl_pct": float(opt["pnl_pct"]),
            "resolution": opt.get("resolution", "?"),
            "month": datetime.fromtimestamp(entry / 1000, tz=timezone.utc).strftime("%Y-%m"),
        })
    trades.sort(key=lambda t: t["entry_ts"])
    # Annotate ADX readiness score at entry (1h history up to entry_ts), so risk
    # overlays can size by it. HIGH score = range-like = best premium decay.
    i1h = 0
    for t in trades:
        while i1h < len(k1h) and k1h[i1h]["start_ms"] + 3_600_000 <= t["entry_ts"]:
            i1h += 1
        s1h = k1h[max(0, i1h - 240):i1h]
        t["adx_score"] = compute_adx_score(s1h).get("score", 0.0) if s1h else 0.0
    json.dump(trades, open(CACHE, "w"))
    print(f"[gen] {len(trades)} trades, {time.time()-t0:.0f}s (cached → {CACHE})", flush=True)
    return trades


# ───────────────────────── event-driven replay ─────────────────────────

def replay(trades: list[dict], *, max_open: int = 999, max_per_side: int = 999,
           cb_k: int = 0, cb_pause_h: int = 48,
           daily_loss_limit_pct: float = 0.0, dyn_size: bool = False,
           size_fn=None) -> dict:
    """Apply overlays via an open/close event stream (no look-ahead). Returns metrics."""
    # event list: (+1 open) then we realize at exit. Process OPEN by entry order; CLOSE on demand.
    events = []
    for ti, t in enumerate(trades):
        events.append((t["entry_ts"], 0, ti))   # 0 = OPEN
        events.append((t["exit_ts"], 1, ti))     # 1 = CLOSE
    events.sort(key=lambda e: (e[0], e[1]))      # at same ts, CLOSE(1) after OPEN(0)? we want CLOSE first to free slots

    # process CLOSE before OPEN at equal ts so freed capacity/cooldown applies
    events.sort(key=lambda e: (e[0], -e[1]))

    equity = START_EQUITY
    peak = equity
    max_dd = 0.0
    open_ids: set[int] = set()
    taken: dict[int, float] = {}        # ti -> size
    cooldown_until = 0
    consec = 0
    recent: list[float] = []
    day_realized: dict[str, float] = {}
    kept_pnls: list[tuple[int, float]] = []  # (entry_ts, pnl_pct) for taken trades

    def day_of(ts: int) -> str:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    for ts, etype, ti in events:
        t = trades[ti]
        if etype == 1:  # CLOSE
            if ti not in taken:
                continue
            size = taken[ti]
            pnl_d = (t["pnl_pct"] / 100.0) * size
            equity += pnl_d
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100 if peak > 0 else 0)
            day_realized[day_of(ts)] = day_realized.get(day_of(ts), 0.0) + pnl_d
            recent.append(t["pnl_pct"])
            if t["pnl_pct"] <= 0:
                consec += 1
                if cb_k and consec >= cb_k:
                    cooldown_until = ts + cb_pause_h * 3600 * 1000
                    consec = 0
            else:
                consec = 0
            open_ids.discard(ti)
        else:  # OPEN — decide whether to take it
            if len(open_ids) >= max_open:
                continue
            if max_per_side < 999:
                side_open = sum(1 for oi in open_ids if trades[oi]["side"] == t["side"])
                if side_open >= max_per_side:
                    continue
            if cb_k and ts < cooldown_until:
                continue
            if daily_loss_limit_pct > 0:
                lost_today = -day_realized.get(day_of(ts), 0.0)
                if lost_today >= daily_loss_limit_pct / 100.0 * equity:
                    continue
            size = RISK_FRAC * equity
            if size_fn is not None:
                size *= size_fn(t.get("adx_score", 0.0))
            if dyn_size and len(recent) >= 10:
                wr10 = sum(1 for p in recent[-10:] if p > 0) / 10.0
                if wr10 < 0.40:
                    size *= 0.5
            taken[ti] = size
            open_ids.add(ti)
            kept_pnls.append((t["entry_ts"], t["pnl_pct"]))

    return _metrics(kept_pnls, equity, max_dd, len(trades))


def _metrics(kept: list[tuple[int, float]], equity: float, max_dd: float, n_total: int) -> dict:
    if not kept:
        return {"n": 0, "skipped": n_total, "avg": 0, "wr": 0, "worst_month": 0,
                "losing_months": 0, "max_dd": round(max_dd, 1), "return_pct": 0}
    pnls = [p for _, p in kept]
    monthly: dict[str, list[float]] = {}
    for ts, p in kept:
        m = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m")
        monthly.setdefault(m, []).append(p)
    month_means = {m: statistics.mean(v) for m, v in monthly.items()}
    return {
        "n": len(pnls),
        "skipped": n_total - len(pnls),
        "avg": round(statistics.mean(pnls), 2),
        "wr": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
        "worst_month": round(min(month_means.values()), 1),
        "losing_months": sum(1 for v in month_means.values() if v < 0),
        "total_months": len(month_means),
        "max_dd": round(max_dd, 1),
        "return_pct": round((equity / START_EQUITY - 1) * 100, 1),
    }


# ───────────────────────── train / holdout split ─────────────────────────

def split(trades: list[dict]) -> tuple[list[dict], list[dict], int]:
    last = max(t["entry_ts"] for t in trades)
    cutoff = last - HOLDOUT_DAYS * 86400 * 1000
    train = [t for t in trades if t["entry_ts"] < cutoff]
    hold = [t for t in trades if t["entry_ts"] >= cutoff]
    return train, hold, cutoff


# ───────────────────────── sweep ─────────────────────────

CONFIGS = [
    # iter-2 winners as anchors
    ("mo=4",                  dict(max_open=4)),
    ("mo=3",                  dict(max_open=3)),
    ("mo=4 + dyn_size",       dict(max_open=4, dyn_size=True)),
    # iter-3: per-SIDE cap (the observed cluster was same-side). Test alone + with mo.
    ("per_side=2",            dict(max_per_side=2)),
    ("per_side=3",            dict(max_per_side=3)),
    ("mo=6 + per_side=2",     dict(max_open=6, max_per_side=2)),
    ("mo=6 + per_side=3",     dict(max_open=6, max_per_side=3)),
    ("mo=4 + per_side=2",     dict(max_open=4, max_per_side=2)),
    ("mo=4 + per_side=2 +dyn", dict(max_open=4, max_per_side=2, dyn_size=True)),
    ("mo=4 + per_side=3 +dyn", dict(max_open=4, max_per_side=3, dyn_size=True)),
]


# ADX-score sizing under the chosen concentration cap (mo=4). avg/wr/worst_month
# are sizing-INVARIANT here (same trades selected); the sizing signal shows in
# return_pct (edge) and max_dd (sized tail) — read those two columns.
ADX_CONFIGS = [
    ("mo=4 flat (control)",   dict(max_open=4)),
    ("mo=4 + 2-tier",         dict(max_open=4, size_fn=SM["2-tier (FW§8) <=7:1.0 >7:1.5"])),
    ("mo=4 + 4-step A",       dict(max_open=4, size_fn=SM["4-step A 0.5/1.0/1.25/1.5"])),
    ("mo=4 + 4-step B",       dict(max_open=4, size_fn=SM["4-step B 0.75/1.0/1.25/1.5"])),
    ("mo=4 + 4-step aggr",    dict(max_open=4, size_fn=SM["4-step aggressive 0.5..2.0"])),
    ("mo=4 + continuous",     dict(max_open=4, size_fn=SM["continuous clamp(0.5+0.1*s)"])),
    ("mo=4 + 4-step B + dyn", dict(max_open=4, size_fn=SM["4-step B 0.75/1.0/1.25/1.5"], dyn_size=True)),
]


def _row(name: str, m: dict) -> str:
    return (f"{name:<22} n={m['n']:>4} avg={m['avg']:>+6.2f}% wr={m['wr']:>4.1f}% "
            f"worstM={m['worst_month']:>+7.1f} losM={m['losing_months']}/{m.get('total_months',0)} "
            f"maxDD={m['max_dd']:>5.1f}% ret={m['return_pct']:>+8.1f}%")


def main() -> int:
    trades = build_trades(force="--force" in sys.argv)
    train, hold, cutoff = split(trades)
    cut_iso = datetime.fromtimestamp(cutoff / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"\ntrades={len(trades)}  train={len(train)} (pre {cut_iso})  holdout={len(hold)} (last {HOLDOUT_DAYS}d)\n")

    print(f"{'='*120}\nTRAIN (in-sample)\n{'-'*120}")
    base_tr = None
    for name, kw in CONFIGS:
        m = replay(train, **kw)
        if name.startswith("baseline"):
            base_tr = m
        print(_row(name, m))

    print(f"\n{'='*120}\nHOLDOUT (out-of-sample, last {HOLDOUT_DAYS}d)\n{'-'*120}")
    for name, kw in CONFIGS:
        m = replay(hold, **kw)
        print(_row(name, m))

    print(f"\n{'='*120}\nFULL 365d (reference)\n{'-'*120}")
    for name, kw in CONFIGS:
        m = replay(trades, **kw)
        print(_row(name, m))

    # ── ADX-score sizing × mo=4 cap: net edge (return_pct) vs net tail (max_dd) ──
    for label, dataset in (("TRAIN", train), ("HOLDOUT", hold), ("FULL", trades)):
        print(f"\n{'='*120}\nADX SIZING × mo=4 — {label}  (read return_pct=edge, max_dd=sized tail)\n{'-'*120}")
        for name, kw in ADX_CONFIGS:
            print(_row(name, replay(dataset, **kw)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

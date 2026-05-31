"""Hold-out validation: last N days never used in sweep ranking."""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir, get_signals, load_local
from services.strategy_config import (
    BASELINE_CALL_EXIT,
    BASELINE_CALL_GEN_KWARGS,
    LIVE_EXIT,
    LIVE_GEN_KWARGS,
    LIVE_GEN_KWARGS_ALT,
)


def _exit_sim(ex: dict) -> dict:
    if "tp1_pct" in ex:
        return {"tp1": ex["tp1_pct"], "tp2": ex["tp2_pct"], "sl": ex["sl_pct"], "hold_h": ex["hold_h"]}
    return ex


def eval_holdout(k5, k15, k1h, gen: dict, ex: dict, holdout_days: int = 90,
                 sigma: float = 0.6, spread: float = 2.0) -> dict:
    exs = _exit_sim(ex)
    sigs = get_signals(k5, k15, k1h, gen)
    if not k5:
        return {"n": 0}
    cutoff = k5[-1]["start_ms"] - holdout_days * 86_400_000
    ho = [s for s in sigs if s["ts_ms"] >= cutoff]
    if len(ho) < 5:
        return {"n": len(ho), "avg": None, "note": "too few signals"}
    sims = simulate_signal_set(
        ho, k5, sigma=sigma, expiry_hours=168.0,
        tp1_pct=exs["tp1"], tp2_pct=exs["tp2"], sl_pct=exs["sl"],
        option_horizon_h=exs["hold_h"], spread_pct=spread,
    )
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return {"n": len(ho), "avg": None}
    return {
        "n": len(pnls),
        "wr": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
        "avg": round(statistics.mean(pnls), 2),
        "median": round(statistics.median(pnls), 2),
        "total": round(sum(pnls), 1),
    }


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--config", default=None, help="JSON file with best gen/exit")
    args = ap.parse_args()

    k5, k15, k1h = load_local(find_data_dir(None))
    configs = [
        ("baseline_call", BASELINE_CALL_GEN_KWARGS, BASELINE_CALL_EXIT),
        ("live_cd12", dict(LIVE_GEN_KWARGS), LIVE_EXIT),
        ("live_cd6", dict(LIVE_GEN_KWARGS_ALT), LIVE_EXIT),
    ]
    if args.config:
        cfg = json.loads(Path(args.config).read_text())
        best = cfg.get("best") or cfg
        configs.append(("custom_best", best["gen"], best["exit"]))

    out = []
    for name, gen, ex in configs:
        print(f"\n--- {name} holdout {args.days}d ---", flush=True)
        t0 = time.time()
        ho = eval_holdout(k5, k15, k1h, gen, ex, holdout_days=args.days)
        row = {"name": name, "gen": gen, "exit": ex, "holdout": ho,
               "elapsed_s": round(time.time() - t0, 1)}
        out.append(row)
        print(f"  signals={ho.get('n')} avg={ho.get('avg')}% WR={ho.get('wr')}", flush=True)

    path = Path(__file__).resolve().parents[2] / "sweep_results" / "holdout_90d.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nSaved {path}", flush=True)


if __name__ == "__main__":
    main()

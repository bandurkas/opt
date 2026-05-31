"""Full train+test + full-year validation of strategy candidates."""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir, get_signals, load_local, run_combo
from services.strategy_config import (
    BASELINE_CALL_EXIT,
    BASELINE_CALL_GEN_KWARGS,
    LIVE_EXIT,
    LIVE_GEN_KWARGS,
    LIVE_GEN_KWARGS_ALT,
    active_exit,
    active_gen_kwargs,
)


def _exit_dict(ex: dict) -> dict:
    return {
        "tp1": ex["tp1_pct"],
        "tp2": ex["tp2_pct"],
        "sl": ex["sl_pct"],
        "hold_h": ex["hold_h"],
    }


CANDIDATES = [
    ("baseline_call", BASELINE_CALL_GEN_KWARGS, _exit_dict(BASELINE_CALL_EXIT)),
    ("live_deployed", dict(LIVE_GEN_KWARGS), _exit_dict(LIVE_EXIT)),
    ("live_alt_cd6", dict(LIVE_GEN_KWARGS_ALT), _exit_dict(LIVE_EXIT)),
    ("live_env", active_gen_kwargs(), _exit_dict(active_exit())),
]


def main() -> None:
    k5, k15, k1h = load_local(find_data_dir(None))
    out = []
    for name, gen, ex in CANDIDATES:
        print(f"\n--- {name} ---", flush=True)
        t0 = time.time()
        row = run_combo(k5, k15, k1h, gen, ex, sigma=0.6, spread=2.0, test_only=False)
        tr, te = row.get("train") or {}, row.get("test") or {}
        sigs = get_signals(k5, k15, k1h, gen)
        sims = simulate_signal_set(
            sigs, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=ex["tp1"], tp2_pct=ex["tp2"], sl_pct=ex["sl"],
            option_horizon_h=ex["hold_h"], spread_pct=2.0,
        )
        pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
        full_avg = statistics.mean(pnls) if pnls else 0
        entry = {
            "name": name, "gen": gen, "exit": ex, "n_signals": len(sigs),
            "train": tr, "test": te,
            "full": {"n": len(pnls), "avg": round(full_avg, 2),
                     "wr": round(sum(1 for p in pnls if p > 0) / len(pnls), 3) if pnls else 0},
            "elapsed_s": round(time.time() - t0, 1),
        }
        out.append(entry)
        print(f"  signals={len(sigs)}", flush=True)
        print(f"  train avg={tr.get('avg')}% n={tr.get('n')}", flush=True)
        print(f"  test  avg={te.get('avg')}% n={te.get('n')} sharpe={te.get('sharpe')}", flush=True)
        print(f"  full  avg={full_avg:+.2f}% n={len(pnls)} WR={entry['full']['wr']*100:.1f}%", flush=True)

    path = Path(__file__).resolve().parents[2] / "sweep_results" / "validation.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nSaved {path}", flush=True)


if __name__ == "__main__":
    main()

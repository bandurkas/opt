"""Parallel wing x TP_frac x spread-on/off sweep for the gold iron-butterfly
harness, using all CPU cores. Each combo runs in its own subprocess (the
harness is a plain script reading argv), so plain multiprocessing.Pool over
subprocess calls is both simple and fully parallel -- no shared state needed.

Run: python3 backend/services/gold_ironfly_sweep_parallel.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HARNESS = str(Path(__file__).resolve().parent / "gold_iron_butterfly_backtest.py")
REPO_ROOT = Path(__file__).resolve().parents[2]

WINGS = [5, 10, 15, 20]
TPS = [0.30, 0.50, 0.70, 0.90, 1.0]
SPREADS = [1, 0]  # 1=real spread on, 0=theoretical mid (old behavior, for comparison


def run_one(wing: float, tp: float, spread_on: int) -> dict:
    out = subprocess.run(
        [sys.executable, HARNESS, str(wing), str(tp), str(spread_on)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    ).stdout

    def grab(pat, txt=out):
        m = re.search(pat, txt)
        return m.group(1) if m else None

    overall = grab(r"overall: (n.*Sh[+-][\d.]+)")
    train = grab(r"TRAIN\([^)]*\) ?: (n.*Sh[+-][\d.]+)")
    holdout = grab(r"HOLDOUT\([^)]*\): (n.*Sh[+-][\d.]+)")
    margin = grab(r"avg margin/contract=\$([\d,]+)")
    credit = grab(r"avg credit/contract=\$([\d,]+)")
    util20_line = grab(r"risk=20% of capital/cycle\) ===\n.*\n.*TOTAL return=([+-][\d.]+)%.*maxDD=([\d.]+)%")
    return {
        "wing": wing, "tp": tp, "spread_on": spread_on,
        "overall": overall, "train": train, "holdout": holdout,
        "margin": margin, "credit": credit,
    }


def main():
    combos = [(w, t, s) for s in SPREADS for w in WINGS for t in TPS]
    results = []
    with ProcessPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(run_one, w, t, s): (w, t, s) for w, t, s in combos}
        for fut in as_completed(futs):
            results.append(fut.result())

    results.sort(key=lambda r: (r["spread_on"], r["wing"], r["tp"]), reverse=True)
    print(f"{'spread':>7} {'wing':>5} {'tp':>5} {'margin':>8} {'credit':>8}  {'overall':<32} {'train':<32} {'holdout':<32}")
    for r in results:
        print(f"{'ON' if r['spread_on'] else 'OFF':>7} {r['wing']:>5} {r['tp']:>5} "
              f"{('$'+r['margin']) if r['margin'] else '-':>8} {('$'+r['credit']) if r['credit'] else '-':>8}  "
              f"{r['overall'] or '-':<32} {r['train'] or '-':<32} {r['holdout'] or '-':<32}")


if __name__ == "__main__":
    main()

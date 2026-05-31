"""Final validation: full train/test + 90d holdout on top Put candidates."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.holdout_eval import eval_holdout
from services.local_optimizer import find_data_dir, load_local, run_combo, score_row

# Proper-holdout grid: bull × cd combinatorial, vol/regime pinned at winners.
# Each gets train(pre-cutoff 70%) / test(pre-cutoff 30%) / holdout(last 90d).
_PUT_EXIT = {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "lbl": "decay_72h_widest"}
_CALL_EXIT = {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24, "lbl": "decay_24h"}


def _put(vol=0.50, regimes=("range",), bull=1.05, cd=12):
    return {
        "vol_threshold": vol,
        "regime_filter": list(regimes),
        "side": "P",
        "mtf_direction_filter": "up",
        "bull_market_ratio_max": bull,
        "adx_max": None,
        "cooldown_bars": cd,
    }


CANDIDATES = [
    # ── core: 3 bull × 2 cd, vol=0.50, regime=range ──
    ("put_bull105_cd12", _put(bull=1.05, cd=12), _PUT_EXIT),
    ("put_bull108_cd12", _put(bull=1.08, cd=12), _PUT_EXIT),
    ("put_bullNone_cd12", _put(bull=None, cd=12), _PUT_EXIT),
    ("put_bull105_cd6", _put(bull=1.05, cd=6), _PUT_EXIT),
    ("put_bull108_cd6", _put(bull=1.08, cd=6), _PUT_EXIT),
    ("put_bullNone_cd6", _put(bull=None, cd=6), _PUT_EXIT),
    # ── sanity controls ──
    ("baseline_call", {
        "vol_threshold": 0.60, "regime_filter": ["range", "transition"], "side": "C",
        "mtf_direction_filter": "down", "bull_market_ratio_max": 1.05,
        "adx_max": None, "cooldown_bars": 6,
    }, _CALL_EXIT),
    # ── lower-frequency Put variant (deployed vol→0.45 idea) ──
    ("put_v045_bull108_cd12", _put(vol=0.45, bull=1.08, cd=12), _PUT_EXIT),
]


def composite_score(row: dict, holdout: dict) -> float:
    """Holdout-weighted score with selection-bias penalty.

    Weights:
      holdout 0.50  (true unseen 90d — most trustworthy)
      test    0.40  (pre-cutoff 30% — used for ranking, so subject to selection)
      sharpe  0.30  (risk-adjusted bonus)
    Penalties:
      overfit         (train_avg >> test_avg, gap > 5%)
      selection_bias  (test_avg  >> holdout_avg, gap > 5%)
    """
    te = row.get("test") or {}
    tr = row.get("train") or {}
    if not te.get("avg") or (te.get("n") or 0) < 25:
        return -999.0
    ho_avg = holdout.get("avg")
    ho_n = holdout.get("n") or 0
    if ho_n < 15:
        return -999.0

    test_score = te["avg"] * 0.40
    holdout_score = ho_avg * 0.50 if ho_avg is not None else -10.0
    sharpe_score = (te.get("sharpe") or 0) * 0.30

    overfit_pen = max(0.0, (tr.get("avg") or 0) - te["avg"] - 5.0) * 0.6 if tr else 0.0
    selection_pen = (
        max(0.0, te["avg"] - ho_avg - 5.0) * 0.5
        if ho_avg is not None else 0.0
    )

    return test_score + holdout_score + sharpe_score - overfit_pen - selection_pen


def main() -> None:
    k5, k15, k1h = load_local(find_data_dir(None))
    results = []
    for name, gen, ex in CANDIDATES:
        print(f"\n=== {name} ===", flush=True)
        row = run_combo(k5, k15, k1h, gen, ex, sigma=0.6, spread=2.0, test_only=False)
        ho = eval_holdout(k5, k15, k1h, gen, ex, holdout_days=90)
        row["name"] = name
        row["holdout_90d"] = ho
        row["composite"] = round(composite_score(row, ho), 3)
        row["score"] = score_row(row)
        results.append(row)
        te, tr = row.get("test") or {}, row.get("train") or {}
        print(f"  train n={tr.get('n')} avg={tr.get('avg')}%", flush=True)
        print(f"  test  n={te.get('n')} avg={te.get('avg')}% sharpe={te.get('sharpe')}", flush=True)
        print(f"  hold  n={ho.get('n')} avg={ho.get('avg')}%", flush=True)
        print(f"  composite={row['composite']}", flush=True)

    ranked = sorted(results, key=lambda r: r["composite"], reverse=True)
    best = ranked[0]
    out = {
        "best": best,
        "ranking": [{k: r.get(k) for k in ("name", "composite", "score", "train", "test", "holdout_90d", "gen", "exit")}
                    for r in ranked],
    }
    path = Path(__file__).resolve().parents[2] / "sweep_results" / "final_validation.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\n=== WINNER: {best['name']} composite={best['composite']} ===", flush=True)
    print(f"Saved {path}", flush=True)
    return best


if __name__ == "__main__":
    main()

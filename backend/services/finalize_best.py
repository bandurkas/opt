"""Final validation: full train/test + 90d holdout on top Put candidates."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.holdout_eval import eval_holdout
from services.local_optimizer import find_data_dir, load_local, run_combo, score_row

# Top distinct Put configs from iter1 + iter3 candidates
CANDIDATES = [
    ("iter1_winner", {
        "vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
        "mtf_direction_filter": "up", "bull_market_ratio_max": 1.05,
        "adx_max": None, "cooldown_bars": 12,
    }, {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "lbl": "decay_72h_widest"}),
    ("iter1_cd6", {
        "vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
        "mtf_direction_filter": "up", "bull_market_ratio_max": 1.05,
        "adx_max": None, "cooldown_bars": 6,
    }, {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "lbl": "decay_72h_widest"}),
    ("bull_none_cd12", {
        "vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
        "mtf_direction_filter": "up", "bull_market_ratio_max": None,
        "adx_max": None, "cooldown_bars": 12,
    }, {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "lbl": "decay_72h_widest"}),
    ("bull_none_cd6", {
        "vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
        "mtf_direction_filter": "up", "bull_market_ratio_max": None,
        "adx_max": None, "cooldown_bars": 6,
    }, {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "lbl": "decay_72h_widest"}),
    ("range_transition_cd6", {
        "vol_threshold": 0.50, "regime_filter": ["range", "transition"], "side": "P",
        "mtf_direction_filter": "up", "bull_market_ratio_max": 1.05,
        "adx_max": None, "cooldown_bars": 6,
    }, {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "lbl": "decay_72h_widest"}),
    ("vol045_cd12", {
        "vol_threshold": 0.45, "regime_filter": ["range"], "side": "P",
        "mtf_direction_filter": "up", "bull_market_ratio_max": 1.05,
        "adx_max": None, "cooldown_bars": 12,
    }, {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold_h": 72, "lbl": "decay_72h_widest"}),
    ("decay48_cd12", {
        "vol_threshold": 0.50, "regime_filter": ["range"], "side": "P",
        "mtf_direction_filter": "up", "bull_market_ratio_max": 1.05,
        "adx_max": None, "cooldown_bars": 12,
    }, {"tp1": 0.40, "tp2": 0.60, "sl": 1.00, "hold_h": 48, "lbl": "decay_48h_wide"}),
    ("baseline_call", {
        "vol_threshold": 0.60, "regime_filter": ["range", "transition"], "side": "C",
        "mtf_direction_filter": "down", "bull_market_ratio_max": 1.05,
        "adx_max": None, "cooldown_bars": 6,
    }, {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold_h": 24, "lbl": "decay_24h"}),
]


def composite_score(row: dict, holdout: dict) -> float:
    te = row.get("test") or {}
    tr = row.get("train") or {}
    if not te.get("avg") or (te.get("n") or 0) < 25:
        return -999.0
    ho_avg = holdout.get("avg")
    ho_part = (ho_avg or 0) * 0.4 if ho_avg is not None else 0
    overfit = max(0, (tr.get("avg") or 0) - te["avg"] - 8) * 0.4 if tr else 0
    return te["avg"] * 0.6 + ho_part + 0.3 * (te.get("sharpe") or 0) - overfit


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

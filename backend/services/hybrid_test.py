"""Standalone hybrid Put + Call test.

Generates Put MTF-up signals AND Call MTF-down signals on the cd=6 winner
config, merges (dedupe by ts_ms), evaluates on holdout. Compares against
Put-only baseline.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.holdout_split import HOLDOUT_DAYS, holdout_cutoff_ms, split_signals_by_holdout
from services.local_optimizer import find_data_dir, load_local
from services.strategy_config import LIVE_EXIT, LIVE_GEN_KWARGS
from services.strategy_registry import gen_sell_premium_iv_high


def _eval(k5, sigs):
    """Eval signals on holdout slice; returns summary."""
    cutoff = holdout_cutoff_ms(k5)
    _, hold = split_signals_by_holdout(sigs, cutoff)
    if len(hold) < 5:
        return {"n": len(hold), "avg": None}
    sims = simulate_signal_set(
        hold, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=LIVE_EXIT["tp1_pct"], tp2_pct=LIVE_EXIT["tp2_pct"],
        sl_pct=LIVE_EXIT["sl_pct"], option_horizon_h=LIVE_EXIT["hold_h"],
        spread_pct=2.0,
    )
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        return {"n": 0, "avg": None}
    stdev = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
    avg = statistics.mean(pnls)
    monthly_dollar = (len(pnls) / 3.0) * (avg / 100.0) * 8.0
    return {
        "n": len(pnls),
        "wr": round(sum(1 for p in pnls if p > 0) / len(pnls), 3),
        "avg": round(avg, 2),
        "median": round(statistics.median(pnls), 2),
        "stdev": round(stdev, 2),
        "sharpe": round(avg / stdev, 3) if stdev > 0 else None,
        "total": round(sum(pnls), 1),
        "monthly_dollar_400": round(monthly_dollar, 2),
    }


def main() -> None:
    print(f"=== Hybrid Put+Call test on LIVE cd={LIVE_GEN_KWARGS['cooldown_bars']} ===", flush=True)
    t0 = time.time()
    k5, k15, k1h = load_local(find_data_dir(None))

    put_gen = dict(LIVE_GEN_KWARGS)
    call_gen = {**put_gen, "side": "C", "mtf_direction_filter": "down"}

    print(f"[1] Put signals (MTF up)…", flush=True)
    put_sigs = gen_sell_premium_iv_high(k5, k15, k1h, **put_gen)
    print(f"    {len(put_sigs)} total", flush=True)

    print(f"[2] Call signals (MTF down)…", flush=True)
    call_sigs = gen_sell_premium_iv_high(k5, k15, k1h, **call_gen)
    print(f"    {len(call_sigs)} total", flush=True)

    print(f"[3] Eval Put-only holdout…", flush=True)
    put_only = _eval(k5, put_sigs)
    print(f"    n={put_only['n']} avg={put_only['avg']}% sharpe={put_only.get('sharpe')} "
          f"${put_only['monthly_dollar_400']}/mo", flush=True)

    print(f"[4] Eval Call-only holdout…", flush=True)
    call_only = _eval(k5, call_sigs)
    print(f"    n={call_only['n']} avg={call_only['avg']}% sharpe={call_only.get('sharpe')} "
          f"${call_only.get('monthly_dollar_400')}/mo", flush=True)

    print(f"[5] Merge + eval hybrid (Put ∪ Call) holdout…", flush=True)
    merged = {int(s["ts_ms"]): s for s in put_sigs}
    for s in call_sigs:
        ts = int(s["ts_ms"])
        if ts not in merged:
            merged[ts] = s
    hybrid_sigs = sorted(merged.values(), key=lambda s: s["ts_ms"])
    hybrid = _eval(k5, hybrid_sigs)
    print(f"    merged sigs={len(hybrid_sigs)}  holdout n={hybrid['n']}  "
          f"avg={hybrid['avg']}% sharpe={hybrid.get('sharpe')} "
          f"${hybrid['monthly_dollar_400']}/mo", flush=True)

    elapsed = round(time.time() - t0, 1)
    out = {
        "put_only": put_only, "call_only": call_only, "hybrid": hybrid,
        "n_put_total": len(put_sigs), "n_call_total": len(call_sigs),
        "n_hybrid_total": len(hybrid_sigs),
        "elapsed_s": elapsed,
    }
    path = Path(__file__).resolve().parents[2] / "sweep_results" / "hybrid_test.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nSaved {path}  ({elapsed}s)", flush=True)


if __name__ == "__main__":
    main()

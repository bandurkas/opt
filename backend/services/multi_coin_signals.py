"""Cross-coin signal/backtest research for the live V3 strategy.

Q2: does the same V2-hybrid+V3-ADX strategy hold up on BTC/SOL/XRP?
Q3: are signals correlated across coins (silence on ETH = silence everywhere?)
    or decorrelated (more total signals by trading the basket)?

Runs the IDENTICAL `generate(variant="v3")` + simulate pipeline used for ETH on
each coin's 365d klines (data/{coin}_{5m,15m,1h}.json), then measures temporal
co-occurrence of fired signals.

Run (fast, native arm64):
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/multi_coin_signals.py
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir
from services.strategy_config import PUT_EXIT, CALL_EXIT
from services.variant_backtest import generate, stats

# Per-asset honest IV: σ_t from each coin's own 168h realized vol (× iv_rv_mult),
# instead of the constant σ=0.6 (crypto-calibrated, wrong for low-vol gold).
IV_RV_MULT = 1.05  # default; over/under per asset via DERIVE_IV calibration


def dyn_sim_set(sigs, k5, k1h, iv_rv_mult=IV_RV_MULT):
    p = [s for s in sigs if s["side"] == "P"]
    c = [s for s in sigs if s["side"] == "C"]
    common = dict(sigma=0.6, expiry_hours=168.0, spread_pct=2.0,
                  dynamic_sigma=True, klines_1h=k1h, iv_rv_multiplier=iv_rv_mult)
    ps = simulate_signal_set(p, k5, tp1_pct=PUT_EXIT["tp1_pct"], tp2_pct=PUT_EXIT["tp2_pct"],
            sl_pct=PUT_EXIT["sl_pct"], option_horizon_h=PUT_EXIT["hold_h"], **common) if p else []
    cs = simulate_signal_set(c, k5, tp1_pct=CALL_EXIT["tp1_pct"], tp2_pct=CALL_EXIT["tp2_pct"],
            sl_pct=CALL_EXIT["sl_pct"], option_horizon_h=CALL_EXIT["hold_h"], **common) if c else []
    return ps + cs

COINS = ["eth", "btc", "sol", "xrp", "xaut"]
VARIANT = "v3"  # live regime = ADX trend cutoff >35
_IV_MAP = {"5": "5m", "15": "15m", "60": "1h"}


def load_coin(prefix: str, data_dir: Path):
    out = {}
    for iv, fname in _IV_MAP.items():
        out[iv] = json.loads((data_dir / f"{prefix}_{fname}.json").read_text())
    return out["5"], out["15"], out["60"]


def day_key(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def bucket4h(ts_ms: int) -> int:
    return ts_ms // (4 * 3600 * 1000)


def main():
    data_dir = find_data_dir(None)
    print(f"data dir: {data_dir}\nvariant: {VARIANT} (live V3, ADX trend>35)\n", flush=True)

    from services.indicators import realized_vol
    import statistics as _st
    sigs_by_coin: dict[str, list] = {}
    print("σ = per-signal 168h realized-vol × {:.2f} (clamp 0.20-1.50), DERIVE-honest IV\n".format(IV_RV_MULT))
    print(f"{'coin':<5} {'medRV':>6} {'sigs':>5} {'n':>5} {'WR':>6} {'avg':>8} {'total':>9} "
          f"{'sharpe':>7} {'maxCL':>6} {'losM/mo':>8}  per-side")
    print("-" * 100)
    for coin in COINS:
        try:
            k5, k15, k1h = load_coin(coin, data_dir)
        except FileNotFoundError:
            print(f"{coin:<5} — no data, skipped")
            continue
        sigs = generate(k5, k15, k1h, variant=VARIANT)
        sigs_by_coin[coin] = sigs
        # median annualized RV(168h) of this asset, for the readout
        cl1h = [c["close"] for c in k1h]
        rvs = [rv for j in range(168, len(cl1h), 24)
               if (rv := realized_vol(cl1h[:j + 1], lookback=168)) is not None]
        med_rv = _st.median(rvs) if rvs else float("nan")
        st = stats(dyn_sim_set(sigs, k5, k1h))
        if not st:
            print(f"{coin:<5} {len(sigs):>5} — no sims")
            continue
        bs = st["by_side"]
        side_str = "  ".join(
            f"{sd}:n{bs[sd]['n']} {bs[sd]['avg']:+.1f}%" for sd in ("P", "C") if sd in bs
        )
        print(f"{coin:<5} {med_rv*100:>5.0f}% {len(sigs):>5} {st['n']:>5} {st['wr']*100:>5.1f}% "
              f"{st['avg']:>+7.2f}% {st['total']:>+8.1f}% {st['sharpe']:>+6.2f} "
              f"{st['mc']:>6} {st['lm']:>3}/{st['tm']:<4}  {side_str}", flush=True)

    coins = [c for c in COINS if c in sigs_by_coin]
    if "eth" not in coins:
        print("\nno ETH baseline; aborting co-occurrence analysis")
        return

    # ---- Q3: temporal co-occurrence ----
    days = {c: {day_key(s["ts_ms"]) for s in sigs_by_coin[c]} for c in coins}
    b4 = {c: {bucket4h(s["ts_ms"]) for s in sigs_by_coin[c]} for c in coins}

    print(f"\n{'='*92}\nQ3 — SIGNAL CO-OCCURRENCE (do conditions align across coins?)\n{'='*92}")

    # signal frequency
    print("\n-- raw signal frequency (365d) --")
    for c in coins:
        n = len(sigs_by_coin[c])
        print(f"  {c}: {n} signals  ({n/365:.2f}/day, ~1 every {365/n:.1f}d)")

    # union vs eth-alone (the diversification gain)
    eth_days = days["eth"]
    union_days = set().union(*days.values())
    others = [c for c in coins if c != "eth"]
    extra_days = union_days - eth_days
    print("\n-- diversification gain (distinct signal-DAYS) --")
    print(f"  ETH-only signal-days:        {len(eth_days)}")
    print(f"  basket union signal-days:    {len(union_days)}  "
          f"(+{len(union_days)-len(eth_days)} days, x{len(union_days)/max(1,len(eth_days)):.2f})")
    print(f"  days SOME coin fired but ETH SILENT: {len(extra_days)}")

    eth_b4 = b4["eth"]
    union_b4 = set().union(*b4.values())
    print("\n-- diversification gain (distinct 4h-BUCKETS) --")
    print(f"  ETH-only 4h-buckets:   {len(eth_b4)}")
    print(f"  basket union buckets:  {len(union_b4)}  (x{len(union_b4)/max(1,len(eth_b4)):.2f})")

    # pairwise jaccard + conditional overlap on signal-days
    print("\n-- pairwise overlap of signal-DAYS (Jaccard) --")
    for i, a in enumerate(coins):
        for b in coins[i + 1:]:
            inter = len(days[a] & days[b])
            uni = len(days[a] | days[b])
            jac = inter / uni if uni else 0
            print(f"  {a}-{b}: jaccard={jac:.2f}  shared={inter}d  "
                  f"({a}-only {len(days[a]-days[b])}d, {b}-only {len(days[b]-days[a])}d)")

    # conditional: when ETH fires, how often does each other coin also fire same day?
    print("\n-- conditional: of days ETH fired, also fired by... --")
    for c in others:
        also = len(eth_days & days[c])
        print(f"  {c}: {also}/{len(eth_days)} = {100*also/max(1,len(eth_days)):.0f}%")

    # conditional reverse: of days a coin fires, is ETH silent? (decorrelation signal)
    print("\n-- conditional: of days COIN fired, ETH was SILENT --")
    for c in others:
        eth_silent = len(days[c] - eth_days)
        print(f"  {c}: {eth_silent}/{len(days[c])} = {100*eth_silent/max(1,len(days[c])):.0f}% "
              f"of {c}'s signal-days have no ETH signal")

    print(f"\n{'='*92}\nINTERPRETATION:")
    print("  high jaccard / high 'ETH-also' %  → coins move together; basket adds little.")
    print("  low jaccard / high 'ETH-silent' % → decorrelated; basket gives more signals.")


if __name__ == "__main__":
    main()

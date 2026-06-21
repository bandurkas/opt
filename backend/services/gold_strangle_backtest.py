"""Gold (XAUT) PERIODIC SHORT STRADDLE — a directionally-NEUTRAL redesign.

WHY: the prior gold strategy (gold_oos_regime.py) reused the crypto trend-signal
generator, which fires mostly Puts in an uptrend → naked-put selling = a hidden
leveraged long that inverted to -24.9%/trade in the 2026-03 correction (REJECTED,
project_options_gold_rejected memory). The redesign here removes the directional
signal entirely: sell BOTH an ATM call and an ATM put on a fixed TIME cadence (no
trend condition), so the position is symmetric to the underlying's direction.
Realism calibrated to the REAL Bybit/CBOE gold-options measurement taken 2026-06-20:
  - σ clamp narrowed to (0.18, 0.32) — real ATM IV measured ~22-24%, NOT crypto's 0.60
  - spread_pct=5.0 (matches measured CBOE GLD ATM spread, vs old 2% assumption)
NOTE: an earlier OTM-strangle version of this harness had a bug — faking the entry
spot to bias the engine's `round(entry_spot/25)*25` ATM-strike rounding also fed
that fake spot into the BS entry-premium calc, while the forward walk used the REAL
spot path. That priced entry as ATM but decay as OTM → deterministic fake TP2 hits
(100% WR, exactly tp2_pct every cycle). Fixed by dropping the OTM hack entirely and
using true ATM (the engine has no native OTM-strike parameter).
Reuses the SAME validated engine (simulate_signal_set + _simulate_option_trade,
position="short_premium") via synthetic periodic signal pairs — no engine changes.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/gold_strangle_backtest.py
  (or plain `python3 backend/services/gold_strangle_backtest.py` — stdlib only)
"""
from __future__ import annotations

import statistics as st
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set  # noqa: E402
from services.local_optimizer import find_data_dir  # noqa: E402
from services.multi_coin_signals import load_coin  # noqa: E402

COIN = "xaut"
CYCLE_H = float(sys.argv[1]) if len(sys.argv) > 1 else 168.0
HOLD_H = float(sys.argv[2]) if len(sys.argv) > 2 else CYCLE_H
SIGMA_CLAMP = (0.18, 0.32)   # real measured gold ATM IV ~22-24%, narrow clamp
SPREAD_PCT = 5.0             # real measured CBOE GLD ATM spread (was 2% in old tests)
IV_RV_MULT = 1.05
TP1_PCT, TP2_PCT, SL_PCT = 0.30, 0.60, 0.50   # symmetric defined exits, both legs
TRAIN_FRAC = 0.70


def mo(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def agg(pnls):
    if not pnls:
        return "  n   0"
    n = len(pnls)
    avg = sum(pnls) / n
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    return f"n{n:>4} avg{avg:>+6.2f}% WR{wr*100:>3.0f}% Sh{sh:>+5.2f}"


def build_periodic_signals(k5: list[dict], cycle_h: float) -> list[dict]:
    """Every cycle_h hours, ONE entry timestamp, both C and P legs (no trend filter),
    same ATM strike for both (true short straddle)."""
    if not k5:
        return []
    step_bars = int(cycle_h * 60 / 5)  # 5m bars per cycle
    warmup = 60
    sigs = []
    idx = warmup
    while idx < len(k5):
        close = k5[idx]["close"]
        ts_ms = k5[idx]["start_ms"] + 5 * 60 * 1000
        for side in ("C", "P"):
            sigs.append({
                "idx_5m": idx, "ts_ms": ts_ms, "close": close,
                "side": side, "position": "short_premium", "_cycle": idx,
            })
        idx += step_bars
    return sigs


def main():
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_coin(COIN, data_dir)
    sigs = build_periodic_signals(k5, CYCLE_H)
    print(f"{COIN.upper()} periodic short straddle: cycle={CYCLE_H}h hold={HOLD_H}h "
          f"sigma_clamp={SIGMA_CLAMP} spread={SPREAD_PCT}%  "
          f"({len(sigs)//2} cycles, {len(sigs)} legs)\n")

    out = simulate_signal_set(
        sigs, k5, sigma=0.25, expiry_hours=CYCLE_H, tp1_pct=TP1_PCT, tp2_pct=TP2_PCT,
        sl_pct=SL_PCT, option_horizon_h=HOLD_H, spread_pct=SPREAD_PCT,
        dynamic_sigma=True, klines_1h=k1h, iv_rv_multiplier=IV_RV_MULT,
        sigma_clamp=SIGMA_CLAMP,
    )

    # combine legs per cycle (avg of call+put pnl = strangle pnl, equal weight)
    by_cycle: dict[int, dict] = {}
    for o in out:
        opt = o.get("option", {})
        if "pnl_pct" not in opt or opt.get("resolution") in ("no_entry", "no_data"):
            continue
        c = o["_cycle"]
        by_cycle.setdefault(c, {"ts_ms": o["ts_ms"]})[o["side"]] = opt["pnl_pct"]

    # also capture sigma_used (trailing RV168h at entry) per cycle — our only
    # richness-timing proxy without a real historical IV series.
    sigma_by_cycle: dict[int, float] = {}
    for o in out:
        if o["side"] == "C":  # one snapshot per cycle is enough (same σ both legs)
            sigma_by_cycle[o["_cycle"]] = o.get("sigma_used", 0.0)

    rows = []  # (ts_ms, strangle_pnl_pct, c_pnl, p_pnl, sigma_at_entry)
    for c, d in sorted(by_cycle.items()):
        if "C" not in d or "P" not in d:
            continue
        cp, pp = d["C"], d["P"]
        rows.append((d["ts_ms"], (cp + pp) / 2, cp, pp, sigma_by_cycle.get(c, 0.0)))

    print(f"=== STRADDLE combined (n={len(rows)} cycles) ===")
    pnls = [p for _, p, _, _, _ in rows]
    print(f"  overall: {agg(pnls)}")
    cpnls = [c for _, _, c, _, _ in rows]
    ppnls = [p for _, _, _, p, _ in rows]
    print(f"  call leg: {agg(cpnls)}")
    print(f"  put  leg: {agg(ppnls)}")

    by_mo: "OrderedDict[str, list]" = OrderedDict()
    for ts, p, _, _, _ in rows:
        by_mo.setdefault(mo(ts), []).append(p)
    print("\n  per-month:")
    for m in sorted(by_mo):
        print(f"    {m}: {agg(by_mo[m])}")

    ts_all = sorted(ts for ts, *_ in rows)
    if ts_all:
        split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
        tr = [p for ts, p, _, _, _ in rows if ts < split_ts]
        ho = [p for ts, p, _, _, _ in rows if ts >= split_ts]
        print(f"\n  TRAIN(<{mo(int(split_ts))}) : {agg(tr)}")
        print(f"  HOLDOUT(>={mo(int(split_ts))}): {agg(ho)}")

    # ── richness-timing test: bucket cycles by trailing-RV (sigma_used) tercile ──
    valid = [(p, s) for _, p, _, _, s in rows if s > 0]
    if len(valid) >= 9:
        svals = sorted(s for _, s in valid)
        n = len(svals)
        lo_cut, hi_cut = svals[n // 3], svals[2 * n // 3]
        low_b = [p for p, s in valid if s <= lo_cut]
        mid_b = [p for p, s in valid if lo_cut < s <= hi_cut]
        high_b = [p for p, s in valid if s > hi_cut]
        print(f"\n  === RICHNESS TIMING: bucket by trailing-RV (σ_used) at entry ===")
        print(f"  σ tercile cuts: low<={lo_cut:.3f}  mid<={hi_cut:.3f}  high>{hi_cut:.3f}")
        print(f"  LOW  vol (cheap, 'bad' time to sell)  : {agg(low_b)}")
        print(f"  MID  vol                              : {agg(mid_b)}")
        print(f"  HIGH vol (rich, 'good' time to sell)  : {agg(high_b)}")

    if pnls:
        avg = sum(pnls) / len(pnls)
        cycles_per_year = 365 * 24 / CYCLE_H
        # rough APR proxy: avg%/cycle compounded at cycles/year, ignoring margin %
        apr_proxy = ((1 + avg / 100) ** cycles_per_year - 1) * 100
        print(f"\n  ~{cycles_per_year:.0f} cycles/yr; naive full-compounding APR proxy (100% margin, "
              f"NOT account-engine) = {apr_proxy:+.1f}%  — compare ballpark to PURR carry (~12-20% APR) "
              f"and ETH options bot, NOT a real account simulation.")


if __name__ == "__main__":
    main()

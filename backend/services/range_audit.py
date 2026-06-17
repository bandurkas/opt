"""Range-detector audit — does the Put `range`-only regime filter leave
profitable premium-selling windows on the table?

The bot stalls because Puts fire only in `range` (ADX<20) while Calls fire in
`range`+`transition` (ADX<35); the live market sits mostly in `transition`, so
Put-biased windows get disqualified and gate cycles barely accumulate. This audit
tests, on 365d ETH with honest σ + deployed exits, whether loosening Puts to
`range`+`transition` is a real throughput win or just adds losing trades.

Method (pure backtest, no bot change): generate baseline (Put rf=["range"]) vs
loosened (Put rf=["range","transition"]); isolate the MARGINAL trades (Put signals
that fire only because transition was allowed) and judge them on their own
train/holdout edge — that, not the blended number, decides it. Risk lens: split
marginal Puts by the underlying's forward move (down = the gold failure mode).

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/range_audit.py
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.variant_backtest as vb
from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.strategy_config import PUT_EXIT
from services.variant_backtest import generate

TRAIN_FRAC = 0.70


def agg(pnls):
    if not pnls:
        return f"{'n   0':<30}"
    n = len(pnls)
    avg = sum(pnls) / n
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    return f"n{n:>4} avg{avg:>+6.2f}% WR{wr*100:>3.0f}% Sh{sh:>+5.2f}"


def sim_puts(sigs, k5, k1h):
    puts = [s for s in sigs if s["side"] == "P"]
    out = simulate_signal_set(puts, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=PUT_EXIT["tp1_pct"], tp2_pct=PUT_EXIT["tp2_pct"],
            sl_pct=PUT_EXIT["sl_pct"], option_horizon_h=PUT_EXIT["hold_h"],
            spread_pct=2.0, dynamic_sigma=True, klines_1h=k1h, iv_rv_multiplier=1.05)
    rows = {}
    for s in out:
        o = s.get("option", {})
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        rows[int(s["ts_ms"])] = o["pnl_pct"]
    return rows  # ts -> pnl%


def split(d, split_ts):
    tr = [p for ts, p in d.items() if ts < split_ts]
    ho = [p for ts, p in d.items() if ts >= split_ts]
    return tr, ho


def fwd_move(k5, ts_ms, horizon_h=96):
    """Underlying %-move from ts over `horizon_h` (sign of the risk for a put)."""
    idx = next((i for i, c in enumerate(k5) if c["start_ms"] >= ts_ms), None)
    if idx is None:
        return None
    j = min(len(k5) - 1, idx + horizon_h * 12)
    p0, p1 = k5[idx]["close"], k5[j]["close"]
    return (p1 / p0 - 1) * 100 if p0 else None


def main():
    coin = sys.argv[1] if len(sys.argv) > 1 else "eth"
    k5, k15, k1h = load_coin(coin, find_data_dir(None))

    # regime distribution of the sample (root cause readout)
    from services.regime import detect_regime
    rc = {"range": 0, "transition": 0, "trend": 0, "unknown": 0}
    for i in range(200, len(k1h)):
        rc[detect_regime(k1h[i - 200:i])["regime"]] += 1
    tot = sum(rc.values()) or 1
    print(f"{coin.upper()} regime distribution (1h bars): "
          + "  ".join(f"{k}={v} ({100*v/tot:.0f}%)" for k, v in rc.items()) + "\n")

    # baseline: Put range-only (current deployed)
    base = generate(k5, k15, k1h, variant="v3")
    base_p = sim_puts(base, k5, k1h)

    # loosened: Put range+transition (mutate the shared dict in place, then restore)
    orig_rf = vb.PUT_GEN_KWARGS["regime_filter"]
    vb.PUT_GEN_KWARGS["regime_filter"] = ["range", "transition"]
    try:
        loose = generate(k5, k15, k1h, variant="v3")
    finally:
        vb.PUT_GEN_KWARGS["regime_filter"] = orig_rf
    loose_p = sim_puts(loose, k5, k1h)

    ts_all = sorted(t for t in loose_p) or [0, 1]
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])

    print("=== PUT side: baseline(range) vs loosened(range+transition) ===")
    btr, bho = split(base_p, split_ts)
    ltr, lho = split(loose_p, split_ts)
    print(f"  baseline range-only : TRAIN {agg(btr)} | HOLDOUT {agg(bho)}")
    print(f"  loosened +transition: TRAIN {agg(ltr)} | HOLDOUT {agg(lho)}")

    # marginal = trades that exist ONLY because transition was allowed
    marg_ts = [t for t in loose_p if t not in base_p]
    marg = {t: loose_p[t] for t in marg_ts}
    mtr, mho = split(marg, split_ts)
    print(f"\n=== MARGINAL transition-only Puts (the decisive set) ===")
    print(f"  count {len(marg)}  (+{100*len(marg)/max(1,len(base_p)):.0f}% vs {len(base_p)} baseline puts)")
    print(f"  TRAIN {agg(mtr)} | HOLDOUT {agg(mho)}")

    # risk lens: marginal puts in down-moves vs up-moves of the underlying
    dn = [loose_p[t] for t in marg_ts if (m := fwd_move(k5, t)) is not None and m < -2]
    up = [loose_p[t] for t in marg_ts if (m := fwd_move(k5, t)) is not None and m > 2]
    fl = [loose_p[t] for t in marg_ts if (m := fwd_move(k5, t)) is not None and -2 <= m <= 2]
    print(f"\n  by 96h underlying move (put risk = down):")
    print(f"    DOWN < -2% : {agg(dn)}")
    print(f"    FLAT ±2%   : {agg(fl)}")
    print(f"    UP   > +2% : {agg(up)}")


if __name__ == "__main__":
    main()

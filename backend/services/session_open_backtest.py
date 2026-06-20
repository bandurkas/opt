"""Session-open backtest — does Asia/US session-open volatility give a tradeable
short-dated (24h) options edge during the `transition` regime windows where the
V3 bot currently sits out?

User's idea: known clock event (exchange/session open) + fixed TP/SL, exit small
on a wrong guess. Two direction variants tested head-to-head, same mechanics as
the bot's existing short-dated Call leg (CALL_EXIT: tp1=0.40 tp2=0.80 sl=0.75
hold=24h), since gold/strangle/ADX-sizing overlays already failed OOS when they
invented new exit mechanics — keep that variable fixed here.

  A) opening-range breakout: 30min post-open range; close beyond high -> Call,
     below low -> Put, signal fires at the 30min mark.
  B) momentum continuation: sign of the prior 4h return at the open instant.

Method: pure backtest, no bot change. Train/holdout 70/30 split by time (matches
range_audit.py / iv_expiry_test.py convention). Report overall AND restricted to
1h bars tagged `transition` by the same ADX detector the live bot uses, since
that's the specific idle window this is meant to fill.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/session_open_backtest.py
"""
from __future__ import annotations

import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.regime import detect_regime
from services.strategy_config import CALL_EXIT

SESSION_OPENS_UTC_MIN = (0, 13 * 60 + 30)  # Asia 00:00 UTC, US 13:30 UTC
OR_MINUTES = 30
MOM_LOOKBACK_H = 4
TRAIN_FRAC = 0.70


def tag_regime_1h(k1h):
    """ts_ms (1h bar start) -> regime, using the same 200-bar ADX window as live."""
    out = {}
    for i in range(200, len(k1h)):
        out[int(k1h[i]["start_ms"])] = detect_regime(k1h[i - 200:i])["regime"]
    return out


def nearest_1h_regime(regime_by_1h, ts_ms, hour_ms=3600_000):
    bucket = (ts_ms // hour_ms) * hour_ms
    return regime_by_1h.get(bucket, "unknown")


def gen_breakout(k5):
    sigs = []
    for i, c in enumerate(k5):
        minute = (c["start_ms"] // 60_000) % (24 * 60)
        if minute not in SESSION_OPENS_UTC_MIN:
            continue
        j = i + OR_MINUTES // 5
        if j >= len(k5):
            continue
        window = k5[i:j]  # OR built from bars BEFORE the breakout bar
        if not window:
            continue
        hi = max(b["high"] for b in window)
        lo = min(b["low"] for b in window)
        close = k5[j]["close"]
        if close > hi:
            side = "C"
        elif close < lo:
            side = "P"
        else:
            continue
        sigs.append({"idx_5m": j, "ts_ms": k5[j]["start_ms"], "close": close, "side": side})
    return sigs


def gen_momentum(k5):
    bars_4h = MOM_LOOKBACK_H * 12
    sigs = []
    for i, c in enumerate(k5):
        minute = (c["start_ms"] // 60_000) % (24 * 60)
        if minute not in SESSION_OPENS_UTC_MIN or i < bars_4h:
            continue
        ret = (c["close"] - k5[i - bars_4h]["close"]) / k5[i - bars_4h]["close"] * 100
        if ret > 0.3:
            side = "C"
        elif ret < -0.3:
            side = "P"
        else:
            continue
        sigs.append({"idx_5m": i, "ts_ms": c["start_ms"], "close": c["close"], "side": side})
    return sigs


def simulate(sigs, k5, k1h):
    out = simulate_signal_set(
        sigs, k5, sigma=0.6, expiry_hours=24.0, spread_pct=2.0,
        dynamic_sigma=True, klines_1h=k1h, iv_rv_multiplier=1.05,
        tp1_pct=CALL_EXIT["tp1_pct"], tp2_pct=CALL_EXIT["tp2_pct"],
        sl_pct=CALL_EXIT["sl_pct"], option_horizon_h=CALL_EXIT["hold_h"],
    )
    rows = []
    for s in out:
        o = s.get("option", {})
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        rows.append((int(s["ts_ms"]), o["pnl_pct"]))
    return rows


def agg(pnls):
    if not pnls:
        return "n   0"
    n = len(pnls)
    avg = sum(pnls) / n
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    return f"n{n:>4} avg{avg:>+6.2f}% WR{wr*100:>3.0f}% Sh{sh:>+5.2f}"


def split(rows, split_ts):
    tr = [p for ts, p in rows if ts < split_ts]
    ho = [p for ts, p in rows if ts >= split_ts]
    return tr, ho


def report(name, rows):
    if not rows:
        print(f"{name}: no trades")
        return
    ts_all = sorted(t for t, _ in rows)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    tr, ho = split(rows, split_ts)
    print(f"{name:<28} TRAIN {agg(tr)} | HOLDOUT {agg(ho)}")


def report_by_regime(name, rows, regime_by_1h):
    trans = [(ts, p) for ts, p in rows if nearest_1h_regime(regime_by_1h, ts) == "transition"]
    other = [(ts, p) for ts, p in rows if nearest_1h_regime(regime_by_1h, ts) != "transition"]
    report(f"  {name} | transition-only", trans)
    report(f"  {name} | other regimes", other)


def main():
    coin = sys.argv[1] if len(sys.argv) > 1 else "eth"
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    regime_by_1h = tag_regime_1h(k1h)

    print(f"=== {coin.upper()} session-open signals, 24h short-dated options, "
          f"fixed TP/SL (CALL_EXIT mechanics) ===\n")

    for name, gen in (("A) opening-range breakout", gen_breakout),
                       ("B) momentum continuation", gen_momentum)):
        sigs = gen(k5)
        c = sum(1 for s in sigs if s["side"] == "C")
        p = sum(1 for s in sigs if s["side"] == "P")
        print(f"{name} — {len(sigs)} signals ({c} Call / {p} Put)")
        rows = simulate(sigs, k5, k1h)
        report("  overall", rows)
        report_by_regime(name, rows, regime_by_1h)
        print()


if __name__ == "__main__":
    main()

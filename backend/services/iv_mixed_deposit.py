"""Throughput payoff: $400 model under MIXED expiry — Calls @24h (re-tuned exits),
Puts @96h (current) — vs the current all-7d baseline. Shows that faster capital
recycling lets the margin-bound account EXECUTE more signals at equal/better quality.

Same Bybit-realistic engine as deposit_sim.py (15% margin, IM, MAX_OPEN=4, 80% cap,
fees, dyn-size, circuit-breaker), compounding.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/iv_mixed_deposit.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs
from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.strategy_config import CALL_EXIT, PUT_EXIT
from services.variant_backtest import generate

START = 400.0
MARGIN_PCT = 0.15
IM_RATE = 0.10
LOT = 0.1
MAX_OPEN = 4
PORT_MARGIN_CAP = 0.80
FEE_RATE = 0.0003
FEE_CAP = 0.125
HALF_SPREAD = 0.01
CB_LOSSES = 5
CB_COOLDOWN_MS = 48 * 3600 * 1000

TRAIN_FRAC = 0.70
# re-optimized Call exits, robust on holdout
CALL_24 = {"tp1": 0.4, "tp2": 0.8, "sl": 0.75, "hold": 24, "expiry": 24.0}
CALL_48 = {"tp1": 0.4, "tp2": 0.6, "sl": 2.0, "hold": 48, "expiry": 48.0}
PUT_96 = {"tp1": PUT_EXIT["tp1_pct"], "tp2": PUT_EXIT["tp2_pct"],
          "sl": PUT_EXIT["sl_pct"], "hold": PUT_EXIT["hold_h"], "expiry": 168.0}
# baseline all-7d
CALL_7D = {"tp1": CALL_EXIT["tp1_pct"], "tp2": CALL_EXIT["tp2_pct"],
           "sl": CALL_EXIT["sl_pct"], "hold": CALL_EXIT["hold_h"], "expiry": 168.0}


def fee(notional, premium_total):
    return min(notional * FEE_RATE, abs(premium_total) * FEE_CAP)


def build_trades(sigs, k5, k1h, cfg):
    out = simulate_signal_set(sigs, k5, sigma=0.6, expiry_hours=cfg["expiry"],
            tp1_pct=cfg["tp1"], tp2_pct=cfg["tp2"], sl_pct=cfg["sl"],
            option_horizon_h=cfg["hold"], spread_pct=2.0, dynamic_sigma=True,
            klines_1h=k1h, iv_rv_multiplier=1.05)
    T0 = cfg["expiry"] / (24 * 365)
    trades = []
    for s in out:
        o = s.get("option", {})
        if "pnl_pct" not in o or o.get("resolution") in ("no_entry", "no_data"):
            continue
        spot = s["close"]
        strike = round(spot / 25) * 25
        mid = bs.price(s["side"], spot, strike, T0, s["sigma_used"])
        if mid <= 0.01:
            continue
        bars = o.get("bars_held", int(cfg["expiry"] * 12))
        trades.append({"ts": int(s["ts_ms"]), "exit_ts": int(s["ts_ms"]) + bars * 5 * 60 * 1000,
                       "strike": strike, "mid": mid, "credit": mid * (1 - HALF_SPREAD),
                       "pnl_pct": o["pnl_pct"] / 100.0, "sigma": s.get("sigma_used", 0.0)})
    return trades


def apply_floor(trades, split_ts):
    """Premium FLOOR (§4.0): drop bottom-quartile-σ trades; cut fit on TRAIN only."""
    tr_sig = sorted(t["sigma"] for t in trades if t["ts"] < split_ts)
    if not tr_sig:
        return trades
    cut = tr_sig[len(tr_sig) // 4]
    return [t for t in trades if t["sigma"] > cut]


def run_engine(trades, label):
    trades = sorted(trades, key=lambda t: t["ts"])
    equity = START
    peak = equity
    max_dd = 0.0
    open_pos = []
    recent = []
    taken_ts = []
    consec = 0
    cb_until = 0
    n_taken = n_cap = n_margin = n_cb = 0

    def realize(now_ts):
        nonlocal equity, peak, max_dd, consec, cb_until
        still = []
        for p in sorted(open_pos, key=lambda x: x["exit_ts"]):
            if p["exit_ts"] <= now_ts:
                equity += p["pnl_dollars"]
                recent.append(p["pnl_pct"])
                if p["pnl_pct"] > 0:
                    consec = 0
                else:
                    consec += 1
                    if consec >= CB_LOSSES:
                        cb_until = p["exit_ts"] + CB_COOLDOWN_MS
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak)
            else:
                still.append(p)
        open_pos[:] = still

    for t in trades:
        realize(t["ts"])
        if t["ts"] < cb_until:
            n_cb += 1
            continue
        if len(open_pos) >= MAX_OPEN:
            n_cap += 1
            continue
        used = sum(p["margin"] for p in open_pos)
        free = max(0.0, equity * PORT_MARGIN_CAP - used)
        dyn = 0.5 if (len(recent) >= 10 and sum(1 for x in recent[-10:] if x > 0) / 10 < 0.40) else 1.0
        budget = min(equity * MARGIN_PCT * dyn, free)
        m_per_lot = (IM_RATE * t["strike"] + t["mid"]) * LOT
        n_lots = int(budget // m_per_lot) if m_per_lot > 0 else 0
        if n_lots < 1:
            n_margin += 1
            continue
        qty = n_lots * LOT
        credit_total = t["credit"] * qty
        gross = credit_total * t["pnl_pct"]
        fees = 2 * fee(t["strike"] * qty, credit_total)
        open_pos.append({"exit_ts": t["exit_ts"], "margin": m_per_lot * n_lots,
                         "pnl_dollars": gross - fees, "pnl_pct": t["pnl_pct"]})
        taken_ts.append(t["ts"])
        n_taken += 1
    if open_pos:
        realize(max(p["exit_ts"] for p in open_pos) + 1)
    ret = (equity - START) / START * 100
    print(f"\n{label}")
    print(f"  signals={len(trades)}  taken={n_taken}  blocked: cap={n_cap} margin={n_margin} cb={n_cb}")
    print(f"  START $400 -> FINAL ${equity:,.2f}  ({ret:+.1f}%)  maxDD {max_dd*100:.1f}%  "
          f"avg ${(equity-START)/max(1,n_taken):+.2f}/taken")
    return n_taken, equity, taken_ts


def run(coin="eth"):
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    sigs = generate(k5, k15, k1h, variant="v3")
    p = [s for s in sigs if s["side"] == "P"]
    c = [s for s in sigs if s["side"] == "C"]
    print(f"{coin.upper()}: {len(p)} Put, {len(c)} Call signals")

    ts_all = sorted(int(s["ts_ms"]) for s in sigs)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])

    put96 = build_trades(p, k5, k1h, PUT_96)
    c7d = build_trades(c, k5, k1h, CALL_7D)
    c24 = build_trades(c, k5, k1h, CALL_24)
    c48 = build_trades(c, k5, k1h, CALL_48)

    configs = {
        "BASELINE Call@7d+Put@7d": c7d + put96,
        "MIXED-24 Call@24h+Put@96h": c24 + put96,
        "MIXED-48 Call@48h+Put@96h": c48 + put96,
        "MIXED-24 + FLOOR": apply_floor(c24, split_ts) + apply_floor(put96, split_ts),
        "MIXED-48 + FLOOR": apply_floor(c48, split_ts) + apply_floor(put96, split_ts),
    }
    taken_by_label = {}
    for label, trades in configs.items():
        _, _, tts = run_engine(trades, f"=== {label} ===")
        taken_by_label[label] = tts

    # trades-per-day analysis
    def day(ts):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    span_days = (ts_all[-1] - ts_all[0]) / 86_400_000
    gen_calls = len(c)
    gen_puts = len(p)
    print("\n---------- TRADES PER DAY ----------")
    print(f"period: {span_days:.0f} days")
    print(f"GENERATED by strategy: {gen_calls+gen_puts} total "
          f"= {(gen_calls+gen_puts)/span_days:.2f}/day  (Call {gen_calls/span_days:.2f}/day, "
          f"Put {gen_puts/span_days:.2f}/day) — what a NON-margin-bound account could take")
    for label in ("BASELINE Call@7d+Put@7d", "MIXED-24 Call@24h+Put@96h"):
        tts = taken_by_label[label]
        days = {}
        for ts in tts:
            d = day(ts)
            days[d] = days.get(d, 0) + 1
        active = len(days)
        dist = {}
        for cnt in days.values():
            dist[cnt] = dist.get(cnt, 0) + 1
        print(f"\n{label}: TAKEN {len(tts)} = {len(tts)/span_days:.2f}/day on $400")
        print(f"  active days (>=1 trade): {active}/{span_days:.0f} ({active/span_days*100:.0f}%) "
              f"-> on active days {len(tts)/max(1,active):.2f} trades/day")
        print(f"  max in one day: {max(days.values()) if days else 0}; "
              f"days with 1/2/3/4+ trades: "
              f"{dist.get(1,0)}/{dist.get(2,0)}/{dist.get(3,0)}/{sum(v for k,v in dist.items() if k>=4)}")

    # honest per-trade quality on HOLDOUT only (no compounding, no margin) —
    # guards against the compounding/in-sample-fit mirage of the $ figures above.
    print("\n---------- HOLDOUT per-trade quality (raw, no compounding) ----------")
    print(f"{'config':<28} {'n':>5} {'avg%':>7} {'WR':>6} {'Sharpe':>7}")
    import statistics as _st
    for label, trades in configs.items():
        ho = [t["pnl_pct"] * 100 for t in trades if t["ts"] >= split_ts]
        if not ho:
            continue
        n = len(ho); avg = sum(ho) / n; wr = sum(1 for x in ho if x > 0) / n
        sd = _st.stdev(ho) if n > 1 else 0.0
        print(f"{label:<28} {n:>5} {avg:>+7.2f} {wr*100:>5.1f}% {(avg/sd if sd else 0):>+7.3f}")


if __name__ == "__main__":
    run("eth")

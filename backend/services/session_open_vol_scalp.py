"""Short straddle/strangle scalp around the confirmed vol-spike event
(CBOE/NYSE cash open 9:30 ET — realized vol 1.6-1.7x baseline, t~9-13,
see session_open_vol_spike.py). Mechanics per the user's idea: sell a
straddle/strangle right BEFORE the open, buy it back 30-60min later — never
held to the 24h expiry.

Entry σ is a trailing 168h realized-vol estimate (same calibration the live
bot uses: RV_168h × 1.05, clamped 0.20-1.50) — i.e. the "calm-day-average" vol
a real seller would quote, which does NOT already know this specific 30-60min
window is about to run hot. σ is held CONSTANT between entry and exit pricing
(no vega assumption) so the P&L isolates exactly the question: does the
elevated realized move during the spike outrun the theta collected, net of a
2% round-trip spread (same convention as backtest.py's spread_pct=2.0)?

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/session_open_vol_scalp.py [coin]
"""
from __future__ import annotations

import math
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import backtest_bs as bs
from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin

NY = ZoneInfo("America/New_York")
EVENTS = {
    "CBOE/NYSE cash open 9:30 ET": (9, 30),
    "COMEX pit-era open 8:20 ET": (8, 20),
    "CME Globex reopen 18:00 ET": (18, 0),
}
HOLD_MIN = (30, 60)
SPREAD_PCT = 2.0
HALF_SPREAD = SPREAD_PCT / 200.0
EXPIRY_H = 24.0
SIGMA_CLAMP = (0.20, 1.50)
TRAIN_FRAC = 0.70
STRIKE_ROUND = {"eth": 25, "xaut": 25}
WING_PCT = 0.015  # strangle wings at +-1.5%


def local_hm(ts_ms):
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(NY)
    return dt.hour, dt.minute


def trailing_sigma(k1h, idx_1h, lookback_h=168, mult=1.05):
    if idx_1h < lookback_h + 1:
        return None
    closes = [k1h[i]["close"] for i in range(idx_1h - lookback_h, idx_1h + 1)]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 2:
        return None
    sd = st.stdev(rets)
    sigma = sd * math.sqrt(8760) * mult
    return max(SIGMA_CLAMP[0], min(SIGMA_CLAMP[1], sigma))


def nearest_1h_idx(k1h, ts_ms, hour_ms=3600_000):
    bucket = (ts_ms // hour_ms) * hour_ms
    lo, hi = 0, len(k1h) - 1
    best = None
    for i, c in enumerate(k1h):
        if int(c["start_ms"]) == bucket:
            return i
    return None


def straddle_legs_pnl(S0, S1, sigma, round_to, T0_h, dt_h, wing_pct=0.0, position="short"):
    K_c = round((S0 * (1 + wing_pct)) / round_to) * round_to
    K_p = round((S0 * (1 - wing_pct)) / round_to) * round_to
    T0 = T0_h / (24 * 365)
    T1 = max(0.0, (T0_h - dt_h)) / (24 * 365)

    call_entry = bs.price("C", S0, K_c, T0, sigma)
    put_entry = bs.price("P", S0, K_p, T0, sigma)
    if call_entry <= 0.01 or put_entry <= 0.01:
        return None
    mid_entry = call_entry + put_entry

    call_exit = bs.price("C", S1, K_c, T1, sigma) if T1 > 0 else max(0.0, S1 - K_c)
    put_exit = bs.price("P", S1, K_p, T1, sigma) if T1 > 0 else max(0.0, K_p - S1)
    mid_exit = call_exit + put_exit

    if position == "short":
        credit = mid_entry * (1 - HALF_SPREAD)
        debit = mid_exit * (1 + HALF_SPREAD)
        pnl_usd = credit - debit
        base = credit
    else:  # long: pay ask to enter, sell at bid to exit
        debit = mid_entry * (1 + HALF_SPREAD)
        credit = mid_exit * (1 - HALF_SPREAD)
        pnl_usd = credit - debit
        base = debit

    pnl_pct = pnl_usd / base * 100 if base > 0 else 0.0
    return pnl_pct


def run_variant(k5, k1h, idxs, hold_min, round_to, wing_pct, label, position="short"):
    rows = []
    n_bars = hold_min // 5
    for i in idxs:
        j = i + n_bars
        if j >= len(k5):
            continue
        ts_ms = k5[i]["start_ms"]
        idx_1h = nearest_1h_idx(k1h, ts_ms)
        if idx_1h is None:
            continue
        sigma = trailing_sigma(k1h, idx_1h)
        if sigma is None:
            continue
        S0, S1 = k5[i]["close"], k5[j]["close"]
        pnl = straddle_legs_pnl(S0, S1, sigma, round_to, EXPIRY_H, hold_min / 60.0, wing_pct, position)
        if pnl is not None:
            rows.append((ts_ms, pnl))

    if not rows:
        print(f"    {label}: no trades")
        return
    ts_all = sorted(t for t, _ in rows)
    split = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    tr = [p for t, p in rows if t < split]
    ho = [p for t, p in rows if t >= split]

    def agg(v):
        if not v:
            return "n   0"
        n = len(v)
        m = sum(v) / n
        sd = st.stdev(v) if n > 1 else 0.0
        t = m / (sd / n ** 0.5) if sd > 0 else 0.0
        wr = sum(1 for x in v if x > 0) / n
        return f"n={n:>4} avg={m:>+6.2f}% WR={wr*100:>3.0f}% t={t:>+5.2f}"

    print(f"    {label}: TRAIN {agg(tr)}  |  HOLDOUT {agg(ho)}")


def main():
    coin = sys.argv[1] if len(sys.argv) > 1 else "eth"
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    round_to = STRIKE_ROUND.get(coin, 25)

    print(f"=== {coin.upper()} straddle/strangle scalp around session open, "
          f"constant sigma (no vega assumption) ===")
    for ev_name, hm in EVENTS.items():
        idxs = [i for i, c in enumerate(k5) if local_hm(c["start_ms"]) == hm]
        print(f"\n{ev_name} — {len(idxs)} occurrences")
        for hold in HOLD_MIN:
            for pos in ("short", "long"):
                run_variant(k5, k1h, idxs, hold, round_to, 0.0,
                            f"[{pos:>5}] straddle (ATM)    hold={hold}m", pos)
                run_variant(k5, k1h, idxs, hold, round_to, WING_PCT,
                            f"[{pos:>5}] strangle (+-{WING_PCT*100:.1f}%) hold={hold}m", pos)


if __name__ == "__main__":
    main()

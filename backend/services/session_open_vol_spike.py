"""Does realized volatility actually spike around CME/CBOE/COMEX open events?

Premise check before designing a "sell the uncertainty" straddle: the prior
session_open_stats.py / cme_cboe_open_stats.py tests found no DIRECTIONAL edge
at session opens. That's compatible with a pure VOLATILITY effect (event
matters for size of move, not sign). This measures realized vol in the [open,
open+N] window vs a matched-length baseline of ALL other times of day, for the
same 3 events used before, both ETH and XAUT.

Realized vol = stdev of 5m log returns within the window, annualized for
comparability across windows of different length.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/session_open_vol_spike.py [coin]
"""
from __future__ import annotations

import math
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin

NY = ZoneInfo("America/New_York")
EVENTS = {
    "CBOE/NYSE cash open 9:30 ET": (9, 30),
    "COMEX pit-era open 8:20 ET": (8, 20),
    "CME Globex reopen 18:00 ET": (18, 0),
}
WINDOWS_MIN = (30, 60)
BARS_PER_YEAR_5M = 365 * 24 * 12  # for annualizing 5m-return stdev


def local_hm(ts_ms):
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(NY)
    return dt.hour, dt.minute


def log_returns(closes):
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]


def realized_vol_annualized(rets):
    if len(rets) < 2:
        return None
    sd = st.stdev(rets)
    return sd * math.sqrt(BARS_PER_YEAR_5M)


def window_vols(k5, idxs, n_bars):
    out = []
    for i in idxs:
        j = i + n_bars
        if j >= len(k5):
            continue
        closes = [k5[k]["close"] for k in range(i, j + 1)]
        v = realized_vol_annualized(log_returns(closes))
        if v is not None:
            out.append(v)
    return out


def baseline_vols(k5, n_bars, exclude_minutes, sample_every=3):
    """Realized vol of ALL windows starting at bars NOT in an event minute,
    subsampled by `sample_every` since adjacent overlapping windows are
    highly correlated and would otherwise dominate the average with redundant data."""
    out = []
    for i in range(0, len(k5) - n_bars, sample_every):
        if local_hm(k5[i]["start_ms"]) in exclude_minutes:
            continue
        closes = [k5[k]["close"] for k in range(i, i + n_bars + 1)]
        v = realized_vol_annualized(log_returns(closes))
        if v is not None:
            out.append(v)
    return out


def main():
    coin = sys.argv[1] if len(sys.argv) > 1 else "eth"
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    exclude_minutes = set(EVENTS.values())

    print(f"=== {coin.upper()} — realized vol in event window vs baseline (annualized) ===")
    for ev_name, hm in EVENTS.items():
        idxs = [i for i, c in enumerate(k5) if local_hm(c["start_ms"]) == hm]
        print(f"\n{ev_name} — {len(idxs)} occurrences")
        for n_min in WINDOWS_MIN:
            n_bars = n_min // 5
            ev_vols = window_vols(k5, idxs, n_bars)
            base_vols = baseline_vols(k5, n_bars, exclude_minutes)
            if not ev_vols or not base_vols:
                continue
            ev_mean = sum(ev_vols) / len(ev_vols)
            base_mean = sum(base_vols) / len(base_vols)
            # Welch's t-test for difference of means (unequal variance)
            n1, n2 = len(ev_vols), len(base_vols)
            sd1, sd2 = st.stdev(ev_vols), st.stdev(base_vols)
            se = math.sqrt(sd1 ** 2 / n1 + sd2 ** 2 / n2)
            t = (ev_mean - base_mean) / se if se > 0 else 0.0
            ratio = ev_mean / base_mean if base_mean else 0.0
            flag = " <-- |t|>2" if abs(t) > 2 else ""
            print(f"  +{n_min}m: event_vol={ev_mean*100:>6.1f}%  baseline_vol={base_mean*100:>6.1f}%  "
                  f"ratio={ratio:>5.2f}x  t={t:>+5.2f}{flag}")


if __name__ == "__main__":
    main()

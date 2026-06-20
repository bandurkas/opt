"""DST-aware re-check of US-exchange-open price stats — fixes a methodology gap
in session_open_stats.py, which matched a FIXED UTC minute (13:30) for "US
session open". The real NYSE/CBOE cash open (9:30am ET) is 13:30 UTC only
during EDT; during EST it's 14:30 UTC. Half the dataset was matched an hour
off, diluting any real effect. This uses America/New_York local time so the
DST shift is handled correctly, and checks three actual US-exchange events
instead of one generic "US session":

  - CBOE/NYSE cash open      9:30am ET  (equity/index/VIX options trade)
  - COMEX pit-era open       8:20am ET  (historic gold futures pit open;
                                          still the volume-concentration time)
  - CME Globex daily reopen  6:00pm ET  (after the daily maintenance halt)

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/cme_cboe_open_stats.py [coin]
"""
from __future__ import annotations

import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.regime import detect_regime

NY = ZoneInfo("America/New_York")
EVENTS = {
    "CBOE/NYSE cash open 9:30 ET": (9, 30),
    "COMEX pit-era open 8:20 ET": (8, 20),
    "CME Globex reopen 18:00 ET": (18, 0),
}
HORIZONS_MIN = (15, 30, 60, 120, 240)
TRAIN_FRAC = 0.70


def tag_regime_1h(k1h):
    out = {}
    for i in range(200, len(k1h)):
        out[int(k1h[i]["start_ms"])] = detect_regime(k1h[i - 200:i])["regime"]
    return out


def nearest_1h_regime(regime_by_1h, ts_ms, hour_ms=3600_000):
    return regime_by_1h.get((ts_ms // hour_ms) * hour_ms, "unknown")


def local_hm(ts_ms):
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).astimezone(NY)
    return dt.hour, dt.minute


def stats_line(label, vals):
    if not vals:
        print(f"    {label:<8}: n=0")
        return
    n = len(vals)
    mean = sum(vals) / n
    sd = st.stdev(vals) if n > 1 else 0.0
    pos = sum(1 for v in vals if v > 0) / n
    tstat = mean / (sd / n ** 0.5) if sd > 0 else 0.0
    flag = " <-- |t|>2" if abs(tstat) > 2 else ""
    print(f"    {label:<8}: n={n:>5}  mean={mean:>+6.3f}%  sd={sd:>5.2f}%  "
          f"%pos={pos*100:>4.1f}%  t={tstat:>+5.2f}{flag}")


def holdout_check(opens, k5, horizon_bars):
    rows = []
    for i in opens:
        j = i + horizon_bars
        if j < len(k5):
            rows.append((k5[i]["start_ms"], (k5[j]["close"] / k5[i]["close"] - 1) * 100))
    if len(rows) < 20:
        return
    ts_all = sorted(t for t, _ in rows)
    split = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    tr = [p for t, p in rows if t < split]
    ho = [p for t, p in rows if t >= split]

    def t_of(v):
        n = len(v)
        if n < 2:
            return 0.0
        m = sum(v) / n
        sd = st.stdev(v)
        return m / (sd / n ** 0.5) if sd > 0 else 0.0

    print(f"      train/holdout @ this horizon: TRAIN t={t_of(tr):+.2f} (n={len(tr)})  "
          f"HOLDOUT t={t_of(ho):+.2f} (n={len(ho)})")


def main():
    coin = sys.argv[1] if len(sys.argv) > 1 else "eth"
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    regime_by_1h = tag_regime_1h(k1h)

    for ev_name, (h, m) in EVENTS.items():
        opens = [i for i, c in enumerate(k5) if local_hm(c["start_ms"]) == (h, m)]
        print(f"\n=== {coin.upper()} — {ev_name} — {len(opens)} sessions (DST-aware) ===")
        best_t, best_h = 0.0, None
        for hz in HORIZONS_MIN:
            j_off = hz // 5
            fwd = []
            for i in opens:
                j = i + j_off
                if j < len(k5):
                    fwd.append((k5[j]["close"] / k5[i]["close"] - 1) * 100)
            stats_line(f"+{hz}m", fwd)
            n = len(fwd)
            if n > 1:
                mean = sum(fwd) / n
                sd = st.stdev(fwd)
                t = mean / (sd / n ** 0.5) if sd > 0 else 0.0
                if abs(t) > abs(best_t):
                    best_t, best_h = t, hz
        if best_h is not None and abs(best_t) > 2:
            holdout_check(opens, k5, best_h // 5)


if __name__ == "__main__":
    main()

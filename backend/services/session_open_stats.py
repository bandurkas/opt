"""Pure price-statistics check on session opens — descriptive only, no options
mechanics (no TP/SL, no theta, no spread). The prior session_open_backtest.py
result (negative even on train) could be the options wrapper drowning a real
but small underlying edge; this isolates the raw ETH return around session
opens to see if there's anything there at all before any strategy is built.

For each session open (Asia 00:00 UTC, US 13:30 UTC), report forward return
distribution at several horizons, plus the correlation between the pre-open
move and the post-open move (mean-reversion vs continuation), split overall
and by `transition`-regime tag.

Run:
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/session_open_stats.py
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.local_optimizer import find_data_dir
from services.multi_coin_signals import load_coin
from services.regime import detect_regime

SESSIONS = {"Asia 00:00 UTC": 0, "US 13:30 UTC": 13 * 60 + 30}
PRE_LOOKBACK_MIN = 4 * 60   # 4h before open
HORIZONS_MIN = (15, 30, 60, 120, 240)


def tag_regime_1h(k1h):
    out = {}
    for i in range(200, len(k1h)):
        out[int(k1h[i]["start_ms"])] = detect_regime(k1h[i - 200:i])["regime"]
    return out


def nearest_1h_regime(regime_by_1h, ts_ms, hour_ms=3600_000):
    return regime_by_1h.get((ts_ms // hour_ms) * hour_ms, "unknown")


def stats_line(label, vals):
    if not vals:
        print(f"    {label:<8}: n=0")
        return
    n = len(vals)
    mean = sum(vals) / n
    median = st.median(vals)
    sd = st.stdev(vals) if n > 1 else 0.0
    pos = sum(1 for v in vals if v > 0) / n
    tstat = mean / (sd / n ** 0.5) if sd > 0 else 0.0
    print(f"    {label:<8}: n={n:>5}  mean={mean:>+6.3f}%  median={median:>+6.3f}%  "
          f"sd={sd:>5.2f}%  %pos={pos*100:>4.1f}%  t={tstat:>+5.2f}")


def main():
    coin = sys.argv[1] if len(sys.argv) > 1 else "eth"
    k5, k15, k1h = load_coin(coin, find_data_dir(None))
    regime_by_1h = tag_regime_1h(k1h)
    bars_pre = PRE_LOOKBACK_MIN // 5

    for sess_name, open_min in SESSIONS.items():
        opens = [i for i, c in enumerate(k5)
                  if (c["start_ms"] // 60_000) % 1440 == open_min and i >= bars_pre]
        print(f"\n=== {sess_name} — {len(opens)} sessions ===")

        pre_moves, regimes = [], []
        for i in opens:
            p0, p1 = k5[i - bars_pre]["close"], k5[i]["close"]
            pre_moves.append((p1 / p0 - 1) * 100 if p0 else 0.0)
            regimes.append(nearest_1h_regime(regime_by_1h, k5[i]["start_ms"]))

        print("  forward return at open, ALL sessions:")
        for h in HORIZONS_MIN:
            j_off = h // 5
            fwd = []
            for i in opens:
                j = i + j_off
                if j < len(k5):
                    fwd.append((k5[j]["close"] / k5[i]["close"] - 1) * 100)
            stats_line(f"+{h}m", fwd)

        print("  forward return, TRANSITION-regime sessions only:")
        for h in HORIZONS_MIN:
            j_off = h // 5
            fwd = []
            for idx, i in enumerate(opens):
                if regimes[idx] != "transition":
                    continue
                j = i + j_off
                if j < len(k5):
                    fwd.append((k5[j]["close"] / k5[i]["close"] - 1) * 100)
            stats_line(f"+{h}m", fwd)

        # pre-move vs post-move correlation (mean reversion vs continuation)
        post_1h = []
        pre_for_corr = []
        for idx, i in enumerate(opens):
            j = i + 12
            if j < len(k5):
                post_1h.append((k5[j]["close"] / k5[i]["close"] - 1) * 100)
                pre_for_corr.append(pre_moves[idx])
        if len(post_1h) > 2:
            corr = st.correlation(pre_for_corr, post_1h)
            print(f"  corr(pre-open 4h move, post-open 1h move) = {corr:+.3f}  "
                  f"(n={len(post_1h)}; negative=mean-reversion, positive=continuation)")


if __name__ == "__main__":
    main()

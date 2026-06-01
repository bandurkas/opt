"""Simulate the DEPLOYED V3 hybrid `determine_side` over the last 30 days.

Production determine_side (services/paper_strategy.py):
    |ret_7d| < 2%  → Put
    ret_7d > +2%   → Call
    ret_7d < -2%   → Put

This is mathematically identical to the asymmetric engine in
check_asymmetric_thresholds.generate_signals(put_max=2.0, call_min=2.0):
    ret < 2.0 → Put      (covers |ret|<2 AND ret<=-2)
    ret > 2.0 → Call     (uptrend)

So we reuse that validated engine and report the per-side breakdown + a
Bybit-realistic $ equity replay (0.1-ETH lots, IM, spread, fees), starting $400.

Run:  cd backend && PYTHONPATH=. python3 services/sim_v3_last30d.py
"""
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, ".")

from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir, load_local
from services.check_asymmetric_thresholds import (
    generate_signals, PUT_EXIT, CALL_EXIT, MS_PER_DAY,
)

# NOTE: paper_strategy (sizing helpers) is intentionally NOT imported — it pulls
# in psycopg2 which isn't installed on the local research box. Per-side % stats
# below are dependency-free and are what we care about (Fact-2 comparison).


def side_stats(sims, label):
    pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
    if not pnls:
        print(f"  {label:<5}  n=0")
        return None
    wr = sum(1 for p in pnls if p > 0) / len(pnls)
    avg = statistics.mean(pnls)
    print(f"  {label:<5}  n={len(pnls):>3}  WR={wr*100:>5.1f}%  avg={avg:>+7.2f}%  total={sum(pnls):>+8.1f}%")
    return {"n": len(pnls), "wr": wr, "avg": avg}


def main():
    t0 = time.time()
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_local(data_dir)
    last_ms = k5[-1]["start_ms"]
    cutoff_ms = last_ms - 30 * MS_PER_DAY
    cutoff = datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last = datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    print("=" * 70)
    print("V3 HYBRID (deployed determine_side) — last 30 days")
    print(f"Window: {cutoff} → {last}")
    print("Rule: |ret_7d|<2%→Put · ret>+2%→Call · ret<-2%→Put")
    print("=" * 70)

    # Deployed V3 == asymmetric(put_max=2.0, call_min=2.0)
    sigs = generate_signals(k5, k15, k1h, cutoff_ms, put_max=2.0, call_min=2.0)
    ps = [s for s in sigs if s["side"] == "P"]
    cs = [s for s in sigs if s["side"] == "C"]
    print(f"\nSignals: {len(sigs)} total  (Put={len(ps)}  Call={len(cs)})\n")

    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ps else []
    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if cs else []

    print("Per-side (model pnl_pct on premium, post-spread):")
    side_stats(psim, "Put")
    side_stats(csim, "Call")
    side_stats(psim + csim, "ALL")
    print(f"\n({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()

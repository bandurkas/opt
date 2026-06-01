"""Simulate the DEPLOYED Config B (asymmetric) over the last 30 days.

Config B:
    ret < -2.5% → Put
    ret > +1.0% → Call
    -2.5%..+1.0% → dead zone (skip)

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
    print("CONFIG B (asymmetric) — last 30 days")
    print(f"Window: {cutoff} → {last}")
    print("Rule: ret<-2.5%→Put · ret>+1.0%→Call · dead zone: skip")
    print("=" * 70)

    # Config B == asymmetric(put_max=-2.5, call_min=1.0)
    sigs = generate_signals(k5, k15, k1h, cutoff_ms, put_max=-2.5, call_min=1.0)
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

    # Compare
    print(f"\n{'='*70}")
    print("COMPARISON — last 30 days")
    print("  Old V3 (symmetric |ret|<2%):  n=54 WR=57.4% avg=-6.00%")
    all_pnls = [s["option"]["pnl_pct"] for s in psim+csim if "pnl_pct" in s.get("option",{})]
    if all_pnls:
        new_wr = sum(1 for p in all_pnls if p>0)/len(all_pnls)
        new_avg = statistics.mean(all_pnls)
        print(f"  Config B (asymmetric -2.5%/+1%): n={len(all_pnls)} WR={new_wr*100:.1f}% avg={new_avg:+.2f}%")
    print(f"\n({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    main()

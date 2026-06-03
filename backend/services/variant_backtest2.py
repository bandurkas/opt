"""Confirm harness vs documented validation + check V3 under validated exits.

Reuses generate()/stats() from variant_backtest but simulates with the
DOCUMENTED exits from hybrid_backtest_v2.main() (which produced +7.09%):
  put_exit  = tp1 .50 tp2 .70 sl 1.50 hold 96
  call_exit = tp1 .30 tp2 .50 sl .50  hold 24
"""
from __future__ import annotations
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.backtest import simulate_signal_set
from services.local_optimizer import find_data_dir, load_local
from services.variant_backtest import generate, stats

PUT_EX = {"tp1": 0.50, "tp2": 0.70, "sl": 1.50, "hold": 96}
CALL_EX = {"tp1": 0.30, "tp2": 0.50, "sl": 0.50, "hold": 24}


def sim_set_docexits(sigs, k5):
    p = [s for s in sigs if s["side"] == "P"]
    c = [s for s in sigs if s["side"] == "C"]
    ps = simulate_signal_set(p, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=PUT_EX["tp1"], tp2_pct=PUT_EX["tp2"], sl_pct=PUT_EX["sl"],
            option_horizon_h=PUT_EX["hold"], spread_pct=2.0) if p else []
    cs = simulate_signal_set(c, k5, sigma=0.6, expiry_hours=168.0,
            tp1_pct=CALL_EX["tp1"], tp2_pct=CALL_EX["tp2"], sl_pct=CALL_EX["sl"],
            option_horizon_h=CALL_EX["hold"], spread_pct=2.0) if c else []
    return ps + cs


def main():
    t0 = time.time()
    k5, k15, k1h = load_local(find_data_dir(None))
    print("=== DOCUMENTED exits (validated set) ===\n", flush=True)
    print(f"{'Config':<22} {'n':>5} {'WR':>6} {'avg':>8} {'sharpe':>7} {'total':>10} {'maxCL':>6} {'losM':>5}")
    print("-" * 78)
    for v, lbl in [("baseline", "Baseline (V2)"), ("v3", "V3 ADX trend>35")]:
        sigs = generate(k5, k15, k1h, variant=v)
        st = stats(sim_set_docexits(sigs, k5))
        print(f"{lbl:<22} {st['n']:>5} {st['wr']*100:>5.1f}% {st['avg']:>+7.2f}% "
              f"{st['sharpe']:>+6.2f} {st['total']:>+9.1f}% {st['mc']:>6} {st['lm']:>5}", flush=True)
        for sd in ("P", "C"):
            if sd in st["by_side"]:
                b = st["by_side"][sd]
                print(f"    {'Put' if sd=='P' else 'Call':<18} {b['n']:>5} {b['wr']*100:>5.1f}% {b['avg']:>+7.2f}%", flush=True)
    print(f"\nelapsed {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()

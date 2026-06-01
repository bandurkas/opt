"""Baseline: current LIVE strategy on full 365d with monthly breakdown."""
import sys, statistics, json
sys.path.insert(0, ".")
from services.local_optimizer import find_data_dir, get_full_signals, load_local
from services.backtest import simulate_signal_set
from services.strategy_config import LIVE_GEN_KWARGS, LIVE_EXIT
from datetime import datetime, timezone

print("Loading data...", flush=True)
k5, k15, k1h = load_local(find_data_dir(None))
span_start = datetime.fromtimestamp(k5[0]["start_ms"]/1000).strftime("%Y-%m-%d %H:%M")
span_end = datetime.fromtimestamp(k5[-1]["start_ms"]/1000).strftime("%Y-%m-%d %H:%M")
print(f"5m bars: {len(k5):,} | 15m: {len(k15):,} | 1h: {len(k1h):,}")
print(f"Span: {span_start} → {span_end}")

sigs = get_full_signals(k5, k15, k1h, LIVE_GEN_KWARGS)
print(f"Signals (full 365d): {len(sigs)}")

sims = simulate_signal_set(
    sigs, k5, sigma=0.6, expiry_hours=168.0,
    tp1_pct=LIVE_EXIT["tp1_pct"], tp2_pct=LIVE_EXIT["tp2_pct"],
    sl_pct=LIVE_EXIT["sl_pct"], option_horizon_h=LIVE_EXIT["hold_h"],
    spread_pct=2.0,
)
pnls = [s["option"]["pnl_pct"] for s in sims if "pnl_pct" in s.get("option", {})]
wr = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0
avg = statistics.mean(pnls) if pnls else 0
stdev = statistics.stdev(pnls) if len(pnls) > 1 else 0
sh = (avg / stdev) if stdev > 0 else 0

print(f"\n{'='*70}")
print(f"LIVE baseline: P mtf_up · cd=4 · hold=96h · vol≥0.50 · regime=range")
print(f"{'='*70}")
print(f"n={len(pnls)}  WR={wr*100:.1f}%  avg={avg:+.2f}%  stdev={stdev:.2f}%  sharpe={sh:.3f}  total={sum(pnls):+.1f}%")

by_month = {}
for s in sims:
    opt = s.get("option") or {}
    if "pnl_pct" not in opt: continue
    ts = datetime.fromtimestamp(s["ts_ms"]/1000, tz=timezone.utc)
    key = ts.strftime("%Y-%m")
    by_month.setdefault(key, []).append(opt["pnl_pct"])

print(f"\n{'Month':<10} {'n':>4} {'WR':>6} {'avg':>8} {'median':>8} {'sharpe':>8}")
print("-" * 52)
for m in sorted(by_month):
    ps = by_month[m]
    m_wr = sum(1 for p in ps if p > 0) / len(ps)
    m_avg = statistics.mean(ps)
    m_med = statistics.median(ps)
    m_std = statistics.stdev(ps) if len(ps) > 1 else 0
    m_sh = (m_avg / m_std) if m_std > 0 else 0
    print(f"  {m}: n={len(ps):3d}  WR={m_wr*100:5.1f}%  avg={m_avg:+7.2f}%  med={m_med:+6.2f}%  sh={m_sh:+.3f}")

losing = [p for p in pnls if p < 0]
winning = [p for p in pnls if p > 0]
print(f"\nWins: n={len(winning)}, avg={statistics.mean(winning):+.2f}%")
print(f"Losses: n={len(losing)}, avg={statistics.mean(losing):+.2f}%")
print(f"Worst: {min(pnls):+.2f}% | Best: {max(pnls):+.2f}%")
print(f"Consecutive losses: ", end="")
max_cl = cl = 0
for p in pnls:
    if p < 0: cl += 1; max_cl = max(max_cl, cl)
    else: cl = 0
print(f"{max_cl}")

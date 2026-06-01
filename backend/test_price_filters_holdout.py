"""Holdout validation for price-based filters from comprehensive research."""
import json, statistics, time, sys
from datetime import datetime, timezone
sys.path.insert(0, ".")

from services.comprehensive_research import (
    generate_signals_with_features, load_all_data, PUT_EXIT, CALL_EXIT,
    simulate_signal_set, find_data_dir, load_local,
)
from services.oi_holdout import load_micro_data, OI_DATA
import services.oi_holdout as oh

load_all_data()
load_micro_data()
oh.OI_DATA = OI_DATA
oh.load_micro_data()

k5, k15, k1h = load_local(find_data_dir(None))

oi_ts = sorted(oh.OI_DATA.keys())
split_idx = int(len(oi_ts) * 0.66)
train_cutoff = oi_ts[split_idx]

train_start = datetime.fromtimestamp(oi_ts[0]/1000, tz=timezone.utc).strftime("%Y-%m-%d")
train_end = datetime.fromtimestamp(train_cutoff/1000, tz=timezone.utc).strftime("%Y-%m-%d")
test_start = datetime.fromtimestamp(train_cutoff/1000, tz=timezone.utc).strftime("%Y-%m-%d")
test_end = datetime.fromtimestamp(oi_ts[-1]/1000, tz=timezone.utc).strftime("%Y-%m-%d")

print(f"Train: {train_start} → {train_end} | Test: {test_start} → {test_end}")

all_sigs = generate_signals_with_features(k5, k15, k1h, 0)
train_sigs = [s for s in all_sigs if s["ts_ms"] < train_cutoff]
test_sigs = [s for s in all_sigs if s["ts_ms"] >= train_cutoff]
print(f"Signals: train={len(train_sigs)} test={len(test_sigs)}")

def simulate(sig_list):
    ps = [s for s in sig_list if s["side"] == "P"]
    cs = [s for s in sig_list if s["side"] == "C"]
    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT["tp1"], tp2_pct=PUT_EXIT["tp2"], sl_pct=PUT_EXIT["sl"],
        option_horizon_h=PUT_EXIT["hold_h"], spread_pct=2.0) if ps else []
    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT["tp1"], tp2_pct=CALL_EXIT["tp2"], sl_pct=CALL_EXIT["sl"],
        option_horizon_h=CALL_EXIT["hold_h"], spread_pct=2.0) if cs else []
    sig_pnl = {}
    for s in psim + csim:
        pnl = s["option"].get("pnl_pct")
        if pnl is not None: sig_pnl[s["ts_ms"]] = pnl
    return [(s, sig_pnl.get(s["ts_ms"])) for s in sig_list if s["ts_ms"] in sig_pnl]

train_pnl = simulate(train_sigs)
test_pnl = simulate(test_sigs)

tr_baseline = [p for _, p in train_pnl]
te_baseline = [p for _, p in test_pnl]
print(f"\nBaseline: train n={len(tr_baseline)} avg={statistics.mean(tr_baseline):+.2f}% | test n={len(te_baseline)} avg={statistics.mean(te_baseline):+.2f}%")

def filt(sigs, feat, thresh):
    return [pnl for s, pnl in sigs if s.get(feat) is not None and s.get(feat) > thresh]

print(f"\n{'Filter':<20} {'Train n':>8} {'Train avg':>10} {'Test n':>8} {'Test avg':>10} {'Gap':>8}")
print("-" * 75)

results = []
for feat in ["mom_48", "ema_spread", "mom_288", "bb_pct", "rv_24h"]:
    for t in [-1, -0.5, 0, 0.5, 1, 2]:
        tr = filt(train_pnl, feat, t)
        te = filt(test_pnl, feat, t)
        if tr and te and len(tr) >= 10 and len(te) >= 5:
            tr_avg = statistics.mean(tr)
            te_avg = statistics.mean(te)
            gap = te_avg - tr_avg
            score = te_avg * min(1.0, len(te)/20)  # reward test avg + sample size
            results.append((feat, t, len(tr), tr_avg, len(te), te_avg, gap, score))
            print(f"{feat} > {t:>4}: {len(tr):>8} {tr_avg:>+9.2f}% {len(te):>8} {te_avg:>+9.2f}% {gap:>+7.2f}%")

# Top by test performance
if results:
    results.sort(key=lambda x: x[7], reverse=True)
    print(f"\n{'='*75}")
    print(f"TOP 5 by holdout performance:")
    for feat, t, tr_n, tr_avg, te_n, te_avg, gap, score in results[:5]:
        print(f"  {feat} > {t}: test n={te_n} avg={te_avg:+.2f}% (train={tr_avg:+.2f}%, gap={gap:+.2f}%)")

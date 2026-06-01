"""Quick holdout test for OI_24h filter."""
import json, statistics
from services.oi_holdout import *
from services.research_microstructure import load_data, OI_DATA, FR_DATA

# Fix: load data into oi_holdout's OI_DATA too
load_data()
# Copy to oi_holdout namespace
import services.oi_holdout as oh
oh.OI_DATA = OI_DATA
oh.FR_DATA = FR_DATA

# Now reload with fixed data
oh.load_micro_data()

k5, k15, k1h = load_local(find_data_dir(None))

oi_ts = sorted(oh.OI_DATA.keys())
if not oi_ts:
    print("ERROR: OI_DATA still empty after load!")
    exit(1)
    
split_idx = int(len(oi_ts) * 0.66)
train_cutoff = oi_ts[split_idx]

all_sigs = gen_signals_with_oi(k5, k15, k1h, 0)
train_sigs = [s for s in all_sigs if s['ts_ms'] < train_cutoff]
test_sigs = [s for s in all_sigs if s['ts_ms'] >= train_cutoff]

def simulate(sig_list):
    ps = [s for s in sig_list if s['side'] == 'P']
    cs = [s for s in sig_list if s['side'] == 'C']
    psim = simulate_signal_set(ps, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=PUT_EXIT['tp1'], tp2_pct=PUT_EXIT['tp2'], sl_pct=PUT_EXIT['sl'],
        option_horizon_h=PUT_EXIT['hold_h'], spread_pct=2.0) if ps else []
    csim = simulate_signal_set(cs, k5, sigma=0.6, expiry_hours=168.0,
        tp1_pct=CALL_EXIT['tp1'], tp2_pct=CALL_EXIT['tp2'], sl_pct=CALL_EXIT['sl'],
        option_horizon_h=CALL_EXIT['hold_h'], spread_pct=2.0) if cs else []
    sig_pnl = {}
    for s in psim + csim:
        pnl = s['option'].get('pnl_pct')
        if pnl is not None: sig_pnl[s['ts_ms']] = pnl
    return [(s, sig_pnl.get(s['ts_ms'])) for s in sig_list if s['ts_ms'] in sig_pnl]

train_with_pnl = simulate(train_sigs)
test_with_pnl = simulate(test_sigs)

def oi_filter(sigs, field, thresh):
    return [pnl for sig, pnl in sigs if sig.get(field) is not None and sig.get(field) > thresh]

print(f'\nBaseline:')
tr_pnls = [p for _, p in train_with_pnl]
te_pnls = [p for _, p in test_with_pnl]
print(f'  Train: n={len(tr_pnls)} avg={statistics.mean(tr_pnls):+.2f}%')
print(f'  Test:  n={len(te_pnls)} avg={statistics.mean(te_pnls):+.2f}%')
print(f'\nOI filters:')
for field in ['oi_4h', 'oi_12h', 'oi_24h']:
    for thresh in [-0.02, -0.01, 0]:
        tr = oi_filter(train_with_pnl, field, thresh)
        te = oi_filter(test_with_pnl, field, thresh)
        if tr and te:
            tr_avg = statistics.mean(tr)
            te_avg = statistics.mean(te)
            print(f'{field} > {thresh:+.0%}:  train n={len(tr):>3} avg={tr_avg:+.2f}%  test n={len(te):>3} avg={te_avg:+.2f}%  gap={te_avg-tr_avg:+.2f}%')

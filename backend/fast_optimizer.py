"""
FAST Final Optimizer — applies all Stage-2 insights as fixed params,
sweeps only the remaining unknowns: expiry, tp1, tp2, sl, tsl.
~300 combos, runs in <2 min.

Fixed from Stage-2:
  - sigma=0.65 (best IV)
  - accel=False filter (better P&L)
  - score<=8 (exclude overheated)
  - regime=trend only
  - ATM options (OTM worse)
  - MTF=2/3, Puts, Fade, CD=6
"""
import sys, os, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services import backtest_bs as bs
from services.backtest import generate_raw_signals
from services.backtest_data import fetch_set
from datetime import datetime, timezone

SIGMA = 0.65
SPREAD = 2.0

def simulate(signal, klines_5m, expiry_h, tp1, tp2, sl, tsl_t, tsl_d):
    start_ms = signal["ts_ms"]
    side = "P"
    spot = signal["close"]
    strike = round(spot / 25) * 25
    T0 = expiry_h / (24.0 * 365.0)
    mid = bs.price(side, spot, strike, T0, SIGMA)
    if mid <= 0.01: return None
    hs = SPREAD / 200.0
    entry = mid * (1 + hs)
    tp1_p = entry * (1 + tp1) / (1 - hs)
    tp2_p = entry * (1 + tp2) / (1 - hs)
    tsl_floor = entry * (1 - sl) / (1 - hs)
    path = [c for c in klines_5m if start_ms < c["start_ms"] <= start_ms + int(expiry_h * 3600000)]
    if not path: return None
    pos1, pos2, e1, e2 = True, True, 0.0, 0.0
    for i, c in enumerate(path):
        T = max(0.0, (expiry_h - (i+1)*5/60) / (24*365))
        hi_p = bs.price(side, c["low"],  strike, T, SIGMA)
        lo_p = bs.price(side, c["high"], strike, T, SIGMA)
        hi_pnl = (hi_p*(1-hs) - entry) / entry
        if tsl_t > 0 and hi_pnl >= tsl_t:
            nf = entry*(1+hi_pnl-tsl_d)/(1-hs)
            if nf > tsl_floor: tsl_floor = nf
        if lo_p <= tsl_floor:
            ep = (tsl_floor*(1-hs)-entry)/entry
            if pos1: e1=ep
            if pos2: e2=ep
            return ((e1+e2)/2)*100
        if pos2 and hi_p >= tp2_p: e2=(tp2_p*(1-hs)-entry)/entry; pos2=False
        if pos1 and hi_p >= tp1_p: e1=(tp1_p*(1-hs)-entry)/entry; pos1=False
        if not pos1 and not pos2: break
    if pos1 or pos2:
        T_l = max(0,(expiry_h-len(path)*5/60)/(24*365))
        fp=(bs.price(side,path[-1]["close"],strike,T_l,SIGMA)*(1-hs)-entry)/entry
        if pos1: e1=fp
        if pos2: e2=fp
    return ((e1+e2)/2)*100

def main():
    print("Fetching data...", flush=True)
    data = fetch_set("ETHUSDT", days=60, intervals=("5","15","60"))
    k5,k15,k1h = data["5"],data["15"],data["60"]

    all_sigs = generate_raw_signals(k5,k15,k1h, min_alignment=2, cooldown_bars=6, fade=True)
    # Apply all Stage-2 fixed filters
    sigs = [s for s in all_sigs
            if s["side"]=="P"
            and s["regime"]=="trend"
            and s["mtf_aligned"]==2
            and s["score"]<=8
            and not s.get("accelerating", True)]
    print(f"Signal pool after all filters: {len(sigs)} signals ({len(sigs)/60:.1f}/day)", flush=True)

    # Hour filter experiments from mega_optimizer findings
    hour_pools = {
        "all":   sigs,
        "day":   [s for s in sigs if 8 <= datetime.fromtimestamp(s["ts_ms"]/1000,tz=timezone.utc).hour < 20],
        "night": [s for s in sigs if datetime.fromtimestamp(s["ts_ms"]/1000,tz=timezone.utc).hour < 8
                                     or datetime.fromtimestamp(s["ts_ms"]/1000,tz=timezone.utc).hour >= 20],
    }

    # Sweep
    expiries = [72.0, 96.0, 120.0, 144.0]
    tp1s     = [0.15, 0.20, 0.25, 0.30]
    tp2s     = [0.40, 0.50, 0.60, 0.70]
    sls      = [0.30, 0.35, 0.40, 0.45]
    tsls     = [(0.0,0.0),(0.20,0.15),(0.25,0.20),(0.30,0.15),(0.30,0.20)]

    total = len(expiries)*len(tp1s)*len(tp2s)*len(sls)*len(tsls)*len(hour_pools)
    print(f"Sweeping {total} combinations...\n", flush=True)

    results = []
    for hours_label, pool in hour_pools.items():
        if len(pool) < 20: continue
        for expiry,tp1,tp2,sl,(tsl_t,tsl_d) in itertools.product(expiries,tp1s,tp2s,sls,tsls):
            if tp2<=tp1: continue
            pnls=[p for s in pool if (p:=simulate(s,k5,expiry,tp1,tp2,sl,tsl_t,tsl_d)) is not None]
            if len(pnls)<20: continue
            wr=sum(1 for p in pnls if p>0)/len(pnls)
            avg=sum(pnls)/len(pnls)
            results.append(dict(hours=hours_label,exp=expiry,tp1=tp1,tp2=tp2,sl=sl,
                                tsl_t=tsl_t,tsl_d=tsl_d,n=len(pnls),
                                per_day=len(pnls)/60,wr=wr,avg=avg,ev=avg*len(pnls)/60))

    # --- Report ---
    hdr = f"{'HRS':>6} {'EXP':>4} {'TP1':>4} {'TP2':>4} {'SL':>4} {'TSL_T':>5} {'TSL_D':>5} | {'N/d':>4} {'WR':>5} {'P&L':>7}"
    sep = "-"*70

    top_pnl = sorted([r for r in results if 2.0<=r["per_day"]<=7.0], key=lambda x:-x["avg"])[:15]
    print("="*70)
    print("TOP 15 BY AVG P&L PER TRADE  (2-7 trades/day)")
    print("="*70)
    print(hdr); print(sep)
    for r in top_pnl:
        print(f"{r['hours']:>6} {r['exp']:>4.0f} {r['tp1']:>4.2f} {r['tp2']:>4.2f} {r['sl']:>4.2f} "
              f"{r['tsl_t']:>5.2f} {r['tsl_d']:>5.2f} | {r['per_day']:>4.1f} {r['wr']*100:>4.1f}% {r['avg']:>+7.2f}%")

    top_ev = sorted([r for r in results if 2.0<=r["per_day"]<=7.0], key=lambda x:-x["ev"])[:15]
    print(f"\n{'='*70}")
    print("TOP 15 BY EV/DAY = avg_pnl × trades_per_day  (2-7 trades/day)")
    print("="*70)
    print(hdr+f" {'EV/d':>6}"); print(sep+"-"*7)
    for r in top_ev:
        print(f"{r['hours']:>6} {r['exp']:>4.0f} {r['tp1']:>4.2f} {r['tp2']:>4.2f} {r['sl']:>4.2f} "
              f"{r['tsl_t']:>5.2f} {r['tsl_d']:>5.2f} | {r['per_day']:>4.1f} {r['wr']*100:>4.1f}% {r['avg']:>+7.2f}% {r['ev']:>+6.2f}")

    if results:
        best=max(results,key=lambda x:x["avg"])
        print(f"\n🏆 ABSOLUTE BEST: {best['avg']:+.2f}% avg | WR={best['wr']*100:.1f}% | {best['per_day']:.1f}/day")
        print(f"   EXP={best['exp']:.0f}h TP1={best['tp1']:.2f} TP2={best['tp2']:.2f} SL={best['sl']:.2f} "
              f"TSL={best['tsl_t']:.2f}/{best['tsl_d']:.2f} HOURS={best['hours']}")

if __name__=="__main__": main()

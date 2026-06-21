"""Gold (XAUT) DAILY IRON BUTTERFLY — defined-risk version of the periodic short
straddle, sized to fit a REAL $2000 IBKR account.

WHY: gold_strangle_backtest.py found a promising 24h-cadence short ATM straddle
(TRAIN +14.65%/cycle -> HOLDOUT +7.89%/cycle, no negative months incl. the crash).
But Reg-T NAKED margin on GLD (spot ~$387, 100x multiplier) is ~$8,000+ per
straddle -- a $2000 account can't open even one. Buying OTM wings (iron butterfly)
caps the margin to wing_width*100 - net_credit, which DOES fit $2000.

This harness prices all 4 legs directly via Black-Scholes (backtest_bs.price), not
through the shared short_premium engine (which only handles single naked legs), and
walks the REAL 5m XAUT-perp path forward to either expiry (intrinsic settlement) or
an early profit-take.

STDLIB ONLY. Run:  python3 backend/services/gold_iron_butterfly_backtest.py [wing_W]
"""
from __future__ import annotations

import math
import statistics as st
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backtest_bs import price  # noqa: E402
from services.local_optimizer import find_data_dir  # noqa: E402
from services.multi_coin_signals import load_coin  # noqa: E402

COIN = "xaut"
CYCLE_H = 24.0
STRIKE_STEP = 5.0          # real GLD chain strike spacing near ATM
# XAUT trades as raw spot gold ($3251-5598/oz over our dataset); GLD is a ~1/10oz
# share (~$387 when spot was ~$4300 on 2026-06-20 measurement day). $5 strikes only
# make sense on the GLD SHARE scale, so convert XAUT->GLD-equivalent before pricing.
# Ratio is scale-invariant for sigma (log-returns identical), only affects $ levels.
GLD_RATIO = 4301.4 / 387.12  # XAUT close on measurement day / real GLD spot same day
WING_W = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
SIGMA_CLAMP = (0.18, 0.32)
IV_RV_MULT = 1.05
TAKE_PROFIT_FRAC = float(sys.argv[2]) if len(sys.argv) > 2 else 0.50  # close early at this frac of max credit captured
TRAIN_FRAC = 0.70
ACCOUNT_USD = 2000.0
MARGIN_UTIL = 0.80         # don't deploy 100% of account to one underlying

# REAL bid/ask spread measured live off the CBOE GLD chain (gld_chain_probe.py,
# 2026-06-20, 1-5 DTE ATM contracts -- the actual tenor this strategy trades, NOT
# the far-dated 20-75 DTE numbers from the original liquidity probe). Round-trip
# spread as % of mid: calls ~9-11%, puts ~9-22% (puts widen sharply at the very
# shortest tenor). Applied as a HALF-spread give-up on each leg, each side of the
# trade (sell legs at bid, buy legs at ask) -- i.e. the model now prices 4 real
# fills, not 4 theoretical BS mids.
SPREAD_RT_CALL = 0.10      # round-trip spread, % of mid, calls
SPREAD_RT_PUT = 0.18       # round-trip spread, % of mid, puts (wider, esp. short-dated)
INCLUDE_SPREAD = (sys.argv[3] != "0") if len(sys.argv) > 3 else True


def mo(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m")


def agg(pnls):
    if not pnls:
        return "  n   0"
    n = len(pnls)
    avg = sum(pnls) / n
    wr = sum(1 for p in pnls if p > 0) / n
    sd = st.stdev(pnls) if n > 1 else 0.0
    sh = avg / sd if sd > 0 else 0.0
    return f"n{n:>4} avg{avg:>+6.2f}% WR{wr*100:>3.0f}% Sh{sh:>+5.2f}"


def realized_vol_168h(k1h: list[dict], idx_1h: int) -> float | None:
    lb = 168
    if idx_1h < lb + 1:
        return None
    w = [k1h[i]["close"] for i in range(idx_1h - lb, idx_1h + 1)]
    rets = [math.log(w[i] / w[i - 1]) for i in range(1, len(w)) if w[i - 1] > 0]
    if len(rets) < 10:
        return None
    m = sum(rets) / len(rets)
    var = sum((x - m) ** 2 for x in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(24 * 365)


def combo_value(S: float, K: float, W: float, T: float, sigma: float) -> float:
    """Theoretical mid-price net value of the short iron-butterfly combo (short
    straddle @K, long call K+W, long put K-W). No spread -- used only for the
    expiry/intrinsic settlement case, where there's no bid/ask (cash-settled)."""
    short_c = price("C", S, K, T, sigma)
    short_p = price("P", S, K, T, sigma)
    long_c = price("C", S, K + W, T, sigma)
    long_p = price("P", S, K - W, T, sigma)
    return (short_c + short_p) - (long_c + long_p)


def combo_entry_credit_fill(S: float, K: float, W: float, T: float, sigma: float) -> float:
    """Real fill to OPEN the combo: sell short legs at BID, buy wing legs at ASK.
    Each leg loses half its round-trip spread vs the theoretical mid."""
    short_c = price("C", S, K, T, sigma)
    short_p = price("P", S, K, T, sigma)
    long_c = price("C", S, K + W, T, sigma)
    long_p = price("P", S, K - W, T, sigma)
    if not INCLUDE_SPREAD:
        return (short_c + short_p) - (long_c + long_p)
    short_c_bid = short_c * (1 - SPREAD_RT_CALL / 2)
    short_p_bid = short_p * (1 - SPREAD_RT_PUT / 2)
    long_c_ask = long_c * (1 + SPREAD_RT_CALL / 2)
    long_p_ask = long_p * (1 + SPREAD_RT_PUT / 2)
    return (short_c_bid + short_p_bid) - (long_c_ask + long_p_ask)


def combo_exit_cost_fill(S: float, K: float, W: float, T: float, sigma: float) -> float:
    """Real fill to CLOSE the combo early: buy back short legs at ASK, sell wing
    legs at BID. Each leg loses half its round-trip spread vs the theoretical mid."""
    short_c = price("C", S, K, T, sigma)
    short_p = price("P", S, K, T, sigma)
    long_c = price("C", S, K + W, T, sigma)
    long_p = price("P", S, K - W, T, sigma)
    if not INCLUDE_SPREAD:
        return (short_c + short_p) - (long_c + long_p)
    short_c_ask = short_c * (1 + SPREAD_RT_CALL / 2)
    short_p_ask = short_p * (1 + SPREAD_RT_PUT / 2)
    long_c_bid = long_c * (1 - SPREAD_RT_CALL / 2)
    long_p_bid = long_p * (1 - SPREAD_RT_PUT / 2)
    return (short_c_ask + short_p_ask) - (long_c_bid + long_p_bid)


def main():
    data_dir = find_data_dir(None)
    k5, k15, k1h = load_coin(COIN, data_dir)
    step_bars = int(CYCLE_H * 60 / 5)
    warmup = 200  # need 168h of 1h bars behind us

    # map 5m idx -> 1h idx for the RV lookup
    h1_ts = [c["start_ms"] for c in k1h]

    def idx_1h_at(ts_ms: int) -> int:
        # binary-search-free linear ok at this scale; data is small enough per run
        lo, hi = 0, len(h1_ts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if h1_ts[mid] <= ts_ms:
                lo = mid
            else:
                hi = mid - 1
        return lo

    rows = []  # (ts_ms, pnl_pct_of_margin, margin_usd, credit_usd)
    idx = warmup
    while idx + step_bars < len(k5):
        S0 = k5[idx]["close"] / GLD_RATIO  # GLD-share-equivalent scale
        ts0 = k5[idx]["start_ms"]
        K = round(S0 / STRIKE_STEP) * STRIKE_STEP
        i1h = idx_1h_at(ts0)
        rv = realized_vol_168h(k1h, i1h)
        if rv is None:
            idx += step_bars
            continue
        sigma = max(SIGMA_CLAMP[0], min(SIGMA_CLAMP[1], rv * IV_RV_MULT))
        T0 = CYCLE_H / 24.0 / 365.0
        entry_credit_mid = combo_value(S0, K, WING_W, T0, sigma)
        entry_credit = combo_entry_credit_fill(S0, K, WING_W, T0, sigma)
        margin = WING_W * 100 - entry_credit * 100
        if margin <= 1:  # degenerate (credit ~= width), skip
            idx += step_bars
            continue

        # TP threshold is judged against the MID combo value (what you'd "see" on
        # screen), but the threshold + the actual exit fill both use real bid/ask.
        tp_value_mid = entry_credit_mid * (1 - TAKE_PROFIT_FRAC)
        resolution = "expiry"
        exit_value = None
        for j in range(idx + 1, idx + step_bars + 1):
            if j >= len(k5):
                break
            St = k5[j]["close"] / GLD_RATIO
            T_rem = max(0.0, (idx + step_bars - j) * 5 / 60 / 24 / 365)
            cv_mid = combo_value(St, K, WING_W, T_rem, sigma)
            if cv_mid <= tp_value_mid:
                exit_value = combo_exit_cost_fill(St, K, WING_W, T_rem, sigma)
                resolution = "tp"
                break
        if exit_value is None:
            j_end = min(idx + step_bars, len(k5) - 1)
            S_end = k5[j_end]["close"] / GLD_RATIO
            # Cash-settled at expiry -- intrinsic value, no bid/ask spread to pay.
            exit_value = combo_value(S_end, K, WING_W, 0.0, sigma)

        pnl_usd = (entry_credit - exit_value) * 100  # short combo: profit = credit - buyback cost
        pnl_pct_margin = pnl_usd / margin * 100
        rows.append((ts0, pnl_pct_margin, margin, entry_credit * 100, resolution))
        idx += step_bars

    print(f"{COIN.upper()} DAILY IRON BUTTERFLY  wing=${WING_W}  cycle={CYCLE_H}h  "
          f"sigma_clamp={SIGMA_CLAMP}  TP={TAKE_PROFIT_FRAC*100:.0f}% of credit\n"
          f"(n={len(rows)} cycles)\n")

    margins = [m for _, _, m, _, _ in rows]
    credits = [c for _, _, _, c, _ in rows]
    print(f"avg margin/contract=${sum(margins)/len(margins):,.0f}  "
          f"avg credit/contract=${sum(credits)/len(credits):,.0f}\n")

    pnls = [p for _, p, _, _, _ in rows]
    print(f"=== % return on MARGIN (per contract, per cycle) ===")
    print(f"  overall: {agg(pnls)}")
    from collections import Counter
    res_ct = Counter(r for *_, r in rows)
    print(f"  resolutions: {dict(res_ct)}")

    by_mo: "OrderedDict[str, list]" = OrderedDict()
    for ts, p, *_ in rows:
        by_mo.setdefault(mo(ts), []).append(p)
    print("\n  per-month (% of margin):")
    for m in sorted(by_mo):
        print(f"    {m}: {agg(by_mo[m])}")

    ts_all = sorted(ts for ts, *_ in rows)
    split_ts = ts_all[0] + TRAIN_FRAC * (ts_all[-1] - ts_all[0])
    tr = [p for ts, p, *_ in rows if ts < split_ts]
    ho = [p for ts, p, *_ in rows if ts >= split_ts]
    print(f"\n  TRAIN(<{mo(int(split_ts))}) : {agg(tr)}")
    print(f"  HOLDOUT(>={mo(int(split_ts))}): {agg(ho)}")

    # ---- $2000 account simulation, swept across risk-per-cycle levels ----
    # MARGIN_UTIL=0.80 (naive full daily reinvestment) blows the account up via
    # over-betting: max single-cycle loss = -100% of deployed margin, and risking
    # 80% of capital on EVERY cycle eventually hits that tail and ruins the account
    # (classic Kelly-criterion violation), regardless of a decent average return.
    # Sweep more realistic risk-per-cycle fractions to find the sane operating point.
    for util in (0.80, 0.40, 0.20, 0.10, 0.05):
        capital = ACCOUNT_USD
        curve = []
        for ts, p, m, c, r in rows:
            nc = max(1, int((capital * util) // m)) if m > 0 else 1
            pnl_usd = (p / 100) * m * nc
            capital += pnl_usd
            capital = max(capital, 0.0)
            curve.append((ts, capital, pnl_usd, nc))
        start_cap, end_cap = ACCOUNT_USD, capital
        n_months = (curve[-1][0] - curve[0][0]) / (1000 * 3600 * 24 * 30.44)
        total_ret = (end_cap / start_cap - 1) * 100
        monthly_avg = (max(end_cap, 0.0001) / start_cap) ** (1 / n_months) * 100 - 100 if n_months > 0 else float("nan")
        peak = start_cap
        maxdd = 0.0
        for _, cap, _, _ in curve:
            peak = max(peak, cap)
            maxdd = max(maxdd, (peak - cap) / peak * 100) if peak > 0 else maxdd
        print(f"\n=== $ {ACCOUNT_USD:.0f} IBKR ACCOUNT SIM (risk={util*100:.0f}% of capital/cycle) ===")
        print(f"  start=${start_cap:,.0f}  end=${end_cap:,.0f}  over {n_months:.1f} months")
        print(f"  TOTAL return={total_ret:+.1f}%   COMPOUND avg/month={monthly_avg:+.2f}%   maxDD={maxdd:.1f}%")

    # ---- non-compounding (FIXED contracts, sized once off starting capital) ----
    avg_margin = sum(margins) / len(margins)
    nc_fixed = max(1, int((ACCOUNT_USD * MARGIN_UTIL) // avg_margin))
    capital = ACCOUNT_USD
    curve = []
    for ts, p, m, c, r in rows:
        pnl_usd = (p / 100) * m * nc_fixed
        capital += pnl_usd
        curve.append((ts, capital, pnl_usd, nc_fixed))
    start_cap, end_cap = ACCOUNT_USD, capital
    n_months = (curve[-1][0] - curve[0][0]) / (1000 * 3600 * 24 * 30.44)
    total_ret = (end_cap / start_cap - 1) * 100
    monthly_pnl_avg = (end_cap - start_cap) / n_months
    peak = start_cap
    maxdd = 0.0
    for _, cap, _, _ in curve:
        peak = max(peak, cap)
        maxdd = max(maxdd, (peak - cap) / peak * 100) if peak > 0 else maxdd
    print(f"\n=== $ {ACCOUNT_USD:.0f} FIXED {nc_fixed} contracts (no resizing, simple addition) ===")
    print(f"  start=${start_cap:,.0f}  end=${end_cap:,.0f}  over {n_months:.1f} months")
    print(f"  TOTAL return={total_ret:+.1f}%   AVG $/month={monthly_pnl_avg:+,.0f}$   maxDD={maxdd:.1f}%")

    by_mo_usd: "OrderedDict[str, float]" = OrderedDict()
    for ts, _, pnl_usd, _ in curve:
        by_mo_usd[mo(ts)] = by_mo_usd.get(mo(ts), 0.0) + pnl_usd
    print("\n  $ P&L per month (fixed contracts):")
    for m, v in by_mo_usd.items():
        print(f"    {m}: {v:+,.0f}$")


if __name__ == "__main__":
    main()

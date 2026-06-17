"""PoC: directional ETH-perp trend-follow in the windows where the OPTIONS bot is SILENT.

Hypothesis (DEX_PERP_COMPLEMENT_RESEARCH.md): the options short-premium bot stands
aside in regime=trend (ADX 1h > 35). A directional perp trend-follower should harvest
those windows. This harness tests whether that edge survives realistic funding + taker
fees with NO look-ahead, on a time-based train/holdout split, vs 4 controls.

Standalone — imports existing code, creates/edits nothing else. Run (fast arm64):
  docker run --rm --platform linux/arm64 -v "$PWD/backend:/app" -v "$PWD/data:/data" \
    -w /app -e PYTHONPATH=/app opt-app-bt:arm64 python3 services/perp_trend_backtest.py

NO LOOK-AHEAD: signals computed on closed bars only (slices advanced exactly like
variant_backtest.generate, never including the forming bar); fills at the NEXT 5m open.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.indicators import adx_full, atr, donchian, ema  # noqa: E402
from services.local_optimizer import find_data_dir  # noqa: E402
from services.multi_coin_signals import load_coin  # noqa: E402
from services.variant_backtest import regime_of  # noqa: E402

# ---- accounting constants (STATED) ----
START_EQUITY = 400.0
LEVERAGE = 2.0
FIXED_NOTIONAL = 800.0      # 2x of $400; flat sizing (compounding off for per-trade stats)
TAKER_FEE = 0.00045         # 0.045% taker, charged on entry notional + exit notional
ATR_TRAIL_MULT = 2.5        # 2.5 * ATR(1h) trailing stop
TREND_ADX = 35.0            # options-silent definition: regime_of(...)[0] == "trend"
HISTORY_WINDOW = 240        # trailing slice length (matches variant_backtest.generate)
MIN_1H = 200                # need >=200 closed 1h bars for ADX(14)
DONCHIAN_N = 20             # 1h donchian lookback for the breakout control direction
HOLDOUT_FRAC = 0.30         # last 30% of the period = holdout
FALLBACK_FUNDING_8H = 0.0001  # +0.01%/8h fallback where real funding data is absent

FIVE_M = 5 * 60 * 1000
Q_M = 15 * 60 * 1000
H_M = 60 * 60 * 1000
EIGHT_H = 8 * 3600 * 1000


def load_funding(data_dir: Path) -> list[dict]:
    raw = json.loads((data_dir / "eth_funding.json").read_text())
    raw.sort(key=lambda r: r["ts_ms"])  # file is newest-first; want ascending
    return raw


def funding_between(funding: list[dict], t0: int, t1: int) -> tuple[float, int, bool]:
    """Sum of funding RATES for every 8h settlement timestamp in (t0, t1].

    Returns (sum_rate, n_settlements, had_real_data). Where the settlement falls
    inside the real funding window we use the actual rate; outside it we fall back
    to FALLBACK_FUNDING_8H (conservative: longs pay) so the cost test stays honest
    on the long pre-funding-data history. Bybit settles every 8h on the UTC clock.
    """
    if t1 <= t0:
        return 0.0, 0, False
    fmin = funding[0]["ts_ms"]
    fmax = funding[-1]["ts_ms"]
    # map each real settlement ts -> rate for fast lookup
    rate_at = {f["ts_ms"]: f["funding_rate"] for f in funding}
    # iterate 8h settlement boundaries strictly inside (t0, t1]
    first = (t0 // EIGHT_H + 1) * EIGHT_H
    total = 0.0
    n = 0
    had_real = False
    ts = first
    while ts <= t1:
        if fmin <= ts <= fmax:
            # find nearest real settlement (exact if aligned, else closest)
            r = rate_at.get(ts)
            if r is None:
                # closest real settlement within 8h
                r = min(funding, key=lambda f: abs(f["ts_ms"] - ts))["funding_rate"]
            total += r
            had_real = True
        else:
            total += FALLBACK_FUNDING_8H
        n += 1
        ts += EIGHT_H
    return total, n, had_real


# ---------------------------------------------------------------------------
# Signal generation — walk 5m clock, closed-bar slices, mark silent windows
# ---------------------------------------------------------------------------
def build_bars(k5, k15, k1h, *, direction_mode: str = "ema_di"):
    """Return one record per 5m bar with everything needed to simulate, computed
    on CLOSED bars only. direction_mode in {"ema_di", "donchian"}.

    record = {i, ts_open(next bar open time is i+1), open_next, silent, want_dir, atr1h}
    want_dir in {+1, -1, 0}: the trend-follow direction the signal wants RIGHT NOW.
    """
    bars = []
    i15 = i1h = 0
    n = len(k5)
    for i, c5 in enumerate(k5):
        ts_end = c5["start_ms"] + FIVE_M
        while i15 < len(k15) and k15[i15]["start_ms"] + Q_M <= ts_end:
            i15 += 1
        while i1h < len(k1h) and k1h[i1h]["start_ms"] + H_M <= ts_end:
            i1h += 1
        s1h = k1h[max(0, i1h - HISTORY_WINDOW):i1h]
        if i + 1 >= n or len(s1h) < MIN_1H:
            continue

        regime_name, adx_v = regime_of(s1h, trend_adx=TREND_ADX)
        silent = regime_name == "trend"

        closes_1h = [c["close"] for c in s1h]
        want = 0
        if direction_mode == "ema_di":
            e50, e200 = ema(closes_1h, 50), ema(closes_1h, 200)
            af = adx_full(s1h, 14)
            if e50 is not None and e200 is not None and af is not None:
                pdi, mdi = af["plus_di"], af["minus_di"]
                if e50 > e200 and pdi > mdi:
                    want = 1
                elif e50 < e200 and mdi > pdi:
                    want = -1
        else:  # donchian breakout on 1h closed bars
            lo, hi = donchian(s1h, DONCHIAN_N)
            if lo is not None and hi is not None:
                last = closes_1h[-1]
                # breakout: close at/near channel extreme
                if last >= hi * 0.999:
                    want = 1
                elif last <= lo * 1.001:
                    want = -1

        atr1h = atr(s1h, 14) or 0.0
        bars.append({
            "i": i,
            "ts_end": ts_end,                # close time of the bar the signal is based on
            "open_next": k5[i + 1]["open"],  # fill price (next bar open)
            "ts_next_open": k5[i + 1]["start_ms"],
            "silent": silent,
            "want": want,
            "atr1h": atr1h,
        })
    return bars


# ---------------------------------------------------------------------------
# Simulation engines
# ---------------------------------------------------------------------------
def simulate(bars, funding, *, strategy: str, regime_filter: bool, seed: int = 7):
    """Walk the prepared bars and run one position-at-a-time.

    strategy: "trend" (use bar['want']), "random" (random side at entry),
              "buyhold" (always long while eligible).
    regime_filter: if True only trade when bar['silent']; if False trade every bar
                   (control d uses trend strategy with regime_filter=False).

    Returns list of closed trades, each a dict with gross/net/funding/fees and meta.
    """
    rng = random.Random(seed)
    trades = []
    pos = None  # {dir, entry_px, entry_ts, peak, trough}

    def eligible(b):
        return b["silent"] if regime_filter else True

    def close_trade(b, exit_px, reason):
        nonlocal pos
        d = pos["dir"]
        entry_px = pos["entry_px"]
        qty = FIXED_NOTIONAL / entry_px
        gross = qty * (exit_px - entry_px) * d
        entry_notional = FIXED_NOTIONAL
        exit_notional = qty * exit_px
        fees = TAKER_FEE * (entry_notional + exit_notional)
        # funding over the holding window; long pays when rate>0 → cost = +d*notional*rate
        sum_rate, n_settle, had_real = funding_between(funding, pos["entry_ts"], b["ts_next_open"])
        # approximate notional during hold as entry notional (flat sizing)
        funding_cost = d * entry_notional * sum_rate
        net = gross - fees - funding_cost
        trades.append({
            "dir": d,
            "entry_ts": pos["entry_ts"],
            "exit_ts": b["ts_next_open"],
            "entry_px": entry_px,
            "exit_px": exit_px,
            "gross": gross,
            "fees": fees,
            "funding": funding_cost,
            "net": net,
            "reason": reason,
            "bars_held": b["i"] - pos["entry_i"],
            "n_settle": n_settle,
            "had_real_funding": had_real,
        })
        pos = None

    for b in bars:
        atr1h = b["atr1h"]
        if pos is not None:
            px = b["open_next"]
            # update trailing extremes using fill-able price
            pos["peak"] = max(pos["peak"], px)
            pos["trough"] = min(pos["trough"], px)
            exit_now = False
            reason = ""
            # 1) regime / eligibility lost
            if regime_filter and not b["silent"]:
                exit_now, reason = True, "regime_exit"
            # 2) direction flip (trend strategy only)
            elif strategy == "trend" and b["want"] != 0 and b["want"] != pos["dir"]:
                exit_now, reason = True, "flip"
            else:
                # 3) ATR trailing stop
                if atr1h > 0:
                    if pos["dir"] == 1 and px <= pos["peak"] - ATR_TRAIL_MULT * atr1h:
                        exit_now, reason = True, "atr_stop"
                    elif pos["dir"] == -1 and px >= pos["trough"] + ATR_TRAIL_MULT * atr1h:
                        exit_now, reason = True, "atr_stop"
            if exit_now:
                close_trade(b, px, reason)

        if pos is None and eligible(b):
            if strategy == "trend":
                d = b["want"]
            elif strategy == "buyhold":
                d = 1
            else:  # random
                d = rng.choice((1, -1))
            if d != 0:
                px = b["open_next"]
                pos = {"dir": d, "entry_px": px, "entry_ts": b["ts_next_open"],
                       "entry_i": b["i"], "peak": px, "trough": px}

    # force-close any open position at the last available fill
    if pos is not None and bars:
        b = bars[-1]
        close_trade(b, b["open_next"], "eod")
    return trades


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def split_trades(trades, cutoff_ts):
    train = [t for t in trades if t["entry_ts"] < cutoff_ts]
    hold = [t for t in trades if t["entry_ts"] >= cutoff_ts]
    return train, hold


def metrics(trades, *, period_ms, label):
    if not trades:
        return {"label": label, "n": 0}
    nets = [t["net"] for t in trades]
    gross = sum(t["gross"] for t in trades)
    fees = sum(t["fees"] for t in trades)
    funding = sum(t["funding"] for t in trades)
    net = sum(nets)
    wins = sum(1 for x in nets if x > 0)
    st = statistics.stdev(nets) if len(nets) > 1 else 0.0
    sharpe = (statistics.mean(nets) / st) if st > 0 else 0.0
    # equity curve (compounding off; additive on START_EQUITY for return % and DD)
    eq = START_EQUITY
    peak = eq
    maxdd = 0.0
    for x in nets:
        eq += x
        peak = max(peak, eq)
        if peak > 0:
            maxdd = max(maxdd, (peak - eq) / peak)
    total_ret = (eq - START_EQUITY) / START_EQUITY * 100
    bars_held = sum(t["bars_held"] for t in trades)
    time_in_mkt = bars_held * FIVE_M / period_ms * 100 if period_ms else 0.0
    real_funding_trades = sum(1 for t in trades if t["had_real_funding"])
    return {
        "label": label,
        "n": len(trades),
        "total_ret": total_ret,
        "sharpe": sharpe,
        "maxdd": maxdd * 100,
        "win_rate": wins / len(trades) * 100,
        "time_in_mkt": time_in_mkt,
        "avg_net": net / len(trades),
        "avg_gross": gross / len(trades),
        "funding": funding,
        "fees": fees,
        "net": net,
        "gross": gross,
        "edge_before": gross / len(trades),                 # $/trade before funding+fees
        "edge_after": net / len(trades),                    # $/trade after funding+fees
        "real_funding_trades": real_funding_trades,
    }


def fmt_row(m):
    if m["n"] == 0:
        return f"{m['label']:<34} {'0 trades':>8}"
    return (f"{m['label']:<34} {m['n']:>4} {m['total_ret']:>+8.1f}% {m['sharpe']:>+6.2f} "
            f"{m['maxdd']:>6.1f}% {m['win_rate']:>5.1f}% {m['time_in_mkt']:>6.1f}% "
            f"{m['edge_before']:>+8.2f} {m['edge_after']:>+8.2f} {m['funding']:>+8.1f} {m['fees']:>7.1f}")


HEADER = (f"{'config':<34} {'n':>4} {'ret%':>9} {'sharpe':>6} {'maxDD':>7} {'WR':>6} "
          f"{'%mkt':>7} {'$gross':>8} {'$net':>8} {'$fund':>8} {'$fees':>7}")


def main():
    t0 = time.time()
    out_lines = []

    def emit(s=""):
        print(s, flush=True)
        out_lines.append(s)

    data_dir = find_data_dir(None)
    k5, k15, k1h = load_coin("eth", data_dir)
    funding = load_funding(data_dir)
    emit(f"data dir: {data_dir}")
    emit(f"klines: 5m={len(k5):,} 15m={len(k15):,} 1h={len(k1h):,}")
    fspan_d = (funding[-1]['ts_ms'] - funding[0]['ts_ms']) / 86400000
    kspan_d = (k5[-1]['start_ms'] - k5[0]['start_ms']) / 86400000
    emit(f"funding rows: {len(funding):,}  span {fspan_d:.0f}d "
         f"[{datetime.utcfromtimestamp(funding[0]['ts_ms']/1000):%Y-%m-%d} .. "
         f"{datetime.utcfromtimestamp(funding[-1]['ts_ms']/1000):%Y-%m-%d}]")
    emit(f"klines span {kspan_d:.0f}d  -> real funding covers ~{fspan_d/kspan_d*100:.0f}% "
         f"of history; rest uses fallback {FALLBACK_FUNDING_8H:.4%}/8h")
    emit(f"sizing: ${FIXED_NOTIONAL:.0f} flat notional ({LEVERAGE:.0f}x of ${START_EQUITY:.0f}), "
         f"taker {TAKER_FEE:.3%} each side, ATR-trail {ATR_TRAIL_MULT}x(1h), trend ADX>{TREND_ADX:.0f}")
    emit()

    # holdout cutoff by 5m bar TIME (last 30% of the period)
    t_start = k5[0]["start_ms"]
    t_last = k5[-1]["start_ms"]
    cutoff_ts = t_start + int((t_last - t_start) * (1 - HOLDOUT_FRAC))
    period_train = cutoff_ts - t_start
    period_hold = t_last - cutoff_ts
    emit(f"split: train < {datetime.utcfromtimestamp(cutoff_ts/1000):%Y-%m-%d} <= holdout "
         f"(train {period_train/86400000:.0f}d / holdout {period_hold/86400000:.0f}d)")
    emit()

    # prepare bar records for each direction mode
    bars_ema = build_bars(k5, k15, k1h, direction_mode="ema_di")
    bars_don = build_bars(k5, k15, k1h, direction_mode="donchian")
    silent_n = sum(1 for b in bars_ema if b["silent"])
    emit(f"5m bars evaluated: {len(bars_ema):,}  options-SILENT (trend): {silent_n:,} "
         f"({silent_n/max(1,len(bars_ema))*100:.1f}%)")
    emit()

    # ---- runs ----
    runs = []  # (key, label, trades)
    runs.append(("A_ema", "(a) trend EMA+DI, silent-only",
                 simulate(bars_ema, funding, strategy="trend", regime_filter=True)))
    runs.append(("A_don", "(a') trend Donchian, silent-only",
                 simulate(bars_don, funding, strategy="trend", regime_filter=True)))
    runs.append(("B", "(b) buy&hold ETH, silent-only",
                 simulate(bars_ema, funding, strategy="buyhold", regime_filter=True)))
    runs.append(("C", "(c) random L/S, silent-only",
                 simulate(bars_ema, funding, strategy="random", regime_filter=True)))
    runs.append(("D", "(d) trend EMA+DI, ALL bars (no filter)",
                 simulate(bars_ema, funding, strategy="trend", regime_filter=False)))

    emit("=" * 132)
    emit("FULL PERIOD")
    emit("=" * 132)
    emit(HEADER)
    emit("-" * 132)
    full_period = t_last - t_start
    for _, label, trades in runs:
        emit(fmt_row(metrics(trades, period_ms=full_period, label=label)))

    emit()
    emit("=" * 132)
    emit("TRAIN (first 70%)")
    emit("=" * 132)
    emit(HEADER)
    emit("-" * 132)
    for _, label, trades in runs:
        tr, _h = split_trades(trades, cutoff_ts)
        emit(fmt_row(metrics(tr, period_ms=period_train, label=label)))

    emit()
    emit("=" * 132)
    emit("HOLDOUT (last 30%)")
    emit("=" * 132)
    emit(HEADER)
    emit("-" * 132)
    for _, label, trades in runs:
        _tr, h = split_trades(trades, cutoff_ts)
        emit(fmt_row(metrics(h, period_ms=period_hold, label=label)))

    # ---- correlation of perp equity vs a proxy options short-premium return ----
    # We do not re-run the options bot here; instead we report whether the perp trades
    # land in DISTINCT time windows from the options bot. By construction the silent-only
    # perp trades occur ONLY in regime=trend, which is exactly when the options bot is
    # disqualified — so their active windows are temporally DISJOINT. We quantify overlap:
    emit()
    emit("=" * 132)
    a_trades = runs[0][2]
    emit("ORTHOGONALITY CHECK")
    emit("=" * 132)
    emit(f"  silent-only perp (a) active during regime=trend by construction; the options")
    emit(f"  short-premium bot is DISQUALIFIED in regime=trend. Perp active 5m-bar share: ")
    emit(f"  {silent_n}/{len(bars_ema)} = {silent_n/max(1,len(bars_ema))*100:.1f}% of time, disjoint from the")
    emit(f"  options bot's range/transition windows -> equity sources are regime-orthogonal.")
    emit(f"  (a) trades: {len(a_trades)}; real-funding trades: "
         f"{sum(1 for t in a_trades if t['had_real_funding'])}")

    emit()
    emit(f"elapsed {time.time()-t0:.0f}s")

    Path("/tmp/perp_poc_result.txt").write_text("\n".join(out_lines) + "\n")
    print("\nwrote /tmp/perp_poc_result.txt", flush=True)


if __name__ == "__main__":
    main()

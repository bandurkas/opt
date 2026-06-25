"""Live paper-trading strategy wrapper for V2 trend-following hybrid.

V2 hybrid (validated 2026-06-02 on 365d):
  ret_7d > +0.5%  → sell Put  (uptrend — Put premium decays)
  ret_7d < -0.5%  → sell Call (downtrend — Call premium decays)
  |ret_7d| < 0.5% → range — try both, MTF picks the side

Circuit breaker: 5 consecutive losses → 48h pause.
"""
from __future__ import annotations

import os
import time

from db import paper_repo
from services.strategy_config import (
    CB_CONSEC_LIMIT,
    CB_PAUSE_HOURS,
    CALL_GEN_KWARGS,
    CALL_EXIT,
    DEFAULT_SIGMA,
    EXPIRY_TARGET_HOURS,
    get_side_expiry_h,
    PUT_GEN_KWARGS,
    PUT_EXIT,
    RET_7D_THRESHOLD,
    SPREAD_HALF_PCT,
)

# ───────────── Bybit-realistic sizing / friction model ─────────────
# Starting equity sized so the minimum 0.1-ETH lot fits in budget.
# Env-overridable (default = validated $400/0.15). 2026-06-20: paper deposit raised to
# $800 via PAPER_START_EQUITY_USD=800, keeping MO4/MP.15. Rationale (OOS deposit-curve,
# real DVOL): granularity KNEE ~$800 — below it the bot is capital-starved in high-signal
# (volatile) periods (train taken 187→264 at $800); above $800 ROI% is flat (~+39% OOS)
# and $ scales linearly. So $800 = capacity headroom for active periods at no ROI/maxDD
# cost. MO6 was REJECTED (halves ROI), BESTPICK REJECTED (overfit). Reversible via env.
# 5m debounce window id — shared by paper_loop (writer) and entry_proximity
# (reader) so the gauge can detect a window_status row that belongs to a
# DIFFERENT window than the live cond snapshot it's being paired with (e.g.
# right after a window rollover, before paper_loop's first per-minute check
# of the new window has run) instead of trusting it purely by clock age.
SIGNAL_CHECK_EVERY_MIN = 5


def window_id(epoch_min: int) -> int:
    """5m-window id: floor(minute / 5). minute % 5 gives position 0..4 in window."""
    return epoch_min // SIGNAL_CHECK_EVERY_MIN


START_EQUITY_USD = float(os.getenv("PAPER_START_EQUITY_USD", "400"))
# Per trade: up to 15% of equity goes into option margin. On $400 → $60 budget.
MARGIN_PCT_PER_TRADE = float(os.getenv("PAPER_MARGIN_PCT_PER_TRADE", "0.15"))
# Bybit min lot for ETH options.
LOT_MIN_ETH = 0.1
# Bybit Cross-Margin IM rate for short ETH options ≈ 10% of strike-notional.
IM_RATE = 0.10
# Maximum percentage of total equity that can be locked in margin across all open positions.
MAX_PORTFOLIO_MARGIN_PCT = 0.80
# Below this equity, the min-lot floor stops rescuing trades — a genuinely dead
# account should sit out, not force-trade circuit-breaker-style.
ABS_FLOOR_EQUITY = float(os.getenv("PAPER_ABS_FLOOR_EQUITY", "50"))
# Bybit taker fee on notional, capped at 12.5% of premium per side.
FEE_RATE = 0.0003
FEE_CAP_PCT_OF_PREMIUM = 0.125
# 7d return window in 5m bars (7 * 24 * 12 = 2016)
BARS_7D = 2016


def is_cb_active(state: dict, now_ms: int) -> bool:
    return now_ms < int(state.get("cb_cooldown_until_ms") or 0)


def dyn_size_factor(state: dict) -> float:
    """Halve size when 10-trade WR < 40%."""
    pnls = state.get("recent_pnls_json") or []
    if len(pnls) >= 10:
        wr = sum(1 for p in pnls[-10:] if p > 0) / 10.0
        if wr < 0.40:
            return 0.5
    return 1.0


def realistic_size_lots(free_margin_usd: float, equity_usd: float, strike: float,
                        premium_mid: float, state: dict) -> int:
    """How many 0.1-ETH lots fit in our margin budget at this signal.

    Min-lot floor: %-of-equity sizing alone can self-reinforce a drawdown —
    once equity*MARGIN_PCT_PER_TRADE drops below one lot's margin, the budget
    shrinks with the very equity it can no longer recover, permanently
    locking the account out of an edge that backtesting shows stays positive
    at every equity level (see finding_grogu_sl_incident_deploy_gap.md /
    eth_capital_aware_sizing_test.py — 24/24 historical starts improved,
    +96%..+496%, by guaranteeing 1 lot whenever the PORTFOLIO cap still has
    room). Only suppressed below ABS_FLOOR_EQUITY, where the account is
    genuinely dead rather than just drawn down.
    """
    if strike <= 0 or premium_mid <= 0 or equity_usd <= 0:
        return 0
    margin_per_lot = (IM_RATE * strike + premium_mid) * LOT_MIN_ETH
    if margin_per_lot <= 0:
        return 0
    trade_budget = equity_usd * MARGIN_PCT_PER_TRADE * dyn_size_factor(state)
    budget = min(trade_budget, free_margin_usd)
    n_lots = int(budget // margin_per_lot)
    if n_lots < 1 and margin_per_lot <= free_margin_usd and equity_usd >= ABS_FLOOR_EQUITY:
        return 1
    return n_lots


def margin_per_lot(strike: float, premium_mid: float) -> float:
    return (IM_RATE * strike + premium_mid) * LOT_MIN_ETH


def apply_entry_spread(premium_mid: float) -> float:
    """We SELL at bid = mid·(1 − half-spread)."""
    return premium_mid * (1 - SPREAD_HALF_PCT / 100.0)


def apply_exit_spread(premium_mid: float) -> float:
    """We BUY BACK at ask = mid·(1 + half-spread)."""
    return premium_mid * (1 + SPREAD_HALF_PCT / 100.0)


def fee_per_side(notional_usd: float, premium_total_usd: float) -> float:
    """0.03% × notional, capped at 12.5% of the premium that side handles."""
    cap = abs(premium_total_usd) * FEE_CAP_PCT_OF_PREMIUM
    return min(notional_usd * FEE_RATE, cap)


def compute_ret_7d(k5: list, idx: int) -> float:
    """Compute 7-day return ending at k5[idx]."""
    if idx < BARS_7D:
        return 0.0
    prev_close = k5[idx - BARS_7D]["close"]
    if prev_close <= 0:
        return 0.0
    return (k5[idx]["close"] - prev_close) / prev_close * 100


def is_new_signal(idx_5m: int, last_signal_idx_5m: int | None) -> bool:
    """Has this generator occurrence already been consumed by an earlier tick?

    check_new_signal() re-walks the FULL k5 history every tick (no memory of
    its own), and accepts a 2-bar-wide window (idx_5m in {last-1, last}) to
    tolerate candle-close timing jitter. That same 2-bar slack means the
    SAME cooldown-spaced occurrence gets rediscovered as "new" on the tick
    right after it first appeared, opening a second near-duplicate position
    5 minutes later (confirmed live 2026-06-23: Sniper1 paired entries 5 min
    apart, pairs spaced exactly 30 min = cooldown_bars). This persists the
    idx_5m of the last occurrence we actually acted on, in `paper_state`, so
    a tick can tell "new occurrence" from "same one I saw last tick."
    """
    return last_signal_idx_5m is None or idx_5m > int(last_signal_idx_5m)


def allowed_sides(ret_7d: float) -> list[str]:
    """V2 trend-following: return list of sides allowed at this 7d return.

    ret_7d > +0.5%  → ["P"] (uptrend → only Put)
    ret_7d < -0.5%  → ["C"] (downtrend → only Call)
    |ret_7d| < 0.5% → ["P", "C"] (range — try both, MTF picks)
    """
    if ret_7d > RET_7D_THRESHOLD:
        return ["P"]
    elif ret_7d < -RET_7D_THRESHOLD:
        return ["C"]
    return ["P", "C"]


def determine_side(ret_7d: float) -> str | None:
    """Back-compat single-side picker for UI / conditions endpoint.

    Returns the first allowed side, or None only if both are allowed (range).
    The actual signal logic in paper_loop iterates allowed_sides() and lets
    the per-side gen filter (MTF, regime, vol) decide which one fires.
    """
    sides = allowed_sides(ret_7d)
    if len(sides) == 1:
        return sides[0]
    return None  # range zone — UI should display "both / MTF picks"


def _evaluate_side(side: str, mtf: dict, regime: str,
                    rolling_vols: list[float], closes_1h: list[float]) -> dict:
    """Runs the vol/regime/mtf/bull checks for one side's gen_kw — mirrors
    gen_sell_premium_iv_high exactly so the gauge/debounce never diverges
    from what the real generator would fire on.

    `rolling_vols` and `regime` are computed ONCE by the caller and shared
    across sides — they don't depend on which side's gen_kw is being
    checked (only the vol_threshold/regime_filter membership test does), so
    recomputing them per side would just be the same O(n) realized_vol scan
    and detect_regime call done twice every per-minute tick in range zone."""
    from .indicators import ema
    from .momentum_mtf import direction_filter_ok

    gen_kw = PUT_GEN_KWARGS if side == "P" else CALL_GEN_KWARGS
    out = {
        "vol_high": False, "regime_ok": False, "mtf_direction_ok": False,
        "vol_pctile": None, "regime": regime, "ema_ratio": None,
        "bull_filter_ok": True,
    }

    if len(rolling_vols) >= 30:
        current_vol = rolling_vols[-1]
        sorted_vols = sorted(rolling_vols)
        threshold_idx = int(len(sorted_vols) * gen_kw["vol_threshold"])
        threshold = sorted_vols[threshold_idx]
        below = sum(1 for v in sorted_vols if v < current_vol)
        out["vol_pctile"] = round(below / len(sorted_vols), 3)
        out["vol_high"] = current_vol >= threshold

    out["regime_ok"] = regime in gen_kw["regime_filter"]

    out["mtf_direction_ok"] = direction_filter_ok(
        mtf, gen_kw.get("mtf_direction_filter"), gen_kw.get("mtf_anchor_tf"))

    bull_max = gen_kw.get("bull_market_ratio_max")
    if bull_max is not None and len(closes_1h) >= 200:
        ema50 = ema(closes_1h, 50)
        ema200 = ema(closes_1h, 200)
        if ema50 is not None and ema200 not in (None, 0):
            ratio = ema50 / ema200
            out["ema_ratio"] = round(ratio, 4)
            out["bull_filter_ok"] = ratio <= bull_max

    out["ready"] = (out["vol_high"] and out["regime_ok"]
                    and out["mtf_direction_ok"] and out["bull_filter_ok"])
    return out


def evaluate_conditions(k5: list, k15: list, k1h: list) -> dict:
    """Check each entry condition for V2 trend-following hybrid.

    Returns a dict with per-condition booleans + summary + which side(s) active.
    In range zone (both sides allowed), checks EACH side's own gen_kw
    independently — exactly like check_new_signal/gen_sell_premium_iv_high
    does live — instead of picking one side from a single shared MTF
    consensus first. That earlier shortcut silently couldn't represent a
    side whose gate (e.g. CALL's 1h-anchor MTF) diverges from the 3-way
    consensus used to pick `active_side`.
    """
    from .indicators import realized_vol
    from .momentum_mtf import analyze_tf, consensus
    from .regime import detect_regime

    out = {
        "ready": False,
        "active_side": None,    # 'P', 'C', or None
        "dead_zone": False,     # always False under V2 (range allows both)
        "ret_7d": None,
        "vol_high": False,
        "regime_ok": False,
        "mtf_direction_ok": False,
        "spot": None,
        "vol_pctile": None,
        "regime": None,
        "mtf_direction": None,
        "mtf_aligned_count": None,
        "ema_ratio": None,
        "bull_filter_ok": True,
    }
    if not k5 or not k15 or not k1h:
        return out
    if len(k5) < BARS_7D or len(k15) < 50 or len(k1h) < 200:
        return out

    idx = len(k5) - 1
    out["spot"] = k5[idx]["close"]

    # 7d return → which sides are allowed under V2 trend-following
    ret_7d = compute_ret_7d(k5, idx)
    out["ret_7d"] = round(ret_7d, 2)
    sides = allowed_sides(ret_7d)

    # MTF (computed once, reused)
    HIST = 240
    s5 = k5[-HIST:] if len(k5) > HIST else k5
    s15 = k15[-HIST:] if len(k15) > HIST else k15
    s1h = k1h[-HIST:] if len(k1h) > HIST else k1h
    mtf = consensus(analyze_tf(s5), analyze_tf(s15), analyze_tf(s1h))
    out["mtf_direction"] = mtf["direction"]
    out["mtf_aligned_count"] = mtf["tfs_aligned"]

    closes_1h = [c["close"] for c in s1h]

    # Vol-percentile rolling window and regime classification don't depend on
    # which side's gen_kw is being checked (only the threshold/filter
    # membership test does) — compute once and share, instead of repeating
    # the same O(n) realized_vol scan + detect_regime call per side below.
    rolling_vols: list[float] = []
    for j in range(20, len(closes_1h)):
        rv = realized_vol(closes_1h[:j + 1], lookback=24)
        if rv is not None:
            rolling_vols.append(rv)
    regime = detect_regime(s1h).get("regime", "unknown")

    # Evaluate every allowed side independently (matches the real generator).
    # Trend zone (one side forced by ret_7d) keeps reporting that side as
    # active_side regardless of readiness — unchanged back-compat behavior.
    # Range zone (both sides allowed) picks whichever side is actually ready,
    # or None if neither is — this is the part that used to short-circuit on
    # a single shared MTF consensus before any per-side gen_kw ran.
    side_results = {side: _evaluate_side(side, mtf, regime, rolling_vols, closes_1h)
                     for side in sides}
    if len(sides) == 1:
        active_side = sides[0]
    else:
        active_side = next((s for s in sides if side_results[s]["ready"]), None)
    out["active_side"] = active_side

    # When nobody's ready in range zone, display whichever side has more
    # gates passing (closest to firing) rather than always defaulting to the
    # first allowed side — the gauge exists to show how close the market is,
    # and silently always showing Put's breakdown would hide a Call that's
    # one gate away.
    def _gates_passed(r: dict) -> int:
        return sum([r["vol_high"], r["regime_ok"], r["mtf_direction_ok"], r["bull_filter_ok"]])

    display_side = active_side or max(sides, key=lambda s: _gates_passed(side_results[s]))
    res = side_results[display_side]
    out["vol_high"] = res["vol_high"]
    out["regime_ok"] = res["regime_ok"]
    out["mtf_direction_ok"] = res["mtf_direction_ok"]
    out["vol_pctile"] = res["vol_pctile"]
    out["regime"] = res["regime"]
    out["ema_ratio"] = res["ema_ratio"]
    out["bull_filter_ok"] = res["bull_filter_ok"]
    out["ready"] = active_side is not None and res["ready"]
    return out


WINDOW_STATUS_STALE_MS = 90_000  # >1 missed per-minute tick from paper_loop


def entry_proximity(cond: dict, adx_score: float,
                    window_status: dict | None = None,
                    now_ms: int | None = None) -> dict:
    """How close the market is to a tradeable entry, as 0-100 + a zone label.

    Display/observability only — this drives the dashboard "proximity gauge", NOT
    position sizing (ADX-score sizing was rejected by backtest: it hurt compounded
    return and worsened drawdown). The needle is a weighted blend of the same
    factors the entry uses, each normalised to 0-1:
      - adx    : adx_score/10  (HIGH = low/falling ADX = range-like = best decay)
      - mtf    : aligned timeframes / 3
      - vol    : volatility percentile
      - regime : regime_ok pass (matches the real entry's regime gate)
      - bull   : bull-market filter pass (Call side; PUT's bull_market_ratio_max is None)
    100 is reserved for `ready` AND a *confirmed* live debounce window
    (FLICKER_TOLERANCE, paper_loop.py) that is not disqualified AND has
    reached the close-tick minute (`min_in_window == SIGNAL_CHECK_EVERY_MIN -
    1`, the same minute paper_loop's `fire_now` actually attempts the open on)
    — otherwise the gauge could pin "entry" as early as minute 0 of 5 just
    because nothing has failed YET, when 3-4 more per-minute checks still
    remain that could still flip the window to disqualified. 2026-06-25: a
    window passing every gate at minute 1 is NOT a guarantee the bot opens a
    trade — only minute 4 (the one immediately preceding the real fire
    attempt) is. `window_status` is the live state paper_loop persists every
    per-minute check (see db.paper_repo window_status_json): `wid` (which 5m
    window it was computed for), `min_in_window`, and `checked_at_ms`. It's
    trusted only when BOTH fresh (within WINDOW_STATUS_STALE_MS) AND for the
    SAME window the caller's `now_ms` falls in — a fresh-by-clock status for
    the window the bot just finished (rolled over seconds ago) must not be
    applied to the new window's `cond` snapshot. Whenever it can't be
    trusted, `debounce_unknown` is True and the gauge is deliberately
    conservative: it caps below 100 even if `ready` — unconfirmed debounce
    state must never be displayed as a guaranteed entry. Even at 100%, this
    is still display-only: margin/position-cap/pause/kill-switch checks
    happen later in paper_loop's fire path and aren't represented here.
    """
    def _f(x: float | None) -> float:
        return 0.0 if x is None else max(0.0, min(1.0, float(x)))

    f_adx = _f((adx_score or 0.0) / 10.0)
    f_mtf = _f((cond.get("mtf_aligned_count") or 0) / 3.0)
    f_vol = _f(cond.get("vol_pctile"))
    f_regime = 1.0 if cond.get("regime_ok") else 0.0
    f_bull = 1.0 if cond.get("bull_filter_ok") else 0.0
    weights = {"adx": 0.30, "mtf": 0.20, "vol": 0.15, "regime": 0.20, "bull": 0.15}
    composite = 100.0 * (weights["adx"] * f_adx + weights["mtf"] * f_mtf
                         + weights["vol"] * f_vol + weights["regime"] * f_regime
                         + weights["bull"] * f_bull)

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    debounce_unknown = True
    window_disqualified = False
    at_close_tick = False
    if window_status and window_status.get("checked_at_ms") is not None:
        fresh = (now_ms - int(window_status["checked_at_ms"])) <= WINDOW_STATUS_STALE_MS
        same_window = window_status.get("wid") == window_id(now_ms // 60_000)
        if fresh and same_window:
            debounce_unknown = False
            window_disqualified = bool(window_status.get("disqualified"))
            at_close_tick = window_status.get("min_in_window") == SIGNAL_CHECK_EVERY_MIN - 1

    # Unconfirmed debounce state (stale, missing, or belongs to a different
    # window than `cond`) must never be displayed as "entry" — only a
    # confirmed, non-disqualified window AT the close-tick minute can pin the
    # gauge to 100. Earlier minutes (0..3 of 5) cap below 100 even if nothing
    # has failed yet — "hasn't failed" isn't "guaranteed to fire".
    ready = (bool(cond.get("ready")) and not debounce_unknown
             and not window_disqualified and at_close_tick)
    pct = 100.0 if ready else min(99.0, composite)
    pct = round(pct, 1)
    if pct >= 100.0:
        zone = "entry"        # all gates pass AND debounce window confirmed — bot will fire
    elif pct >= 80.0:
        zone = "ready"        # one factor short of entry
    elif pct >= 50.0:
        zone = "preparing"    # factors aligning
    else:
        zone = "waiting"      # far from a signal
    return {
        "proximity_pct": pct,
        "zone": zone,
        "factors": {"adx": round(f_adx, 3), "mtf": round(f_mtf, 3),
                    "vol": round(f_vol, 3), "regime": round(f_regime, 3),
                    "bull": round(f_bull, 3)},
        "weights": weights,
        "debounce_unknown": debounce_unknown,
        "window_disqualified": window_disqualified,
    }


def _next_cb_state(consec: int, cb_until: int, pnls: list[float],
                   pnl_pct: float, now_ms: int) -> dict:
    """Pure: given the *current* CB counters, compute the next ones after one
    trade closes at `pnl_pct`. No I/O — unit-testable on its own.

    A loss bumps the consecutive-loss counter; hitting CB_CONSEC_LIMIT arms the
    circuit breaker (cooldown window) and resets the counter. Any win resets it.
    `recent_pnls` is a rolling window of the last 50 results.
    """
    pnls = (list(pnls) + [float(pnl_pct)])[-50:]
    if pnl_pct <= 0:
        consec += 1
        if consec >= CB_CONSEC_LIMIT:
            cb_until = now_ms + CB_PAUSE_HOURS * 60 * 60 * 1000
            consec = 0
    else:
        consec = 0
    return {
        "consec_losses": consec,
        "cb_cooldown_until_ms": cb_until,
        "recent_pnls": pnls,
    }


def record_trade_result(pnl_pct: float) -> dict:
    """Update CB counters + recent_pnls list after a trade closes.

    The read-modify-write of consec_losses MUST be atomic: when two positions
    close in one loop iteration, a split read/update would lose one increment and
    the breaker could miss 5 consecutive losses (FUTURE_WORK §5.2). We therefore
    do the whole transition inside a single locked transaction in the repo, with
    `_next_cb_state` as the pure decision step.
    """
    paper_repo.ensure_state(START_EQUITY_USD)
    return paper_repo.record_trade_outcome(
        float(pnl_pct), int(time.time() * 1000), _next_cb_state,
    )

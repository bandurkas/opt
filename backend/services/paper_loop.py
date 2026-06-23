"""Live paper-trading background service — V3 Hybrid.

Runs as a separate docker container. Two main responsibilities:

1. **Signal check** every 5 min (right after a 5m candle closes):
   Pull recent klines from DB, compute 7d return, determine active side
   (Put or Call), run the side-specific generator, check if the LAST bar
   emits a signal. If yes (and CB not active, and we have capacity),
   open a paper position using current Bybit option chain (or BS fallback).

2. **Position monitoring** every 30s:
   For each open position, fetch current option price, check TP1/TP2/SL/
   time-stop (using per-side exit params). Close (or half-close) when
   triggered. Update equity snapshot.

State persisted in `paper_state`, `paper_positions`, `paper_equity_snapshots`.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import control_repo  # noqa: E402  (Mission Control pause/close-all flags)
from db import paper_repo  # noqa: E402
from db.engine import apply_schema  # noqa: E402
from db.repository import recent_klines  # noqa: E402
from services import backtest_bs as bs  # noqa: E402
from services import broker  # noqa: E402  (live order routing; inert in paper mode)
from services import execution_config as cfg  # noqa: E402  (live mode/caps; safe defaults)
from services import live_safety  # noqa: E402  (live pre-open gates; inert in paper mode)
from services import reconcile  # noqa: E402  (live exchange↔DB sync; inert in paper mode)
from services.bybit_client import bybit_client  # noqa: E402
from services.strategy_config import (  # noqa: E402
    CALL_SL_DOLLAR_FRAC,
    RET_7D_THRESHOLD,
    get_side_gen_kwargs,
)
from services.paper_strategy import (  # noqa: E402
    BARS_7D,
    CB_CONSEC_LIMIT,
    CB_PAUSE_HOURS,
    DEFAULT_SIGMA,
    IM_RATE,
    get_side_expiry_h,
    LOT_MIN_ETH,
    MAX_PORTFOLIO_MARGIN_PCT,
    START_EQUITY_USD,
    allowed_sides,
    apply_entry_spread,
    apply_exit_spread,
    compute_ret_7d,
    determine_side,
    dyn_size_factor,
    evaluate_conditions,
    fee_per_side,
    is_cb_active,
    margin_per_lot,
    realistic_size_lots,
    record_trade_result,
)
from services.strategy_registry import gen_sell_premium_iv_high  # noqa: E402
from services import telegram_notify  # noqa: E402


BOT_NAME = "eth_signal"
POLL_INTERVAL_S = int(os.getenv("PAPER_POLL_INTERVAL", "30"))
SIGNAL_CHECK_EVERY_MIN = 5
# Entry conditions are re-evaluated every minute; the open command is committed
# near the 5m candle close (~:50 of the window's last minute) only if conditions
# held on EVERY per-minute check inside the window (persistence / debounce).
ENTRY_FIRE_SECOND = int(os.getenv("PAPER_ENTRY_FIRE_SECOND", "50"))
SPOT_SYMBOL = "ETHUSDT"
BASE_COIN = "ETH"
STRIKE_GRID = 25.0   # Bybit ETH options use $25/$50 grid; use $25 for safety


# ───────────────────── kline → generator input ─────────────────────

def load_klines_for_generator(window_5m: int = 2100) -> tuple[list, list, list]:
    """Pull recent klines from DB for the generator. Returns (k5, k15, k1h).

    window_5m must be >= BARS_7D (2016) so we can compute 7d return.
    """
    k5 = recent_klines(SPOT_SYMBOL, "5m", limit=window_5m)
    k15 = recent_klines(SPOT_SYMBOL, "15m", limit=220)
    k1h = recent_klines(SPOT_SYMBOL, "1h", limit=270)
    return k5, k15, k1h


# ───────────────────── option pricing (live + fallback) ─────────────

def pick_bybit_atm_option(chain: list[dict], spot: float, target_expiry_h: int,
                          option_side: str = "C") -> dict | None:
    """From live Bybit chain, pick ATM call or put closest to target_expiry_h."""
    option_side = (option_side or "C").upper()
    if option_side not in ("C", "P"):
        option_side = "C"
    now_ms = int(time.time() * 1000)
    target_ms = now_ms + target_expiry_h * 3_600_000
    candidates = [
        o for o in chain
        if o.get("side") == option_side
        and o.get("expiry_ms", 0) > now_ms + 6 * 3_600_000
        and (o.get("bid") or 0) > 0
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda o: abs((o.get("expiry_ms") or 0) - target_ms))
    by_expiry = candidates[0].get("expiry_ms")
    same_expiry = [o for o in candidates if o.get("expiry_ms") == by_expiry]
    target_strike = round(spot / STRIKE_GRID) * STRIKE_GRID
    same_expiry.sort(key=lambda o: abs((o.get("strike") or 0) - target_strike))
    return same_expiry[0] if same_expiry else None


def current_mark(p: dict, spot: float, chain_dict: dict[str, dict] | None) -> float | None:
    """Live Bybit mark/mid for an open position."""
    if chain_dict:
        key = f"{p['side']}-{int(p['strike'])}-{p['expiry_ms']}"
        q = chain_dict.get(key)
        if q:
            mark = float(q.get("mark_price", 0) or 0)
            if mark > 0:
                return mark
            bid = float(q.get("bid", 0) or 0)
            ask = float(q.get("ask", 0) or 0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
    return None


def current_mark_or_bs(p: dict, spot: float, chain_dict: dict[str, dict] | None) -> float:
    """For MtM display only — never use this for TP/SL decisions."""
    live = current_mark(p, spot, chain_dict)
    if live is not None:
        return live
    return price_option_bs(p["side"], spot, float(p["strike"]),
                           int(p["expiry_ms"]), DEFAULT_SIGMA)


def price_option_bs(side: str, spot: float, strike: float, expiry_ms: int,
                    sigma: float = DEFAULT_SIGMA) -> float:
    """Black-Scholes fallback pricing."""
    now_ms = int(time.time() * 1000)
    T_years = max(1 / 365 / 24, (expiry_ms - now_ms) / 1000 / 86400 / 365)
    return bs.price(side, spot, strike, T_years, sigma)


# ───────────────────── side-specific exit config ────────────────────

def exit_for_side(side: str) -> dict:
    """Return TP/SL thresholds for the given side."""
    from services.strategy_config import get_side_exits
    return get_side_exits(side)


def call_dollar_sl_pct(strike: float, entry_credit: float,
                       sl_dollar_frac: float = CALL_SL_DOLLAR_FRAC,
                       im_rate: float = IM_RATE) -> float:
    """Dollar-margin SL for short Calls, expressed as the equivalent
    %-of-entry-credit ratio so it plugs straight into the existing
    `sl_threshold = entry_credit * (1 + sl_pct)` check unchanged.

    margin = im_rate*strike + entry_credit (per 1 ETH, matches BTC straddle's
    btc_straddle_sl.margin_per_lot formula); SL trips at sl_dollar_frac*margin
    of buyback loss. Validated 2026-06-21 (eth_dollar_sl_deposit_sweep.py,
    frac=0.10 dominates the live %-SL=0.75 on the real $-account engine).
    Put side is unaffected — keeps the static %-of-premium sl_pct.
    """
    if entry_credit <= 0:
        return 0.0
    margin = im_rate * strike + entry_credit
    return sl_dollar_frac * margin / entry_credit


# ───────────────────── signal logic ─────────────────────────────────

def check_new_signal(k5, k15, k1h) -> dict | None:
    """Run V2 trend-following hybrid generator (validated 2026-06-02 on 365d).

    Logic per tick:
      1. Compute 7d return
      2. allowed_sides(ret_7d):
           ret > +0.5%  → ["P"] only
           ret < -0.5%  → ["C"] only
           |ret| < 0.5% → ["P", "C"] (range — try both)
      3. For each allowed side, run gen_sell_premium_iv_high with side-specific
         filters (MTF=up for Put, MTF=down for Call, etc.). First side to fire
         at the current bar wins.

    Cooldown is taken from each side's PUT/CALL_GEN_KWARGS["cooldown_bars"] = 6.
    """
    if not k5 or len(k5) < BARS_7D + 1:
        return None

    idx = len(k5) - 1
    ret_7d = compute_ret_7d(k5, idx)
    sides = allowed_sides(ret_7d)
    last_idx = idx

    for side in sides:
        gen_kw = get_side_gen_kwargs(side)
        sigs = gen_sell_premium_iv_high(k5, k15, k1h, **gen_kw)
        latest = [s for s in sigs if s.get("idx_5m") in (last_idx - 1, last_idx)]
        if not latest:
            continue
        sig = latest[-1]
        if sig.get("side") != side:
            continue
        sig["active_side"] = side
        sig["ret_7d"] = round(ret_7d, 2)
        return sig

    return None


# ───────────────────── equity computation ──────────────────────────

def compute_equity(state: dict, spot: float, atm_chain_quotes: dict[str, dict] | None = None) -> dict:
    """Compute current equity = start + realized PnL + unrealized PnL.

    For SHORT premium positions, unrealized PnL uses ASK price (the actual
    buyback cost) when available. Falls back to mid if no live data.
    """
    start_equity = float(state["start_equity_usd"])
    stats = paper_repo.position_stats()
    realized = float(stats["realized_usd"])

    if broker.is_live():
        # Live equity is the real wallet balance (Bybit already marks open
        # positions into it). Fall back to the DB model only if the read fails.
        wallet = broker.wallet_equity_usdt()
        if wallet is not None:
            return {
                "equity": wallet,
                "realized": realized,
                "unrealized": wallet - start_equity - realized,
                "n_open": stats["n_open"],
                "n_closed": stats["n_closed"],
            }

    open_pos = paper_repo.open_positions()
    unrealized = 0.0
    for p in open_pos:
        contracts = float(p["contracts"])

        # Get live mark for short position MtM
        if atm_chain_quotes:
            key = f"{p['side']}-{int(p['strike'])}-{p['expiry_ms']}"
            q = atm_chain_quotes.get(key)
            if q:
                ask = float(q.get("ask", 0) or 0)
                bid = float(q.get("bid", 0) or 0)
                if ask > 0:
                    # Short position: profit = entry - buyback_cost (ask)
                    pnl_per_contract = float(p["entry_credit_usd"]) - ask
                    unrealized += pnl_per_contract * contracts
                    continue
                elif bid > 0:
                    pnl_per_contract = float(p["entry_credit_usd"]) - bid
                    unrealized += pnl_per_contract * contracts
                    continue

        # BS fallback — use mid price
        mark = current_mark_or_bs(p, spot, atm_chain_quotes)
        pnl_per_contract = float(p["entry_credit_usd"]) - mark
        unrealized += pnl_per_contract * contracts

    equity = start_equity + realized + unrealized
    return {
        "equity": equity,
        "realized": realized,
        "unrealized": unrealized,
        "n_open": stats["n_open"],
        "n_closed": stats["n_closed"],
    }


# ───────────────────── position open / close ───────────────────────

def open_paper_position(signal: dict, spot: float, equity_usd: float, free_margin_usd: float,
                        state: dict, chain: list[dict]) -> int | None:
    """Open a paper position for a signal — Bybit-realistic sizing/friction.

    Uses per-side exit params (PUT_EXIT for P, CALL_EXIT for C). `chain` is the
    options chain already fetched by the caller this tick (avoids re-fetching).
    """
    active_side = signal.get("active_side", signal.get("side", "P"))
    ex_kw = exit_for_side(active_side)

    target_expiry_h = get_side_expiry_h(active_side)
    pick = pick_bybit_atm_option(chain, spot, target_expiry_h, active_side)

    if pick and pick.get("bid", 0) > 0 and pick.get("ask", 0) > 0:
        strike = float(pick["strike"])
        expiry_ms = int(pick["expiry_ms"])
        premium_mid = (float(pick["bid"]) + float(pick["ask"])) / 2.0
        entry_source = "bybit"
        symbol = pick["symbol"]
    else:
        strike = round(spot / STRIKE_GRID) * STRIKE_GRID
        expiry_ms = int(time.time() * 1000) + target_expiry_h * 3_600_000
        premium_mid = price_option_bs(active_side, spot, strike, expiry_ms, DEFAULT_SIGMA)
        if premium_mid <= 0:
            print(f"[paper] open skipped — could not price option", flush=True)
            return None
        entry_source = "bs_fallback"
        symbol = f"ETH-?-{int(strike)}-{active_side}"

    # ── sizing + entry pricing: LIVE (real fills) vs PAPER (simulated) ──
    if broker.is_live():
        # Real money: only trade a real Bybit instrument (never the BS fallback),
        # size off the real wallet, and persist the REAL fill — never assume one.
        if entry_source != "bybit":
            print("[paper] LIVE open skip — no real Bybit instrument (BS fallback)", flush=True)
            return None
        # P4 liquidity guard: skip illiquid options (wide bid/ask) before ordering.
        if pick and not live_safety.spread_ok(float(pick.get("bid") or 0), float(pick.get("ask") or 0)):
            sp = live_safety.spread_pct(float(pick.get("bid") or 0), float(pick.get("ask") or 0))
            print(f"[paper] LIVE open skip — spread {sp}% > {cfg.MAX_SPREAD_PCT}% (illiquid)", flush=True)
            return None
        fill = broker.live_open(symbol, strike, premium_mid)
        if fill is None:
            telegram_notify.notify_skipped_margin(
                spot=spot, strike=strike,
                need_usd=margin_per_lot(strike, premium_mid), have_usd=equity_usd)
            return None
        # P4 post-fill slippage alert (informational — does not block).
        if live_safety.slippage_alarming(premium_mid, fill.avg_price, "sell"):
            telegram_notify.notify_slippage(
                symbol=symbol, expected=premium_mid, got=fill.avg_price,
                pct=live_safety.slippage_pct(premium_mid, fill.avg_price, "sell"))
        n_lots = fill.n_lots
        contracts = fill.qty_eth
        entry_fee = fill.fee
        premium_received_total = fill.avg_price * contracts
        entry_credit_per_contract_net = fill.avg_price - (entry_fee / max(contracts, 1e-9))
        margin_locked = margin_per_lot(strike, premium_mid) * n_lots  # est; exchange authoritative
        entry_credit_pct = entry_credit_per_contract_net / spot * 100 if spot > 0 else 0
        entry_source = "bybit_live"
    else:
        n_lots = realistic_size_lots(free_margin_usd, equity_usd, strike, premium_mid, state)
        if n_lots < 1:
            m_per_lot = margin_per_lot(strike, premium_mid)
            print(f"[paper] open skipped — insufficient margin/budget "
                  f"(need ${m_per_lot:.2f}/lot, equity=${equity_usd:.2f}, "
                  f"free_margin=${free_margin_usd:.2f}, size_factor={dyn_size_factor(state)})", flush=True)
            telegram_notify.notify_skipped_margin(
                spot=spot, strike=strike, need_usd=m_per_lot, have_usd=equity_usd,
            )
            return None

        contracts = n_lots * LOT_MIN_ETH
        notional = strike * contracts

        entry_credit_per_contract_gross = apply_entry_spread(premium_mid)
        premium_received_total = entry_credit_per_contract_gross * contracts
        entry_fee = fee_per_side(notional, premium_received_total)
        entry_credit_per_contract_net = entry_credit_per_contract_gross - (entry_fee / max(contracts, 1e-9))

        margin_locked = margin_per_lot(strike, premium_mid) * n_lots
        entry_credit_pct = entry_credit_per_contract_net / spot * 100

    # Calls: dollar-margin SL (validated 2026-06-21) instead of the static
    # %-of-premium sl_pct. Puts are unaffected (no viable dollar-SL operating
    # point was found for them) and keep ex_kw["sl_pct"] as-is.
    sl_pct = (call_dollar_sl_pct(strike, entry_credit_per_contract_net)
             if active_side == "C" else ex_kw["sl_pct"])

    pid = paper_repo.open_position(
        opened_at_ms=int(time.time() * 1000),
        underlying_at_open=spot,
        side=active_side,
        strike=strike,
        expiry_ms=expiry_ms,
        contracts=contracts,
        size_usd=margin_locked,
        entry_credit_usd=entry_credit_per_contract_net,
        entry_credit_pct=entry_credit_pct,
        entry_source=entry_source,
        tp1_pct=ex_kw["tp1_pct"],
        tp2_pct=ex_kw["tp2_pct"],
        sl_pct=sl_pct,
        hold_h=ex_kw["hold_h"],
        signal_payload={
            "symbol": symbol, "signal": signal,
            "n_lots": n_lots, "margin_locked": round(margin_locked, 2),
            "premium_mid": round(premium_mid, 4),
            "entry_fee_usd": round(entry_fee, 4),
            "active_side": active_side,
            "ret_7d": signal.get("ret_7d", 0),
        },
    )
    print(f"[paper] OPENED #{pid}: SELL {symbol} "
          f"lots={n_lots} contracts={contracts:.2f}ETH  "
          f"credit_net=${entry_credit_per_contract_net:.2f}/ETH "
          f"margin=${margin_locked:.2f}  fee=${entry_fee:.3f}  sl_pct={sl_pct:.2f} "
          f"source={entry_source}  side={active_side} 7d_ret={signal.get('ret_7d', 0):+.2f}%",
          flush=True)
    telegram_notify.notify_open(
        pid=pid, symbol=symbol, side=active_side, strike=strike, spot=spot,
        n_lots=n_lots, contracts=contracts,
        premium_recv=premium_received_total,
        margin_locked=margin_locked, entry_fee=entry_fee, source=entry_source,
    )
    return pid


def check_and_close_position(p: dict, spot: float,
                              chain_dict: dict[str, dict] | None = None) -> bool:
    """Check exit conditions on one position. Uses per-side exit params."""
    now_ms = int(time.time() * 1000)
    age_h = (now_ms - int(p["opened_at_ms"])) / 3_600_000

    entry_credit = float(p["entry_credit_usd"])

    # Time-stop ALWAYS runs
    if age_h >= float(p["hold_h"]):
        premium_mid = current_mark_or_bs(p, spot, chain_dict)
        return _do_close(p, premium_mid, "time_stop", now_ms)

    # TP/SL require LIVE Bybit mark. BS fallback is NOT used for TP/SL because
    # BS σ=0.6 diverges from real Bybit IV by 30–50%, which would trigger
    # false SL hits during brief chain outages. If no live data, skip TP/SL
    # check this tick — time-stop will eventually close the position anyway.
    premium_mid = current_mark(p, spot, chain_dict)
    if premium_mid is None:
        return False

    tp1_threshold = entry_credit * (1 - float(p["tp1_pct"]))
    tp2_threshold = entry_credit * (1 - float(p["tp2_pct"]))
    sl_threshold = entry_credit * (1 + float(p["sl_pct"]))

    reason = None
    if premium_mid >= sl_threshold:
        reason = "sl"
    elif premium_mid <= tp2_threshold:
        reason = "tp2"
    elif p["status"] == "open" and premium_mid <= tp1_threshold:
        # TP1: mark half-closed for tracking. PnL accounting: the backtest
        # does not model partial closes — it records full PnL at TP2/SL.
        # To match the backtest exactly, we do NOT halve contracts at TP1.
        # The status marker is informational; the full position closes later.
        paper_repo.mark_half_closed(int(p["id"]), now_ms)
        print(f"[paper] #{p['id']} TP1 @ mid ${premium_mid:.2f} (entry ${entry_credit:.2f})",
              flush=True)
        return True

    if reason is None:
        return False

    return _do_close(p, premium_mid, reason, now_ms)


def force_close_all(open_pos: list[dict], spot: float, chain_dict: dict[str, dict] | None) -> int:
    """Mission Control emergency flatten — closes every open position at its
    current mark (BS fallback allowed here, unlike the normal TP/SL check,
    since this is an explicit manual action, not an automated trigger).
    Returns count actually closed (a live close can fail to fill and is left
    open, same as the normal monitor path).

    Each position is isolated: one bad close must not abort the rest of the
    flatten, and must not propagate up to abort that tick's normal TP/SL
    monitoring of OTHER positions or the equity snapshot — exactly the
    opposite of what an emergency "get me out" command should do.
    """
    n = 0
    for p in open_pos:
        try:
            premium_mid = current_mark_or_bs(p, spot, chain_dict)
            if _do_close(p, premium_mid, "manual_close_all", int(time.time() * 1000)):
                n += 1
        except Exception:  # noqa: BLE001
            print(f"[paper] ERROR force-closing #{p.get('id')}:\n{traceback.format_exc()}",
                  flush=True)
            _report_loop_error(f"force-close #{p.get('id')}")
    return n


def _do_close(p: dict, premium_mid: float, reason: str, now_ms: int) -> bool:
    """Apply exit-side friction, record close, notify.

    Uses full contracts for PnL to match the backtest exactly.
    The backtest does not model partial closes at TP1 — it records full PnL
    at TP2/SL/time-stop. Even if TP1 fired earlier (status=half_closed_tp1),
    we close the full position so PnL accounting matches the validated numbers.
    """
    entry_credit = float(p["entry_credit_usd"])
    contracts = float(p["contracts"])
    notional = float(p["strike"]) * contracts

    if broker.is_live():
        # Real money: buy-to-close on the exchange. If it does NOT confirm filled,
        # return False and leave the position open so the DB never claims a close
        # the exchange didn't make (reconciler P5 is the backstop for divergence).
        symbol = (p.get("signal_payload") or {}).get("symbol")
        if not symbol:
            print(f"[paper] LIVE close abort #{p['id']} — no symbol on position", flush=True)
            return False
        fill = broker.live_close(symbol, contracts, premium_mid)
        if fill is None:
            return False
        exit_fee = fill.fee
        exit_debit_net = fill.avg_price + (exit_fee / max(contracts, 1e-9))
    else:
        exit_debit_gross = apply_exit_spread(premium_mid)
        premium_paid_total = exit_debit_gross * contracts
        exit_fee = fee_per_side(notional, premium_paid_total)
        exit_debit_net = exit_debit_gross + (exit_fee / max(contracts, 1e-9))

    pnl_per_contract = entry_credit - exit_debit_net
    pnl_usd = pnl_per_contract * contracts
    pnl_pct = (pnl_per_contract / entry_credit) * 100 if entry_credit > 0 else 0

    paper_repo.close_position(
        int(p["id"]),
        closed_at_ms=now_ms,
        exit_debit_usd=exit_debit_net,
        pnl_pct=pnl_pct,
        pnl_usd=pnl_usd,
        exit_reason=reason,
    )
    res = record_trade_result(pnl_pct)
    print(f"[paper] CLOSED #{p['id']} reason={reason} "
          f"mid=${premium_mid:.2f} debit_net=${exit_debit_net:.2f} fee=${exit_fee:.3f}  "
          f"pnl={pnl_pct:+.2f}% (${pnl_usd:+.2f})", flush=True)

    stats_after = paper_repo.position_stats()
    state_now = paper_repo.get_state() or {}
    start_eq = float(state_now.get("start_equity_usd") or START_EQUITY_USD)
    equity_after = start_eq + float(stats_after["realized_usd"])
    telegram_notify.notify_close(
        pid=int(p["id"]), side=p["side"], strike=float(p["strike"]),
        reason=reason, pnl_pct=pnl_pct, pnl_usd=pnl_usd,
        equity_after=equity_after,
        hold_h=int(p.get("hold_h") or 0),
    )
    if int(res.get("cb_cooldown_until_ms") or 0) > now_ms:
        telegram_notify.notify_cb_triggered(equity_after=equity_after)
    return True


# ───────────────────── main loop ───────────────────────────────────

# Throttled error alerting: a loop error should surface to the user fast (the
# close_position bug ran silently for 3 days). Telegram at most once per window.
_err_state = {"count": 0, "last_alert_ms": 0}
_ERR_ALERT_THROTTLE_MS = 30 * 60 * 1000  # 30 min between error alerts

# Separate throttle for the close-all "stragglers, retrying" alert — without
# it, a permanently-unclosable position (delisted contract, sustained
# exchange outage) would re-alert every poll tick forever (every ~30s).
_close_all_alert_state = {"last_alert_ms": 0}
_CLOSE_ALL_ALERT_THROTTLE_MS = 30 * 60 * 1000


def _report_close_all_stuck(n_closed: int, n_target: int) -> None:
    now_ms = int(time.time() * 1000)
    if now_ms - _close_all_alert_state["last_alert_ms"] < _CLOSE_ALL_ALERT_THROTTLE_MS:
        return
    _close_all_alert_state["last_alert_ms"] = now_ms
    telegram_notify.notify(
        f"⚠️ ETH signal bot close-all: only {n_closed}/{n_target} closed — still retrying "
        f"every tick. If this persists, check the position manually on Bybit.",
        silent=False)


def _report_loop_error(where: str) -> None:
    """Count loop errors; Telegram-alert (throttled) so silent failures surface."""
    _err_state["count"] += 1
    now_ms = int(time.time() * 1000)
    if now_ms - _err_state["last_alert_ms"] < _ERR_ALERT_THROTTLE_MS:
        return
    _err_state["last_alert_ms"] = now_ms
    try:
        telegram_notify.notify(
            f"⚠️ paper-loop error (#{_err_state['count']} since start): {where}. "
            f"Бот может не торговать — проверь логи.", silent=False)
    except Exception:  # noqa: BLE001
        pass  # telemetry must never break the loop


def _live_preopen_block(now_ms: int) -> str | None:
    """P4 live-only gate: return a reject-reason if a real open must be blocked
    right now (kill-switch / daily realized-loss limit), else None. Paper mode
    never calls this."""
    if cfg.killswitch_engaged():
        return "killswitch"
    if reconcile.is_blocked():
        return "unreconciled"
    day_start = live_safety.utc_day_start_ms(now_ms)
    realized_today = paper_repo.realized_pnl_since(day_start)
    if live_safety.daily_loss_limit_hit(realized_today):
        return "daily_loss_limit"
    return None


def window_id(epoch_min: int) -> int:
    """5m-window id: floor(minute / 5). minute % 5 gives position 0..4 in window."""
    return epoch_min // SIGNAL_CHECK_EVERY_MIN


def at_position_cap(open_count: int) -> bool:
    """Tail-risk concentration cap: True if we already hold the max number of
    simultaneous positions (open + half_closed_tp1) and must refuse new opens.
    cfg.MAX_OPEN_POSITIONS == 0 disables the cap. Validated OOS by
    tail_overlay_sweep.py (commit 987efab)."""
    return bool(cfg.MAX_OPEN_POSITIONS) and open_count >= cfg.MAX_OPEN_POSITIONS


def conditions_ready(k5, k15, k1h) -> tuple[bool, dict]:
    """Live entry readiness — same booleans the dashboard 'Условия входа' dots show."""
    ev = evaluate_conditions(k5, k15, k1h)
    ready = bool(ev.get("ready")) and ev.get("active_side") is not None
    return ready, ev


async def loop():
    apply_schema()
    state = paper_repo.ensure_state(START_EQUITY_USD)
    print(f"[paper] schema ready, start_equity=${state['start_equity_usd']}, "
          f"poll={POLL_INTERVAL_S}s, V2 trend-following: "
          f"ret>+{RET_7D_THRESHOLD}%→Put, ret<-{RET_7D_THRESHOLD}%→Call, range→both", flush=True)

    # P5: live reconcile on startup (and a live-start banner). Inert in paper.
    last_reconcile_ms = 0
    if broker.is_live():
        telegram_notify.notify_trader_start(
            mode=cfg.TRADING_MODE, armed=True, wallet_usdt=broker.wallet_equity_usdt())
        reconcile.reconcile_once()
        last_reconcile_ms = int(time.time() * 1000)

    # Per-window persistence state for the debounced entry:
    cur_window_id = -1
    window_disqualified = False   # any per-minute check in this window failed → no entry
    window_fired = False          # already opened (or attempted) in this window
    window_audited = False        # observation row already persisted for this window
    last_minute_eval = -1         # epoch-minute of the last per-minute condition eval

    while True:
        try:
            spot = bybit_client.get_spot_price(SPOT_SYMBOL)
            if spot <= 0:
                print("[paper] WARN: spot price unavailable, skipping iteration", flush=True)
                await asyncio.sleep(POLL_INTERVAL_S)
                continue

            state = paper_repo.get_state() or state

            # P5: periodic live reconcile (exchange wins; blocks opens on drift). Inert in paper.
            if broker.is_live():
                tick_ms = int(time.time() * 1000)
                if tick_ms - last_reconcile_ms >= cfg.RECONCILE_EVERY_MIN * 60_000:
                    reconcile.reconcile_once()
                    last_reconcile_ms = tick_ms

            # Fetch option chain ONCE per tick, lazily — reused for both position
            # monitoring and (if a signal fires later this tick) opening, so we
            # never hit Bybit twice for the same tick's chain.
            open_pos_now = paper_repo.open_positions()
            chain: list[dict] | None = None
            chain_dict: dict[str, dict] | None = None
            if open_pos_now:
                try:
                    chain = bybit_client.get_options_tickers(BASE_COIN)
                    chain_dict = {
                        f"{o.get('side')}-{int(o.get('strike'))}-{o.get('expiry_ms')}": o
                        for o in chain
                        if o.get('side') and o.get('strike') and o.get('expiry_ms')
                    }
                except Exception as e:  # noqa: BLE001
                    print(f"[paper] WARN: chain fetch failed: {e!r}", flush=True)

            # 0) Mission Control emergency flatten — takes priority over the
            # normal TP/SL/time-stop monitor pass below.
            if control_repo.is_close_all_requested(BOT_NAME):
                if broker.is_live() and reconcile.is_blocked():
                    telegram_notify.notify(
                        "⚠️ ETH signal bot close-all: DB/exchange state is UNRECONCILED — "
                        "proceeding on DB-tracked positions anyway, but VERIFY MANUALLY on Bybit",
                        silent=False)
                n_target = len(open_pos_now)
                n_closed = force_close_all(open_pos_now, spot, chain_dict)
                print(f"[paper] Mission Control close-all: closed {n_closed}/{n_target}",
                      flush=True)
                if n_closed >= n_target:
                    control_repo.clear_close_all_requested(BOT_NAME)
                else:
                    _report_close_all_stuck(n_closed, n_target)
                open_pos_now = paper_repo.open_positions()

            # 1) Position monitoring — every iteration. Each position is isolated:
            #    a failure on one (e.g. a bad close) must NOT abort monitoring of
            #    the others, the equity snapshot, or signal evaluation below.
            for p in open_pos_now:
                try:
                    check_and_close_position(p, spot, chain_dict)
                except Exception:  # noqa: BLE001
                    print(f"[paper] ERROR closing #{p.get('id')}:\n{traceback.format_exc()}",
                          flush=True)
                    _report_loop_error(f"close #{p.get('id')}")

            # 2) Entry conditions — evaluate every minute, debounce across the 5m
            #    window, commit the open near the candle close (~:50 of last minute).
            now = datetime.now(timezone.utc)
            epoch_min = int(time.time() // 60)
            wid = window_id(epoch_min)
            min_in_window = epoch_min % SIGNAL_CHECK_EVERY_MIN  # 0..4

            if wid != cur_window_id:
                cur_window_id = wid
                window_disqualified = False
                window_fired = False
                window_audited = False
                last_minute_eval = -1

            # 2a) Per-minute condition check (once per distinct minute). A single
            #     failed check disqualifies the whole window (persistence rule).
            if epoch_min != last_minute_eval:
                last_minute_eval = epoch_min
                k5_m, k15_m, k1h_m = load_klines_for_generator()
                if len(k5_m) >= BARS_7D + 50:
                    minute_ready, ev_m = conditions_ready(k5_m, k15_m, k1h_m)
                else:
                    minute_ready, ev_m = False, {}
                if not minute_ready:
                    window_disqualified = True
                print(f"[paper] cond w{wid} m{min_in_window}: ready={minute_ready} "
                      f"side={ev_m.get('active_side')} regime={ev_m.get('regime')} "
                      f"mtf={ev_m.get('mtf_direction')}/{ev_m.get('mtf_aligned_count')} "
                      f"vol={ev_m.get('vol_high')} disq={window_disqualified}", flush=True)

                # Persist one observation row per disqualified window so the audit
                # trail is complete during long range-less stretches (the fire-time
                # write below only runs for windows that survive to the open). One
                # row per window; the full eval (regime/side/mtf/vol/…) goes into
                # signal_payload. Observability only — a failed write must not stop
                # trading, so it rides the loop's outer try/except.
                if window_disqualified and not window_audited:
                    window_audited = True
                    paper_repo.insert_signal_audit(
                        ts_ms=int(time.time() * 1000),
                        ret_7d=float(ev_m.get("ret_7d") or 0),
                        active_side=ev_m.get("active_side"),
                        dead_zone=bool(ev_m.get("dead_zone")),
                        signal_generated=False, accepted=None,
                        reject_reason="disqualified",
                        spot=float(ev_m.get("spot") or 0),
                        signal_payload=ev_m or None,
                    )

            # 2b) Fire the open near the candle close, once per window, only if every
            #     per-minute check in this window passed.
            fire_now = (min_in_window == SIGNAL_CHECK_EVERY_MIN - 1
                        and now.second >= ENTRY_FIRE_SECOND
                        and not window_fired
                        and not window_disqualified)
            if fire_now:
                window_fired = True
                now_ms = int(time.time() * 1000)

                # Compute ret_7d and active side for audit
                k5_audit, k15_audit, k1h_audit = load_klines_for_generator()
                ret_7d_val = compute_ret_7d(k5_audit, len(k5_audit) - 1) if len(k5_audit) >= BARS_7D else 0
                side_val = determine_side(ret_7d_val) if len(k5_audit) >= BARS_7D else None
                dead_zone_val = side_val is None
                spot_val = k5_audit[-1]["close"] if k5_audit else spot

                # Also check close_all_requested directly (not just paused): if an
                # operator resumes while stragglers from an emergency flatten are
                # still being retried (request_close_all sets paused=true, but
                # control_resume only clears paused, not close_all_requested), a
                # new entry must still not open on top of an in-flight flatten.
                if control_repo.is_paused(BOT_NAME) or control_repo.is_close_all_requested(BOT_NAME):
                    print("[paper] paused/close-all via Mission Control — no new entries", flush=True)
                    paper_repo.insert_signal_audit(
                        ts_ms=now_ms, ret_7d=ret_7d_val, active_side=side_val,
                        dead_zone=dead_zone_val, signal_generated=False,
                        accepted=False, reject_reason="paused", spot=spot_val,
                        signal_payload=None)
                elif is_cb_active(state, now_ms):
                    cb_remaining_h = (int(state["cb_cooldown_until_ms"]) -
                                       now_ms) / 3_600_000
                    print(f"[paper] CB cooldown active ({cb_remaining_h:.1f}h left), no signals",
                          flush=True)
                    paper_repo.insert_signal_audit(
                        ts_ms=now_ms, ret_7d=ret_7d_val, active_side=side_val,
                        dead_zone=dead_zone_val, signal_generated=False,
                        accepted=False, reject_reason="cb_active", spot=spot_val,
                        signal_payload=None)
                elif len(k5_audit) < BARS_7D + 50:
                    print(f"[paper] not enough klines yet ({len(k5_audit)} 5m), skip signal check",
                          flush=True)
                    paper_repo.insert_signal_audit(
                        ts_ms=now_ms, ret_7d=ret_7d_val, active_side=side_val,
                        dead_zone=dead_zone_val, signal_generated=False,
                        accepted=None, reject_reason="no_signal", spot=spot_val,
                        signal_payload=None)
                else:
                    sig = check_new_signal(k5_audit, k15_audit, k1h_audit)
                    if sig and broker.is_live():
                        # P4: block live opens on kill-switch / daily-loss limit.
                        blocked = _live_preopen_block(now_ms)
                        if blocked:
                            print(f"[paper] LIVE skip signal — {blocked}", flush=True)
                            telegram_notify.notify(f"⛔ Live open blocked: {blocked}")
                            paper_repo.insert_signal_audit(
                                ts_ms=now_ms, ret_7d=ret_7d_val, active_side=sig.get("active_side"),
                                dead_zone=False, signal_generated=True, accepted=False,
                                reject_reason=blocked, spot=spot_val, signal_payload=sig)
                            sig = None
                    if sig and at_position_cap(len(open_pos_now)):
                        # Tail-risk concentration cap (validated OOS by tail_overlay_sweep,
                        # commit 987efab): refuse new opens once we already hold the max
                        # number of simultaneous positions. Cuts negative-EV cluster trades
                        # that blow up the worst month. Counts open + half_closed_tp1 (1=1 slot).
                        print(f"[paper] skip signal — concentration cap "
                              f"({len(open_pos_now)} open >= MAX_OPEN_POSITIONS="
                              f"{cfg.MAX_OPEN_POSITIONS})", flush=True)
                        paper_repo.insert_signal_audit(
                            ts_ms=now_ms, ret_7d=ret_7d_val, active_side=sig.get("active_side"),
                            dead_zone=False, signal_generated=True, accepted=False,
                            reject_reason="max_open_positions", spot=spot_val,
                            signal_payload=sig)
                    elif sig:
                        eq = compute_equity(state, spot, chain_dict)
                        locked_margin = sum(float(p["size_usd"]) for p in open_pos_now)
                        free_margin = (eq["equity"] * MAX_PORTFOLIO_MARGIN_PCT) - locked_margin

                        if free_margin <= 0:
                            print(f"[paper] skip signal — portfolio margin maxed out "
                                  f"(locked ${locked_margin:.2f} >= limit ${eq['equity']*MAX_PORTFOLIO_MARGIN_PCT:.2f})",
                                  flush=True)
                            paper_repo.insert_signal_audit(
                                ts_ms=now_ms, ret_7d=ret_7d_val, active_side=sig.get("active_side"),
                                dead_zone=False, signal_generated=True, accepted=False,
                                reject_reason="insufficient_margin", spot=spot_val,
                                signal_payload=sig)
                        else:
                            if chain is None:
                                try:
                                    chain = bybit_client.get_options_tickers(BASE_COIN)
                                except Exception as e:  # noqa: BLE001
                                    print(f"[paper] WARN: chain fetch failed: {e!r}", flush=True)
                                    chain = []
                            pid = open_paper_position(sig, spot, eq["equity"], free_margin, state, chain)
                            paper_repo.insert_signal_audit(
                                ts_ms=now_ms, ret_7d=ret_7d_val, active_side=sig.get("active_side"),
                                dead_zone=False, signal_generated=True, accepted=pid is not None,
                                reject_reason=None if pid else "no_option", spot=spot_val,
                                signal_payload=sig)
                    else:
                        print(f"[paper] tick: no signal (spot=${spot_val:.2f}, 7d_ret={ret_7d_val:+.2f}%, "
                              f"side={side_val or '?'}, open={len(open_pos_now)})", flush=True)
                        paper_repo.insert_signal_audit(
                            ts_ms=now_ms, ret_7d=ret_7d_val, active_side=side_val,
                            dead_zone=dead_zone_val, signal_generated=False,
                            accepted=None, reject_reason="no_signal", spot=spot_val,
                            signal_payload=None)

            # 3) Equity snapshot — every iteration
            eq = compute_equity(state, spot, chain_dict)
            started_at = int(state.get("started_at_ms") or 0)
            peak = paper_repo.peak_equity_since(started_at) or eq["equity"]
            peak_eff = max(peak, eq["equity"], float(state.get("start_equity_usd") or 0))
            max_dd_pct = ((peak_eff - eq["equity"]) / peak_eff * 100.0) if peak_eff > 0 else 0.0
            paper_repo.insert_equity_snapshot(
                ts_ms=int(time.time() * 1000),
                equity_usd=eq["equity"],
                realized_usd=eq["realized"],
                unrealized_usd=eq["unrealized"],
                n_open=eq["n_open"],
                n_closed=eq["n_closed"],
                max_dd_pct=round(max_dd_pct, 4),
            )

        except Exception as e:  # noqa: BLE001
            # Log the FULL traceback — `repr(e)` alone once hid a fatal
            # close_position bug for 3 days (see git 52f9dc6). A bare message
            # tells you nothing about WHERE the failure is.
            print(f"[paper] error: {e!r}\n{traceback.format_exc()}", flush=True)
            _report_loop_error(repr(e))

        # Adaptive sleep: in the window's last minute, wake right at the fire
        # instant (~:50) so we don't overshoot it with the coarse poll interval.
        sleep_s = POLL_INTERVAL_S
        _now = datetime.now(timezone.utc)
        if (int(time.time() // 60) % SIGNAL_CHECK_EVERY_MIN == SIGNAL_CHECK_EVERY_MIN - 1
                and _now.second < ENTRY_FIRE_SECOND):
            sleep_s = min(sleep_s, ENTRY_FIRE_SECOND - _now.second)
        await asyncio.sleep(max(1, sleep_s))


if __name__ == "__main__":
    asyncio.run(loop())

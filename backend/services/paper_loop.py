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
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import paper_repo  # noqa: E402
from db.engine import apply_schema  # noqa: E402
from db.repository import recent_klines  # noqa: E402
from services import backtest_bs as bs  # noqa: E402
from services.bybit_client import bybit_client  # noqa: E402
from services.strategy_config import (  # noqa: E402
    RET_7D_THRESHOLD,
    get_side_gen_kwargs,
)
from services.paper_strategy import (  # noqa: E402
    BARS_7D,
    CB_CONSEC_LIMIT,
    CB_PAUSE_HOURS,
    DEFAULT_SIGMA,
    EXPIRY_TARGET_HOURS,
    LOT_MIN_ETH,
    MAX_PORTFOLIO_MARGIN_PCT,
    START_EQUITY_USD,
    allowed_sides,
    apply_entry_spread,
    apply_exit_spread,
    compute_ret_7d,
    determine_side,
    fee_per_side,
    is_cb_active,
    margin_per_lot,
    realistic_size_lots,
    record_trade_result,
)
from services.strategy_registry import gen_sell_premium_iv_high  # noqa: E402
from services import telegram_notify  # noqa: E402


POLL_INTERVAL_S = int(os.getenv("PAPER_POLL_INTERVAL", "30"))
SIGNAL_CHECK_EVERY_MIN = 5
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

def open_paper_position(signal: dict, spot: float, equity_usd: float, free_margin_usd: float, state: dict) -> int | None:
    """Open a paper position for a signal — Bybit-realistic sizing/friction.

    Uses per-side exit params (PUT_EXIT for P, CALL_EXIT for C).
    """
    active_side = signal.get("active_side", signal.get("side", "P"))
    ex_kw = exit_for_side(active_side)

    chain = bybit_client.get_options_tickers(BASE_COIN)
    pick = pick_bybit_atm_option(chain, spot, EXPIRY_TARGET_HOURS, active_side)

    if pick and pick.get("bid", 0) > 0 and pick.get("ask", 0) > 0:
        strike = float(pick["strike"])
        expiry_ms = int(pick["expiry_ms"])
        premium_mid = (float(pick["bid"]) + float(pick["ask"])) / 2.0
        entry_source = "bybit"
        symbol = pick["symbol"]
    else:
        strike = round(spot / STRIKE_GRID) * STRIKE_GRID
        expiry_ms = int(time.time() * 1000) + EXPIRY_TARGET_HOURS * 3_600_000
        premium_mid = price_option_bs(active_side, spot, strike, expiry_ms, DEFAULT_SIGMA)
        if premium_mid <= 0:
            print(f"[paper] open skipped — could not price option", flush=True)
            return None
        entry_source = "bs_fallback"
        symbol = f"ETH-?-{int(strike)}-{active_side}"

    n_lots = realistic_size_lots(free_margin_usd, equity_usd, strike, premium_mid, state)
    if n_lots < 1:
        m_per_lot = margin_per_lot(strike, premium_mid)
        print(f"[paper] open skipped — insufficient margin "
              f"(need ${m_per_lot:.2f}/lot, have ${equity_usd:.2f} equity)", flush=True)
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
        sl_pct=ex_kw["sl_pct"],
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
          f"margin=${margin_locked:.2f}  fee=${entry_fee:.3f}  source={entry_source}  "
          f"side={active_side} 7d_ret={signal.get('ret_7d', 0):+.2f}%",
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

def is_signal_check_time(last_check_ms: int) -> bool:
    """Trigger every 5 min, around the top of a 5m candle close."""
    now = datetime.now(timezone.utc)
    if (now.minute % SIGNAL_CHECK_EVERY_MIN) != 0:
        return False
    return (int(time.time() * 1000) - last_check_ms) >= 4 * 60 * 1000


async def loop():
    apply_schema()
    state = paper_repo.ensure_state(START_EQUITY_USD)
    print(f"[paper] schema ready, start_equity=${state['start_equity_usd']}, "
          f"poll={POLL_INTERVAL_S}s, V2 trend-following: "
          f"ret>+{RET_7D_THRESHOLD}%→Put, ret<-{RET_7D_THRESHOLD}%→Call, range→both", flush=True)

    last_signal_check_ms = 0

    while True:
        try:
            spot = bybit_client.get_spot_price(SPOT_SYMBOL)
            if spot <= 0:
                print("[paper] WARN: spot price unavailable, skipping iteration", flush=True)
                await asyncio.sleep(POLL_INTERVAL_S)
                continue

            state = paper_repo.get_state() or state

            # Fetch option chain ONCE per tick
            open_pos_now = paper_repo.open_positions()
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

            # 1) Position monitoring — every iteration
            for p in open_pos_now:
                check_and_close_position(p, spot, chain_dict)

            # 2) Signal check — every 5 min
            if is_signal_check_time(last_signal_check_ms):
                last_signal_check_ms = int(time.time() * 1000)
                now_ms = last_signal_check_ms

                # Compute ret_7d and active side for audit
                k5_audit, k15_audit, k1h_audit = load_klines_for_generator()
                ret_7d_val = compute_ret_7d(k5_audit, len(k5_audit) - 1) if len(k5_audit) >= BARS_7D else 0
                side_val = determine_side(ret_7d_val) if len(k5_audit) >= BARS_7D else None
                dead_zone_val = side_val is None
                spot_val = k5_audit[-1]["close"] if k5_audit else spot

                if is_cb_active(state, now_ms):
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
                    if sig:
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
                            pid = open_paper_position(sig, spot, eq["equity"], free_margin, state)
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
            print(f"[paper] error: {e!r}", flush=True)

        await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(loop())

"""BTC unconditional short-straddle paper bot — runs as a separate docker
container from the ETH V3 book, against its own DB tables (btc_straddle_*).

Cycle-driven, NOT signal-driven (unlike paper_loop.py): every CYCLE_H (24h)
boundary, sell one ATM call + one ATM put, no entry filter — pure variance-risk-
premium harvesting. Strategy/parameters per BTC_STRADDLE_HANDOFF.md.

Paper vs. live is the SAME script, gated on ``broker.is_live()`` exactly like
paper_loop.py — going live later is a docker-compose service + env flip, not a
rewrite. The live path reuses the shared execution stack (broker/live_sizing/
live_safety/reconcile), generalized with explicit base_coin="BTC"/lot_size=0.01
args so the ETH defaults in those modules are unaffected.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import btc_straddle_repo as repo  # noqa: E402
from db import control_repo  # noqa: E402  (Mission Control pause/close-all flags)
from db.engine import apply_schema  # noqa: E402
from db.repository import recent_klines  # noqa: E402
from services import backtest_bs as bs  # noqa: E402
from services import broker  # noqa: E402  (live order routing; inert in paper mode)
from services import btc_straddle_sl as sl  # noqa: E402
from services import execution_config as cfg  # noqa: E402
from services import live_safety  # noqa: E402  (inert in paper mode)
from services import reconcile  # noqa: E402  (inert in paper mode)
from services.bybit_client import bybit_client  # noqa: E402
from services import telegram_notify  # noqa: E402


POLL_INTERVAL_S = int(os.getenv("BTC_STRADDLE_POLL_INTERVAL", "30"))
START_EQUITY_USD = float(os.getenv("BTC_STRADDLE_START_EQUITY_USD", "2000"))
MARGIN_PCT_PER_CYCLE = float(os.getenv("BTC_STRADDLE_MARGIN_PCT", "0.15"))

BOT_NAME = "btc_straddle"
SPOT_SYMBOL = "BTCUSDT"
BASE_COIN = "BTC"
STRIKE_ROUND = 500.0  # $ — Bybit's near-term BTC strike step
CYCLE_MS = int(sl.CYCLE_H * 3_600_000)
TARGET_EXPIRY_H = sl.CYCLE_H
SIGMA_CLAMP = (0.20, 1.50)
IV_RV_MULT = 1.10
SPREAD_HALF_PCT = 1.0       # half of handoff's 2.0% round-trip spread assumption
FEE_RATE = 0.0003           # Bybit option taker fee (same schedule as ETH, exchange-level)
FEE_CAP_PCT_OF_PREMIUM = 0.125
RV_WINDOW_H = 168


# ───────────────────── option pricing (live + fallback) ─────────────

def pick_bybit_atm_option(chain: list[dict], spot: float, option_side: str) -> dict | None:
    """From the live Bybit BTC chain, pick the ATM call or put nearest CYCLE_H out."""
    now_ms = int(time.time() * 1000)
    target_ms = now_ms + int(TARGET_EXPIRY_H * 3_600_000)
    candidates = [
        o for o in chain
        if o.get("side") == option_side
        and o.get("expiry_ms", 0) > now_ms + 3 * 3_600_000
        and (o.get("bid") or 0) > 0
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda o: abs((o.get("expiry_ms") or 0) - target_ms))
    by_expiry = candidates[0].get("expiry_ms")
    same_expiry = [o for o in candidates if o.get("expiry_ms") == by_expiry]
    target_strike = round(spot / STRIKE_ROUND) * STRIKE_ROUND
    same_expiry.sort(key=lambda o: abs((o.get("strike") or 0) - target_strike))
    return same_expiry[0] if same_expiry else None


def price_option_bs(side: str, spot: float, strike: float, expiry_ms: int, sigma: float) -> float:
    now_ms = int(time.time() * 1000)
    T_years = max(1 / 365 / 24, (expiry_ms - now_ms) / 1000 / 86400 / 365)
    return bs.price(side, spot, strike, T_years, sigma)


def current_mark(side: str, strike: float, expiry_ms: int,
                 chain_dict: dict[str, dict] | None) -> float | None:
    if chain_dict:
        key = f"{side}-{int(strike)}-{expiry_ms}"
        q = chain_dict.get(key)
        if q:
            ask = float(q.get("ask", 0) or 0)
            if ask > 0:
                return ask  # short-position MtM/SL math wants the real buyback cost
    return None


def trailing_sigma() -> float:
    """Entry vol = trailing-168h realized vol on BTCUSDT 1h closes × IV_RV_MULT,
    clamped — same methodology as the backtest harnesses (btc_straddle_*.py)."""
    import math
    kl = recent_klines(SPOT_SYMBOL, "1h", limit=RV_WINDOW_H + 1)
    closes = [float(k["close"]) for k in reversed(kl)]
    if len(closes) < 2:
        return SIGMA_CLAMP[0]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 2:
        return SIGMA_CLAMP[0]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    hourly_vol = var ** 0.5
    annualized_rv = hourly_vol * math.sqrt(24 * 365)
    sigma = annualized_rv * IV_RV_MULT
    return max(SIGMA_CLAMP[0], min(SIGMA_CLAMP[1], sigma))


# ───────────────────── friction model (paper fills only) ────────────

def apply_entry_spread(premium_mid: float) -> float:
    return premium_mid * (1 - SPREAD_HALF_PCT / 100.0)


def apply_exit_spread(premium_mid: float) -> float:
    return premium_mid * (1 + SPREAD_HALF_PCT / 100.0)


def fee_per_side(notional_usd: float, premium_total_usd: float) -> float:
    cap = abs(premium_total_usd) * FEE_CAP_PCT_OF_PREMIUM
    return min(notional_usd * FEE_RATE, cap)


# ───────────────────── equity computation ──────────────────────────

def compute_equity(state: dict, spot: float, chain_dict: dict[str, dict] | None) -> dict:
    start_equity = float(state["start_equity_usd"])
    stats = repo.position_stats()
    realized = float(stats["realized_usd"])

    if broker.is_live():
        wallet = broker.wallet_equity_usdt()
        if wallet is not None:
            return {
                "equity": wallet, "realized": realized,
                "unrealized": wallet - start_equity - realized,
                "n_open": stats["n_open"], "n_closed": stats["n_closed"],
            }

    open_pos = repo.open_positions()
    unrealized = 0.0
    for p in open_pos:
        contracts = float(p["contracts"])
        mark = current_mark(p["leg"], float(p["strike"]), int(p["expiry_ms"]), chain_dict)
        if mark is None:
            mark = price_option_bs(p["leg"], spot, float(p["strike"]), int(p["expiry_ms"]), trailing_sigma())
        unrealized += (float(p["entry_credit_usd"]) - mark) * contracts

    equity = start_equity + realized + unrealized
    return {"equity": equity, "realized": realized, "unrealized": unrealized,
            "n_open": stats["n_open"], "n_closed": stats["n_closed"]}


# ───────────────────── open / close one leg ─────────────────────────

def open_leg(cycle_id: int, leg: str, spot: float, equity_usd: float,
            chain: list[dict]) -> int | None:
    pick = pick_bybit_atm_option(chain, spot, leg)
    sigma = None  # only computed below if the bs_fallback path actually needs it

    if pick and pick.get("bid", 0) > 0 and pick.get("ask", 0) > 0:
        strike = float(pick["strike"])
        expiry_ms = int(pick["expiry_ms"])
        premium_mid = (float(pick["bid"]) + float(pick["ask"])) / 2.0
        entry_source = "bybit"
        symbol = pick["symbol"]
    else:
        sigma = trailing_sigma()
        strike = round(spot / STRIKE_ROUND) * STRIKE_ROUND
        expiry_ms = int(time.time() * 1000) + int(TARGET_EXPIRY_H * 3_600_000)
        premium_mid = price_option_bs(leg, spot, strike, expiry_ms, sigma)
        if premium_mid <= 0:
            print(f"[btc_straddle] open {leg} skipped — could not price option", flush=True)
            return None
        entry_source = "bs_fallback"
        symbol = f"BTC-?-{int(strike)}-{leg}"

    margin_lot = sl.margin_per_lot(strike, premium_mid)
    sl_trip = sl.sl_dollar_trip(margin_lot)

    if broker.is_live():
        if entry_source != "bybit":
            print(f"[btc_straddle] LIVE open {leg} skip — no real Bybit instrument", flush=True)
            return None
        if not live_safety.spread_ok(float(pick.get("bid") or 0), float(pick.get("ask") or 0)):
            print(f"[btc_straddle] LIVE open {leg} skip — illiquid spread", flush=True)
            return None
        fill = broker.live_open(symbol, strike, premium_mid, lot_size=sl.LOT_BTC)
        if fill is None:
            telegram_notify.notify_skipped_margin(spot=spot, strike=strike,
                                                   need_usd=margin_lot, have_usd=equity_usd,
                                                   asset="BTC")
            return None
        contracts = fill.qty_eth
        entry_fee = fill.fee
        entry_credit_net = fill.avg_price - (entry_fee / max(contracts, 1e-9))
        margin_locked = margin_lot * fill.n_lots
        entry_source = "bybit_live"
    else:
        budget_per_leg = equity_usd * MARGIN_PCT_PER_CYCLE / 2.0
        n_lots = int(budget_per_leg // margin_lot) if margin_lot > 0 else 0
        if n_lots < 1:
            print(f"[btc_straddle] open {leg} skipped — insufficient margin "
                  f"(need ${margin_lot:.2f}/lot, budget ${budget_per_leg:.2f})", flush=True)
            telegram_notify.notify_skipped_margin(spot=spot, strike=strike,
                                                   need_usd=margin_lot, have_usd=budget_per_leg,
                                                   asset="BTC")
            return None
        contracts = n_lots * sl.LOT_BTC
        notional = strike * contracts
        entry_credit_gross = apply_entry_spread(premium_mid)
        premium_total = entry_credit_gross * contracts
        entry_fee = fee_per_side(notional, premium_total)
        entry_credit_net = entry_credit_gross - (entry_fee / max(contracts, 1e-9))
        margin_locked = margin_lot * n_lots

    entry_credit_pct = (entry_credit_net / spot * 100) if spot > 0 else 0
    pid = repo.open_position(
        cycle_id=cycle_id, leg=leg, opened_at_ms=int(time.time() * 1000),
        underlying_at_open=spot, strike=strike, expiry_ms=expiry_ms,
        contracts=contracts, size_usd=margin_locked,
        entry_credit_usd=entry_credit_net, entry_credit_pct=entry_credit_pct,
        entry_source=entry_source, margin_per_lot_usd=margin_lot,
        sl_dollar_trip_usd=sl_trip,
        signal_payload={"symbol": symbol, "premium_mid": round(premium_mid, 4),
                        "sigma": round(sigma, 4) if sigma is not None else None,
                        "entry_fee_usd": round(entry_fee, 4)},
    )
    print(f"[btc_straddle] OPENED #{pid} cycle={cycle_id} SELL {symbol} "
          f"contracts={contracts:.4f}BTC credit_net=${entry_credit_net:.2f} "
          f"margin=${margin_locked:.2f} sl_trip=${sl_trip:.2f}/lot source={entry_source}", flush=True)
    telegram_notify.notify_open(
        pid=pid, symbol=symbol, side=leg, strike=strike, spot=spot,
        n_lots=int(round(contracts / sl.LOT_BTC)), contracts=contracts,
        premium_recv=entry_credit_net * contracts, margin_locked=margin_locked,
        entry_fee=entry_fee, source=entry_source, asset="BTC",
    )
    return pid


def check_and_close_position(p: dict, spot: float, chain_dict: dict[str, dict] | None) -> bool:
    now_ms = int(time.time() * 1000)
    age_h = (now_ms - int(p["opened_at_ms"])) / 3_600_000
    entry_credit = float(p["entry_credit_usd"])
    contracts = float(p["contracts"])

    if age_h >= sl.CYCLE_H:
        mark = current_mark(p["leg"], float(p["strike"]), int(p["expiry_ms"]), chain_dict)
        if mark is None:
            mark = price_option_bs(p["leg"], spot, float(p["strike"]), int(p["expiry_ms"]), trailing_sigma())
        return _do_close(p, mark, "time_stop", now_ms)

    mark = current_mark(p["leg"], float(p["strike"]), int(p["expiry_ms"]), chain_dict)
    if mark is None:
        return False  # no live data — wait for the time-stop, same caution as paper_loop

    if sl.is_tripped(entry_credit=entry_credit, current_buyback_ask=mark, qty=contracts,
                     sl_trip_per_lot_usd=float(p["sl_dollar_trip_usd"])):
        return _do_close(p, mark, "sl", now_ms)

    tp2_threshold = entry_credit * (1 - sl.TP2_PCT)
    if mark <= tp2_threshold:
        return _do_close(p, mark, "tp2", now_ms)

    return False


def force_close_all(open_pos: list[dict], spot: float, chain_dict: dict[str, dict] | None) -> int:
    """Mission Control emergency flatten — closes every open leg at its current
    mark (or BS fallback) regardless of SL/TP2/time-stop state. Returns count
    actually closed (a live close can fail to fill and is left open, same as
    the normal monitor path).

    Each leg is isolated: one bad leg (bad data, an exchange error) must not
    abort the rest of the flatten, and must not propagate up to abort that
    tick's normal SL/TP monitoring of OTHER positions or the equity snapshot —
    exactly the opposite of what an emergency "get me out" command should do.
    """
    n = 0
    for p in open_pos:
        try:
            mark = current_mark(p["leg"], float(p["strike"]), int(p["expiry_ms"]), chain_dict)
            if mark is None:
                mark = price_option_bs(p["leg"], spot, float(p["strike"]), int(p["expiry_ms"]), trailing_sigma())
            if _do_close(p, mark, "manual_close_all", int(time.time() * 1000)):
                n += 1
        except Exception:  # noqa: BLE001
            print(f"[btc_straddle] ERROR force-closing #{p.get('id')}:\n{traceback.format_exc()}",
                  flush=True)
            _report_loop_error(f"force-close #{p.get('id')}")
    return n


def _do_close(p: dict, mark: float, reason: str, now_ms: int) -> bool:
    entry_credit = float(p["entry_credit_usd"])
    contracts = float(p["contracts"])
    notional = float(p["strike"]) * contracts

    if broker.is_live():
        symbol = (p.get("signal_payload") or {}).get("symbol")
        if not symbol:
            print(f"[btc_straddle] LIVE close abort #{p['id']} — no symbol on position", flush=True)
            return False
        fill = broker.live_close(symbol, contracts, mark, lot_size=sl.LOT_BTC)
        if fill is None:
            return False
        exit_fee = fill.fee
        exit_debit_net = fill.avg_price + (exit_fee / max(contracts, 1e-9))
    else:
        exit_debit_gross = apply_exit_spread(mark)
        premium_paid = exit_debit_gross * contracts
        exit_fee = fee_per_side(notional, premium_paid)
        exit_debit_net = exit_debit_gross + (exit_fee / max(contracts, 1e-9))

    pnl_per_contract = entry_credit - exit_debit_net
    pnl_usd = pnl_per_contract * contracts
    pnl_pct = (pnl_per_contract / entry_credit) * 100 if entry_credit > 0 else 0

    repo.close_position(int(p["id"]), closed_at_ms=now_ms, exit_debit_usd=exit_debit_net,
                        pnl_pct=pnl_pct, pnl_usd=pnl_usd, exit_reason=reason)
    print(f"[btc_straddle] CLOSED #{p['id']} leg={p['leg']} reason={reason} "
          f"mark=${mark:.2f} debit_net=${exit_debit_net:.2f} fee=${exit_fee:.3f} "
          f"pnl={pnl_pct:+.2f}% (${pnl_usd:+.2f})", flush=True)

    stats_after = repo.position_stats()
    state_now = repo.get_state() or {}
    start_eq = float(state_now.get("start_equity_usd") or START_EQUITY_USD)
    equity_after = start_eq + float(stats_after["realized_usd"])
    telegram_notify.notify_close(
        pid=int(p["id"]), side=p["leg"], strike=float(p["strike"]), reason=reason,
        pnl_pct=pnl_pct, pnl_usd=pnl_usd, equity_after=equity_after, hold_h=int(sl.CYCLE_H),
    )
    return True


# ───────────────────── main loop ───────────────────────────────────

_err_state = {"count": 0, "last_alert_ms": 0}
_ERR_ALERT_THROTTLE_MS = 30 * 60 * 1000

# Separate throttle for the close-all "stragglers, retrying" alert — without
# it, a permanently-unclosable leg (delisted contract, sustained exchange
# outage) would re-alert every poll tick forever (every ~30s).
_close_all_alert_state = {"last_alert_ms": 0}
_CLOSE_ALL_ALERT_THROTTLE_MS = 30 * 60 * 1000


def _report_close_all_stuck(n_closed: int, n_target: int) -> None:
    now_ms = int(time.time() * 1000)
    if now_ms - _close_all_alert_state["last_alert_ms"] < _CLOSE_ALL_ALERT_THROTTLE_MS:
        return
    _close_all_alert_state["last_alert_ms"] = now_ms
    telegram_notify.notify(
        f"⚠️ BTC straddle close-all: only {n_closed}/{n_target} closed — still retrying "
        f"every tick. If this persists, check the position manually on Bybit.",
        silent=False)


def _report_loop_error(where: str) -> None:
    _err_state["count"] += 1
    now_ms = int(time.time() * 1000)
    if now_ms - _err_state["last_alert_ms"] < _ERR_ALERT_THROTTLE_MS:
        return
    _err_state["last_alert_ms"] = now_ms
    try:
        telegram_notify.notify(
            f"⚠️ btc_straddle-loop error (#{_err_state['count']} since start): {where}. "
            f"Бот может не торговать — проверь логи.", silent=False)
    except Exception:  # noqa: BLE001
        pass


def _live_preopen_block(now_ms: int) -> str | None:
    if cfg.killswitch_engaged():
        return "killswitch"
    if reconcile.is_blocked():
        return "unreconciled"
    day_start = live_safety.utc_day_start_ms(now_ms)
    realized_today = repo.realized_pnl_since(day_start)
    if live_safety.daily_loss_limit_hit(realized_today):
        return "daily_loss_limit"
    return None


def current_cycle_id(now_ms: int) -> int:
    return now_ms // CYCLE_MS


async def loop(run_once: bool = False) -> None:
    apply_schema()
    state = repo.ensure_state(START_EQUITY_USD)
    print(f"[btc_straddle] schema ready, start_equity=${state['start_equity_usd']}, "
          f"cycle={sl.CYCLE_H}h tp2={sl.TP2_PCT} sl_frac={sl.SL_DOLLAR_FRAC} "
          f"margin_pct={MARGIN_PCT_PER_CYCLE} poll={POLL_INTERVAL_S}s", flush=True)

    last_reconcile_ms = 0
    if broker.is_live():
        telegram_notify.notify_trader_start(
            mode=cfg.TRADING_MODE, armed=True, wallet_usdt=broker.wallet_equity_usdt())
        reconcile.reconcile_once(repo_module=repo, base_coin=BASE_COIN)
        last_reconcile_ms = int(time.time() * 1000)

    while True:
        try:
            spot = bybit_client.get_spot_price(SPOT_SYMBOL)
            if spot <= 0:
                print("[btc_straddle] WARN: spot price unavailable, skipping iteration", flush=True)
                if run_once:
                    return
                await asyncio.sleep(POLL_INTERVAL_S)
                continue

            state = repo.get_state() or state

            if broker.is_live():
                tick_ms = int(time.time() * 1000)
                if tick_ms - last_reconcile_ms >= cfg.RECONCILE_EVERY_MIN * 60_000:
                    reconcile.reconcile_once(repo_module=repo, base_coin=BASE_COIN)
                    last_reconcile_ms = tick_ms

            open_pos_now = repo.open_positions()
            # Always refresh the chain (not just when positions are open) — the
            # new-cycle open below needs it too. Fetched ONCE per tick and reused
            # by both the monitor pass and open_leg() below (no second chain fetch).
            chain: list[dict] = []
            chain_dict: dict[str, dict] | None = None
            try:
                chain = bybit_client.get_options_tickers(BASE_COIN)
                chain_dict = {
                    f"{o.get('side')}-{int(o.get('strike'))}-{o.get('expiry_ms')}": o
                    for o in chain
                    if o.get('side') and o.get('strike') and o.get('expiry_ms')
                }
            except Exception as e:  # noqa: BLE001
                print(f"[btc_straddle] WARN: chain fetch failed: {e!r}", flush=True)

            # 0) Mission Control emergency flatten — takes priority over the
            # normal SL/TP2/time-stop monitor pass below.
            if control_repo.is_close_all_requested(BOT_NAME):
                if broker.is_live() and reconcile.is_blocked():
                    # Proceed anyway — an operator hitting "close all" wants out
                    # NOW, and refusing entirely could leave real risk on while
                    # waiting for reconcile to clear. But DB-tracked open_pos_now
                    # may not match the exchange right now, so say so loudly:
                    # the operator should verify the result manually on Bybit.
                    telegram_notify.notify(
                        "⚠️ BTC straddle close-all: DB/exchange state is UNRECONCILED — "
                        "proceeding on DB-tracked positions anyway, but VERIFY MANUALLY on Bybit",
                        silent=False)
                n_target = len(open_pos_now)
                n_closed = force_close_all(open_pos_now, spot, chain_dict)
                print(f"[btc_straddle] Mission Control close-all: closed {n_closed}/{n_target}",
                      flush=True)
                if n_closed >= n_target:
                    control_repo.clear_close_all_requested(BOT_NAME)
                else:
                    # Leave the flag set so the next tick retries the stragglers
                    # (e.g. a live close that didn't confirm filled) instead of
                    # silently abandoning open risk under a "flatten everything" command.
                    _report_close_all_stuck(n_closed, n_target)
                open_pos_now = repo.open_positions()

            # 1) Monitor open legs (SL / TP2 / 24h time-stop)
            for p in open_pos_now:
                try:
                    check_and_close_position(p, spot, chain_dict)
                except Exception:  # noqa: BLE001
                    print(f"[btc_straddle] ERROR closing #{p.get('id')}:\n{traceback.format_exc()}",
                          flush=True)
                    _report_loop_error(f"close #{p.get('id')}")

            # 2) New cycle — open one call + one put if this cycle hasn't fired yet
            # (skipped while Mission Control has paused this bot).
            now_ms = int(time.time() * 1000)
            cyc = current_cycle_id(now_ms)
            if cyc > int(state.get("last_cycle_id") or 0):
                # Also check close_all_requested directly (not just paused): if an
                # operator resumes while stragglers from an emergency flatten are
                # still being retried (request_close_all sets paused=true, but
                # control_resume only clears paused, not close_all_requested),
                # a new cycle must still not open on top of an in-flight flatten.
                if control_repo.is_paused(BOT_NAME) or control_repo.is_close_all_requested(BOT_NAME):
                    print(f"[btc_straddle] paused/close-all via Mission Control — skip cycle {cyc} open",
                          flush=True)
                else:
                    blocked = _live_preopen_block(now_ms) if broker.is_live() else None
                    if blocked:
                        print(f"[btc_straddle] LIVE skip cycle {cyc} — {blocked}", flush=True)
                        telegram_notify.notify(f"⛔ BTC straddle live open blocked: {blocked}")
                    else:
                        eq = compute_equity(state, spot, chain_dict)
                        open_leg(cyc, "C", spot, eq["equity"], chain)
                        open_leg(cyc, "P", spot, eq["equity"], chain)
                repo.update_state(last_cycle_id=cyc)
                state["last_cycle_id"] = cyc

            # 3) Equity snapshot
            eq = compute_equity(state, spot, chain_dict)
            started_at = int(state.get("started_at_ms") or 0)
            peak = repo.peak_equity_since(started_at) or eq["equity"]
            peak_eff = max(peak, eq["equity"], float(state.get("start_equity_usd") or 0))
            max_dd_pct = ((peak_eff - eq["equity"]) / peak_eff * 100.0) if peak_eff > 0 else 0.0
            repo.insert_equity_snapshot(
                ts_ms=int(time.time() * 1000), equity_usd=eq["equity"],
                realized_usd=eq["realized"], unrealized_usd=eq["unrealized"],
                n_open=eq["n_open"], n_closed=eq["n_closed"], max_dd_pct=round(max_dd_pct, 4),
            )

        except Exception as e:  # noqa: BLE001
            print(f"[btc_straddle] error: {e!r}\n{traceback.format_exc()}", flush=True)
            _report_loop_error(repr(e))

        if run_once:
            return
        await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(loop(run_once="--once" in sys.argv))

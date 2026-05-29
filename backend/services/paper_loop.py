"""Live paper-trading background service.

Runs as a separate docker container. Two main responsibilities:

1. **Signal check** every 5 min (right after a 5m candle closes):
   Pull recent klines from DB, run the validated generator, check if the
   LAST bar emits a signal. If yes (and CB not active, and we have capacity),
   open a paper position using current Bybit option chain (or BS fallback).

2. **Position monitoring** every 30s:
   For each open position, fetch current option price, check TP1/TP2/SL/
   time-stop. Close (or half-close) when triggered. Update equity snapshot.

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
from services.paper_strategy import (  # noqa: E402
    DEFAULT_SIGMA,
    EXPIRY_TARGET_HOURS,
    LOT_MIN_ETH,
    START_EQUITY_USD,
    WINNER_EXIT,
    WINNER_GEN_KWARGS,
    apply_entry_spread,
    apply_exit_spread,
    fee_per_side,
    is_cb_active,
    margin_per_lot,
    realistic_size_lots,
    record_trade_result,
)
from services.strategy_registry import gen_sell_premium_iv_high  # noqa: E402


POLL_INTERVAL_S = int(os.getenv("PAPER_POLL_INTERVAL", "30"))
SIGNAL_CHECK_EVERY_MIN = 5
SPOT_SYMBOL = "ETHUSDT"
BASE_COIN = "ETH"
STRIKE_GRID = 25.0   # Bybit ETH options use $25/$50 grid; use $25 for safety


# ───────────────────── kline → generator input ─────────────────────

def load_klines_for_generator(window_5m: int = 600) -> tuple[list, list, list]:
    """Pull recent klines from DB for the generator. Returns (k5, k15, k1h)."""
    k5 = recent_klines(SPOT_SYMBOL, "5m", limit=window_5m)
    k15 = recent_klines(SPOT_SYMBOL, "15m", limit=window_5m // 3 + 20)
    k1h = recent_klines(SPOT_SYMBOL, "1h", limit=window_5m // 12 + 20)
    return k5, k15, k1h


# ───────────────────── option pricing (live + fallback) ─────────────

def pick_bybit_atm_call(chain: list[dict], spot: float, target_expiry_h: int) -> dict | None:
    """From live Bybit chain, pick the ATM call closest to target_expiry_h."""
    now_ms = int(time.time() * 1000)
    target_ms = now_ms + target_expiry_h * 3_600_000
    candidates = [
        o for o in chain
        if o.get("side") == "C"
        and o.get("expiry_ms", 0) > now_ms + 6 * 3_600_000  # at least 6h to expiry
        and (o.get("bid") or 0) > 0
    ]
    if not candidates:
        return None
    # Pick expiry closest to target
    candidates.sort(key=lambda o: abs((o.get("expiry_ms") or 0) - target_ms))
    by_expiry = candidates[0].get("expiry_ms")
    same_expiry = [o for o in candidates if o.get("expiry_ms") == by_expiry]
    # Now pick strike closest to spot (rounded to $25)
    target_strike = round(spot / STRIKE_GRID) * STRIKE_GRID
    same_expiry.sort(key=lambda o: abs((o.get("strike") or 0) - target_strike))
    return same_expiry[0] if same_expiry else None


def price_option_live(symbol: str, side: str) -> dict | None:
    """Fetch latest ticker for a specific option symbol. Returns dict with
    bid/ask/mark or None if unavailable."""
    chain = bybit_client.get_options_tickers(BASE_COIN)
    for o in chain:
        if o.get("symbol") == symbol:
            return o
    return None


def price_option_bs(side: str, spot: float, strike: float, expiry_ms: int,
                    sigma: float = DEFAULT_SIGMA) -> float:
    """Black-Scholes fallback pricing. Returns per-contract premium (USD)."""
    now_ms = int(time.time() * 1000)
    T_years = max(1 / 365 / 24, (expiry_ms - now_ms) / 1000 / 86400 / 365)
    return bs.price(side, spot, strike, T_years, sigma)


# ───────────────────── signal logic ─────────────────────────────────

def check_new_signal(k5, k15, k1h) -> dict | None:
    """Run the validated generator on recent klines. If the JUST-CLOSED bar
    (or the live edge, in case the in-progress bar isn't in DB yet) emitted a
    signal, return its dict.

    Why both last_idx and last_idx-1: when paper polls at a minute boundary
    (e.g. 01:30:00), the DB may have either:
      - k5[-1] = 01:30 bar (just opened, ~0s data) AND k5[-2] = 01:25 (closed)
      - OR k5[-1] = 01:25 bar (closed) with no 01:30 yet (poller hadn't run)
    Signal fires on the CLOSED 01:25 bar — which is at either idx -1 or -2
    depending on whether the live bar got upserted yet. Accept both to be safe.
    """
    if not k5:
        return None
    last_idx = len(k5) - 1
    sigs = gen_sell_premium_iv_high(k5, k15, k1h, **WINNER_GEN_KWARGS)
    # Accept signals at the just-closed bar OR the live edge
    latest = [s for s in sigs if s.get("idx_5m") in (last_idx - 1, last_idx)]
    return latest[-1] if latest else None  # newest if both fire


# ───────────────────── equity computation ──────────────────────────

def compute_equity(state: dict, spot: float, atm_chain_quotes: dict[str, dict] | None = None) -> dict:
    """Compute current equity = start + realized PnL + unrealized PnL on open positions."""
    start_equity = float(state["start_equity_usd"])
    stats = paper_repo.position_stats()
    realized = float(stats["realized_usd"])

    # Mark open positions to current option mark price (live if available; BS fallback)
    open_pos = paper_repo.open_positions()
    unrealized = 0.0
    for p in open_pos:
        # Build the symbol so we can look up the live ticker (best-effort)
        # We stored entry_credit_usd per contract. Need current mark per contract.
        # We pass the chain quotes via atm_chain_quotes if available; else fallback.
        live_quote = None
        if atm_chain_quotes:
            # Match by (strike, expiry_ms, side)
            key = f"{p['side']}-{int(p['strike'])}-{p['expiry_ms']}"
            live_quote = atm_chain_quotes.get(key)
        if live_quote and live_quote.get("mark_price", 0) > 0:
            current_mark = float(live_quote["mark_price"])
        else:
            current_mark = price_option_bs(p["side"], spot, float(p["strike"]),
                                           int(p["expiry_ms"]), DEFAULT_SIGMA)
        # short premium: profit = entry_credit - current_mark
        pnl_per_contract = float(p["entry_credit_usd"]) - current_mark
        unrealized += pnl_per_contract * float(p["contracts"])

    equity = start_equity + realized + unrealized
    return {
        "equity": equity,
        "realized": realized,
        "unrealized": unrealized,
        "n_open": stats["n_open"],
        "n_closed": stats["n_closed"],
    }


# ───────────────────── position open / close ───────────────────────

def open_paper_position(signal: dict, spot: float, equity_usd: float, state: dict) -> int | None:
    """Open a paper position for a signal — Bybit-realistic sizing/friction.

    Sizing: pick the largest whole number of 0.1-ETH lots whose Bybit Cross-
    Margin IM fits in MARGIN_PCT_PER_TRADE × equity. Skip the signal if even
    one lot doesn't fit.

    Entry friction: receive premium at bid = mid·(1 − half-spread), then
    deduct 0.03%·notional taker fee (capped at 12.5% of premium). The stored
    entry_credit_usd is per-contract NET of entry fee, so all downstream
    P&L math (close, equity) is correct without schema changes.
    """
    chain = bybit_client.get_options_tickers(BASE_COIN)
    pick = pick_bybit_atm_call(chain, spot, EXPIRY_TARGET_HOURS)

    if pick and pick.get("bid", 0) > 0 and pick.get("ask", 0) > 0:
        strike = float(pick["strike"])
        expiry_ms = int(pick["expiry_ms"])
        # Bybit's posted bid/ask already include market spread; use mid as
        # the reference and apply our model haircut on top to be conservative.
        premium_mid = (float(pick["bid"]) + float(pick["ask"])) / 2.0
        entry_source = "bybit"
        symbol = pick["symbol"]
    else:
        strike = round(spot / STRIKE_GRID) * STRIKE_GRID
        expiry_ms = int(time.time() * 1000) + EXPIRY_TARGET_HOURS * 3_600_000
        premium_mid = price_option_bs("C", spot, strike, expiry_ms, DEFAULT_SIGMA)
        if premium_mid <= 0:
            print(f"[paper] open skipped — could not price option", flush=True)
            return None
        entry_source = "bs_fallback"
        symbol = f"ETH-?-{int(strike)}-C"

    n_lots = realistic_size_lots(equity_usd, strike, premium_mid, state)
    if n_lots < 1:
        m_per_lot = margin_per_lot(strike, premium_mid)
        print(f"[paper] open skipped — insufficient margin "
              f"(need ${m_per_lot:.2f}/lot, have ${equity_usd:.2f} equity)", flush=True)
        return None

    contracts = n_lots * LOT_MIN_ETH
    notional = strike * contracts

    # Entry-side friction
    entry_credit_per_contract_gross = apply_entry_spread(premium_mid)  # we sell at bid
    premium_received_total = entry_credit_per_contract_gross * contracts
    entry_fee = fee_per_side(notional, premium_received_total)
    entry_credit_per_contract_net = entry_credit_per_contract_gross - (entry_fee / max(contracts, 1e-9))

    # size_usd now means MARGIN locked, not premium budget
    margin_locked = margin_per_lot(strike, premium_mid) * n_lots
    entry_credit_pct = entry_credit_per_contract_net / spot * 100

    pid = paper_repo.open_position(
        opened_at_ms=int(time.time() * 1000),
        underlying_at_open=spot,
        side="C",
        strike=strike,
        expiry_ms=expiry_ms,
        contracts=contracts,
        size_usd=margin_locked,
        entry_credit_usd=entry_credit_per_contract_net,
        entry_credit_pct=entry_credit_pct,
        entry_source=entry_source,
        tp1_pct=WINNER_EXIT["tp1_pct"],
        tp2_pct=WINNER_EXIT["tp2_pct"],
        sl_pct=WINNER_EXIT["sl_pct"],
        hold_h=WINNER_EXIT["hold_h"],
        signal_payload={
            "symbol": symbol, "signal": signal,
            "n_lots": n_lots, "margin_locked": round(margin_locked, 2),
            "premium_mid": round(premium_mid, 4),
            "entry_fee_usd": round(entry_fee, 4),
        },
    )
    print(f"[paper] OPENED #{pid}: SELL {symbol} "
          f"lots={n_lots} contracts={contracts:.2f}ETH  "
          f"credit_net=${entry_credit_per_contract_net:.2f}/ETH "
          f"margin=${margin_locked:.2f}  fee=${entry_fee:.3f}  source={entry_source}",
          flush=True)
    return pid


def check_and_close_position(p: dict, spot: float) -> bool:
    """Check exit conditions on one position. Returns True if state changed.

    TP/SL compare against the BS-mid (clean signal), but the actual exit
    debit applies the ask-side spread + exit fee — so realized P&L
    includes round-trip friction (entry already accounts for its half).
    """
    now_ms = int(time.time() * 1000)
    age_h = (now_ms - int(p["opened_at_ms"])) / 3_600_000

    premium_mid = price_option_bs(p["side"], spot, float(p["strike"]),
                                  int(p["expiry_ms"]), DEFAULT_SIGMA)
    entry_credit = float(p["entry_credit_usd"])  # NET per contract, post entry-side friction
    tp1_threshold = entry_credit * (1 - float(p["tp1_pct"]))
    tp2_threshold = entry_credit * (1 - float(p["tp2_pct"]))
    sl_threshold = entry_credit * (1 + float(p["sl_pct"]))

    reason = None
    if premium_mid >= sl_threshold:
        reason = "sl"
    elif premium_mid <= tp2_threshold:
        reason = "tp2"
    elif age_h >= float(p["hold_h"]):
        reason = "time_stop"
    elif p["status"] == "open" and premium_mid <= tp1_threshold:
        paper_repo.mark_half_closed(int(p["id"]), now_ms)
        print(f"[paper] #{p['id']} TP1 marked @ mid ${premium_mid:.2f} (entry ${entry_credit:.2f})",
              flush=True)
        return True

    if reason is None:
        return False

    contracts = float(p["contracts"])
    notional = float(p["strike"]) * contracts

    # Exit-side friction: we buy back at ask = mid·(1 + half-spread), then
    # pay 0.03%·notional taker fee (capped at 12.5% of premium handled).
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
    record_trade_result(pnl_pct)
    print(f"[paper] CLOSED #{p['id']} reason={reason} "
          f"mid=${premium_mid:.2f} debit_net=${exit_debit_net:.2f} fee=${exit_fee:.3f}  "
          f"pnl={pnl_pct:+.2f}% (${pnl_usd:+.2f})", flush=True)
    return True


# ───────────────────── main loop ───────────────────────────────────

def is_signal_check_time(last_check_ms: int) -> bool:
    """Trigger every 5 min, around the top of a 5m candle close."""
    now = datetime.now(timezone.utc)
    if (now.minute % SIGNAL_CHECK_EVERY_MIN) != 0:
        return False
    # Avoid double-check inside the same minute
    return (int(time.time() * 1000) - last_check_ms) >= 4 * 60 * 1000


async def loop():
    apply_schema()
    state = paper_repo.ensure_state(START_EQUITY_USD)
    print(f"[paper] schema ready, start_equity=${state['start_equity_usd']}, "
          f"poll={POLL_INTERVAL_S}s", flush=True)

    last_signal_check_ms = 0

    while True:
        try:
            spot = bybit_client.get_spot_price(SPOT_SYMBOL)
            if spot <= 0:
                print("[paper] WARN: spot price unavailable, skipping iteration", flush=True)
                await asyncio.sleep(POLL_INTERVAL_S)
                continue

            state = paper_repo.get_state() or state

            # 1) Position monitoring — every iteration
            for p in paper_repo.open_positions():
                check_and_close_position(p, spot)

            # 2) Signal check — every 5 min
            if is_signal_check_time(last_signal_check_ms):
                last_signal_check_ms = int(time.time() * 1000)
                if is_cb_active(state, last_signal_check_ms):
                    cb_remaining_h = (int(state["cb_cooldown_until_ms"]) -
                                       last_signal_check_ms) / 3_600_000
                    print(f"[paper] CB cooldown active ({cb_remaining_h:.1f}h left), no signals",
                          flush=True)
                else:
                    k5, k15, k1h = load_klines_for_generator()
                    if len(k5) < 300:
                        print(f"[paper] not enough klines yet ({len(k5)} 5m), skip signal check",
                              flush=True)
                    else:
                        sig = check_new_signal(k5, k15, k1h)
                        if sig:
                            eq = compute_equity(state, spot)
                            open_paper_position(sig, spot, eq["equity"], state)
                        else:
                            print(f"[paper] tick: no signal (spot=${spot:.2f}, open={len(paper_repo.open_positions())})",
                                  flush=True)

            # 3) Equity snapshot — every iteration
            eq = compute_equity(state, spot)
            paper_repo.insert_equity_snapshot(
                ts_ms=int(time.time() * 1000),
                equity_usd=eq["equity"],
                realized_usd=eq["realized"],
                unrealized_usd=eq["unrealized"],
                n_open=eq["n_open"],
                n_closed=eq["n_closed"],
                max_dd_pct=None,  # computed on read
            )

        except Exception as e:  # noqa: BLE001
            print(f"[paper] error: {e!r}", flush=True)

        await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(loop())

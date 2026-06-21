"""Position reconciliation (P5) — exchange is the source of truth.

On startup and every ``RECONCILE_EVERY_MIN`` minutes (live mode only), compare the
real Bybit option positions against the trader DB's open positions and heal drift:

  - DB-open but FLAT on the exchange  → it was closed outside the bot (the user's
    manual close, an expiry, a liquidation). Heal: mark it closed in the DB with
    reason 'reconciled'. Alert.
  - On the exchange but NOT in the DB  → an untracked position the bot can't manage.
    Don't auto-adopt (unsafe). Alert and BLOCK new opens until it's resolved.

While unresolved (untracked position, or the exchange read failed), ``is_blocked()``
is True and the loop must not open new positions — trading blind to real exposure
is worse than missing a signal.

Paper mode never calls this (there are no exchange positions, so everything would
look 'externally closed'). The loop gates every call on ``broker.is_live()``.
"""
from __future__ import annotations

import time
from typing import Any, NamedTuple

from services import broker, telegram_notify
# NOTE: `db.paper_repo` (psycopg2) is imported lazily inside reconcile_once so the
# pure helpers (diff_positions / exchange_position_sizes) stay importable without a
# DB driver — mirrors live_sizing / strategy_config being dependency-light.


class ReconcileResult(NamedTuple):
    ok: bool                    # True when DB and exchange agree (after healing)
    healed_closed: list[int]    # position ids closed to match the (flat) exchange
    untracked: list[str]        # exchange symbols with size not present in the DB
    detail: str


# Block new opens until a clean reconcile clears this. Starts blocked-safe? No —
# the loop reconciles at startup before opening, so default False is fine; a failed
# or dirty reconcile sets it True.
_blocked = False


def is_blocked() -> bool:
    return _blocked


def _position_symbol(p: dict) -> str | None:
    payload = p.get("signal_payload") or {}
    sym = payload.get("symbol") if isinstance(payload, dict) else None
    return sym or None


def exchange_position_sizes(client: Any, base_coin: str = "ETH") -> dict[str, float] | None:
    """Map of {symbol: abs(size)} for the bot's option positions, or None on read
    failure (caller should then block, not assume flat)."""
    rows = client.positions(base_coin)
    if rows is None:
        return None
    out: dict[str, float] = {}
    for r in rows:
        sym = r.get("symbol")
        try:
            size = abs(float(r.get("size") or 0.0))
        except (TypeError, ValueError):
            size = 0.0
        if sym and size > 0:
            out[sym] = out.get(sym, 0.0) + size
    return out


def diff_positions(db_open: list[dict], exch_sizes: dict[str, float]) -> tuple[list[dict], list[str]]:
    """Pure diff. Returns (closed_externally, untracked_symbols).

      closed_externally — DB-open positions flat (≤0 size) on the exchange.
      untracked         — exchange symbols with size>0 not tracked in the DB.
    """
    db_by_sym: dict[str, dict] = {}
    for p in db_open:
        sym = _position_symbol(p)
        if sym:
            db_by_sym[sym] = p
    closed_externally = [p for sym, p in db_by_sym.items() if exch_sizes.get(sym, 0.0) <= 0.0]
    untracked = [sym for sym, sz in exch_sizes.items() if sz > 0 and sym not in db_by_sym]
    return closed_externally, untracked


def reconcile_once(client: Any | None = None, *, repo_module: Any | None = None,
                   base_coin: str = "ETH") -> ReconcileResult:
    """Compare exchange vs DB, heal externally-closed positions, set the block flag.
    Exchange wins. Returns a ReconcileResult.

    ``repo_module`` defaults to ``db.paper_repo`` (the ETH path); pass
    ``db.btc_straddle_repo`` + ``base_coin="BTC"`` for the BTC straddle loop. The
    injected module must expose the same ``open_positions``/``close_position``
    surface as ``paper_repo``.
    """
    global _blocked
    if repo_module is None:
        from db import paper_repo as repo_module  # lazy: importable without psycopg2
    if client is None:
        client = broker._get_client()

    exch = exchange_position_sizes(client, base_coin)
    if exch is None:
        _blocked = True
        msg = "reconcile: could not read exchange positions — BLOCKING opens"
        print(f"[reconcile] {msg}", flush=True)
        telegram_notify.notify_reconcile_mismatch(detail=msg)
        return ReconcileResult(False, [], [], msg)

    db_open = repo_module.open_positions()
    closed_externally, untracked = diff_positions(db_open, exch)

    healed: list[int] = []
    now_ms = int(time.time() * 1000)
    for p in closed_externally:
        pid = int(p["id"])
        # PnL is unknown from here (real result is in the wallet); record 0 and let
        # the wallet/equity be authoritative. The alert tells the user to verify.
        repo_module.close_position(
            pid, closed_at_ms=now_ms, exit_debit_usd=0.0,
            pnl_pct=0.0, pnl_usd=0.0, exit_reason="reconciled")
        healed.append(pid)
        print(f"[reconcile] healed #{pid} — closed on exchange, marked reconciled", flush=True)

    _blocked = bool(untracked)
    if untracked:
        msg = f"untracked exchange position(s): {', '.join(untracked)} — BLOCKING opens"
        print(f"[reconcile] {msg}", flush=True)
        telegram_notify.notify_reconcile_mismatch(detail=msg)
        return ReconcileResult(False, healed, untracked, msg)

    if healed:
        telegram_notify.notify_reconcile_mismatch(
            detail=f"healed {len(healed)} position(s) closed outside the bot: {healed}")

    return ReconcileResult(True, healed, [], f"ok (healed={len(healed)})")

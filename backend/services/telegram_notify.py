"""Fire-and-forget Telegram notifications for paper-loop events.

Stateless, no aiogram dependency, no separate process. If TELEGRAM_BOT_TOKEN
or TELEGRAM_CHAT_ID env vars are missing, every notify call is a no-op so the
paper-loop never breaks because of telemetry config.
"""
from __future__ import annotations

import os
from typing import Final

import requests

_TOKEN: Final = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
_CHAT_ID: Final = os.getenv("TELEGRAM_CHAT_ID", "").strip()
_TIMEOUT_S: Final = 5


def is_enabled() -> bool:
    return bool(_TOKEN and _CHAT_ID)


def notify(text: str, *, parse_mode: str = "HTML", silent: bool = False) -> None:
    """Send a message. Never raises — paper-loop should never break on this."""
    if not is_enabled():
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={
                "chat_id": _CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": silent,
            },
            timeout=_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] notify failed: {e!r}", flush=True)


def notify_open(*, pid: int, symbol: str, side: str, strike: float, spot: float,
                n_lots: int, contracts: float, premium_recv: float,
                margin_locked: float, entry_fee: float, source: str) -> None:
    side_word = "CALL" if side == "C" else "PUT"
    emoji = "🟢"
    text = (
        f"{emoji} <b>OPENED #{pid}</b> · SELL {side_word}\n"
        f"  Strike: ${strike:.0f} · ETH at entry: ${spot:.2f}\n"
        f"  Size: <b>{n_lots} lots</b> ({contracts:.2f} ETH)\n"
        f"  Premium received: <b>${premium_recv:.2f}</b>\n"
        f"  Margin locked: ${margin_locked:.2f} · fee ${entry_fee:.2f}\n"
        f"  Symbol: <code>{symbol}</code> · source: {source}"
    )
    notify(text)


def notify_close(*, pid: int, side: str, strike: float, reason: str,
                 pnl_pct: float, pnl_usd: float, equity_after: float,
                 hold_h: int = 0) -> None:
    side_word = "CALL" if side == "C" else "PUT"
    profit = pnl_usd > 0
    emoji = "✅" if profit else "❌"
    if reason == "time_stop" and hold_h > 0:
        reason_label = f"time-stop {hold_h}h"
    else:
        reason_label = {
            "tp1": "TP1 (50% closed)",
            "tp2": "TP2 (full close)",
            "sl": "STOP-LOSS",
        }.get(reason, reason.upper())
    sign = "+" if profit else ""
    text = (
        f"{emoji} <b>CLOSED #{pid}</b> · SELL {side_word} @ ${strike:.0f}\n"
        f"  Reason: {reason_label}\n"
        f"  P&amp;L: <b>{sign}${pnl_usd:.2f}</b> ({sign}{pnl_pct:.1f}% of premium)\n"
        f"  Equity now: <b>${equity_after:.2f}</b>"
    )
    notify(text)


def notify_skipped_margin(*, spot: float, strike: float, need_usd: float,
                          have_usd: float) -> None:
    """Signal fired but margin didn't fit — useful to know we're capital-bound."""
    text = (
        f"⚠️ <b>Signal skipped</b> — insufficient margin\n"
        f"  ETH ${spot:.2f} · strike ${strike:.0f}\n"
        f"  Need ${need_usd:.2f}/lot · equity ${have_usd:.2f}"
    )
    notify(text, silent=True)


def notify_cb_triggered(*, equity_after: float) -> None:
    from services.strategy_config import CB_CONSEC_LIMIT, CB_PAUSE_HOURS
    text = (
        f"⏸ <b>Circuit-breaker activated</b>\n"
        f"  {CB_CONSEC_LIMIT} losing trades in a row · pause {CB_PAUSE_HOURS}h\n"
        f"  Equity: ${equity_after:.2f}"
    )
    notify(text)

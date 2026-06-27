"""Fire-and-forget Telegram notifications for paper-loop events.

Stateless, no aiogram dependency, no separate process. If TELEGRAM_BOT_TOKEN
or TELEGRAM_CHAT_ID env vars are missing, every notify call is a no-op so the
paper-loop never breaks because of telemetry config.

All four bots (Sniper1/Boba1/Grogu1, plus Tyagach in its own separate repo)
post to the SAME chat, so every message is auto-tagged with the bot's name
(2026-06-27) — read once from execution_config.ACCOUNT_NAME, which is already
set per docker-compose service (MC_ACCOUNT_NAME) and used throughout the rest
of the platform (Mission Control, accounts_repo) — no new config needed.
"""
from __future__ import annotations

import os
from typing import Final

import requests

from services import execution_config as cfg

_TOKEN: Final = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
# TELEGRAM_CHAT_ID supports multiple recipients: comma/space-separated chat IDs.
_CHAT_IDS: Final = [c for c in os.getenv("TELEGRAM_CHAT_ID", "").replace(",", " ").split() if c]
_TIMEOUT_S: Final = 5

# "default" is execution_config's fallback for ad-hoc scripts/tests that never
# set MC_ACCOUNT_NAME — skip tagging in that case so we don't spam "[default]"
# on things that aren't one of the four real bots.
BOT_LABEL: Final = cfg.ACCOUNT_NAME if cfg.ACCOUNT_NAME != "default" else None


def is_enabled() -> bool:
    return bool(_TOKEN and _CHAT_IDS)


def notify(text: str, *, parse_mode: str = "HTML", silent: bool = False) -> None:
    """Send a message to every configured chat. Never raises — paper-loop should never break on this."""
    if not is_enabled():
        return
    if BOT_LABEL:
        text = f"<b>[{BOT_LABEL}]</b> {text}"
    for chat_id in _CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_notification": silent,
                },
                timeout=_TIMEOUT_S,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[telegram] notify failed for {chat_id}: {e!r}", flush=True)


def notify_open(*, pid: int, symbol: str, side: str, strike: float, spot: float,
                n_lots: int, contracts: float, premium_recv: float,
                margin_locked: float, entry_fee: float, source: str,
                equity_now: float, asset: str = "ETH") -> None:
    side_word = "CALL" if side == "C" else "PUT"
    emoji = "🟢"
    text = (
        f"{emoji} <b>OPENED #{pid}</b> · SELL {side_word}\n"
        f"  Strike: ${strike:.0f} · {asset} at entry: ${spot:.2f}\n"
        f"  Size: <b>{n_lots} lots</b> ({contracts:.4f} {asset})\n"
        f"  Premium received: <b>${premium_recv:.2f}</b>\n"
        f"  Margin locked: ${margin_locked:.2f} · fee ${entry_fee:.2f}\n"
        f"  Balance now: <b>${equity_now:.2f}</b>\n"
        f"  Symbol: <code>{symbol}</code> · source: {source}"
    )
    notify(text)


def notify_close(*, pid: int, side: str, strike: float, reason: str,
                 pnl_pct: float, pnl_usd: float, equity_after: float,
                 total_pnl_usd: float, hold_h: int = 0) -> None:
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
            "cluster_stop_worst_leg": "cluster-stop (worst leg)",
        }.get(reason, reason.upper())
    sign = "+" if profit else "-"
    pct_sign = "+" if pnl_pct >= 0 else "-"
    total_sign = "+" if total_pnl_usd >= 0 else "-"
    text = (
        f"{emoji} <b>CLOSED #{pid}</b> · SELL {side_word} @ ${strike:.0f}\n"
        f"  Reason: {reason_label}\n"
        f"  This trade: <b>{sign}${abs(pnl_usd):.2f}</b> ({pct_sign}{abs(pnl_pct):.1f}% of premium)\n"
        f"  Balance now: <b>${equity_after:.2f}</b>\n"
        f"  Total P&amp;L since start: <b>{total_sign}${abs(total_pnl_usd):.2f}</b>"
    )
    notify(text)


def notify_skipped_margin(*, spot: float, strike: float, need_usd: float,
                          have_usd: float, asset: str = "ETH") -> None:
    """Signal fired but margin didn't fit — useful to know we're capital-bound."""
    text = (
        f"⚠️ <b>Signal skipped</b> — insufficient margin\n"
        f"  {asset} ${spot:.2f} · strike ${strike:.0f}\n"
        f"  Need ${need_usd:.2f}/lot · equity ${have_usd:.2f}"
    )
    notify(text, silent=True)


def notify_fill(*, action: str, symbol: str, qty: float, avg_price: float,
                fees: float, status: str, ref_mid: float) -> None:
    """Real order filled (testnet/live)."""
    slip = ((avg_price - ref_mid) / ref_mid * 100) if ref_mid else 0.0
    text = (
        f"📡 <b>FILL</b> · {action} <code>{symbol}</code>\n"
        f"  qty {qty:.2f} ETH @ <b>${avg_price:.2f}</b> ({status})\n"
        f"  ref mid ${ref_mid:.2f} · slip {slip:+.1f}% · fee ${fees:.3f}"
    )
    notify(text)


def notify_order_error(*, action: str, symbol: str, detail: str) -> None:
    text = (
        f"🛑 <b>ORDER ERROR</b> · {action} <code>{symbol}</code>\n"
        f"  {detail}\n  (signal skipped — no position assumed)"
    )
    notify(text)


def notify_reconcile_mismatch(*, detail: str) -> None:
    text = f"🔄 <b>RECONCILE</b>\n  {detail}"
    notify(text)


def notify_killswitch(*, reason: str) -> None:
    text = f"🚫 <b>KILL-SWITCH</b> — trading halted\n  {reason}"
    notify(text)


def notify_cap_breach(*, cap: str, detail: str) -> None:
    text = f"⛔️ <b>CAP</b> {cap} — open blocked\n  {detail}"
    notify(text, silent=True)


def notify_slippage(*, symbol: str, expected: float, got: float, pct: float) -> None:
    text = (
        f"⚠️ <b>SLIPPAGE</b> <code>{symbol}</code>\n"
        f"  expected ${expected:.2f} · got ${got:.2f} ({pct:+.1f}%)"
    )
    notify(text)


def notify_trader_start(*, mode: str, armed: bool, wallet_usdt: float | None) -> None:
    text = (
        f"🤖 <b>TRADER START</b> · mode=<b>{mode}</b> armed={armed}\n"
        f"  USDT wallet: {wallet_usdt if wallet_usdt is not None else '?'}"
    )
    notify(text)


def notify_cb_triggered(*, equity_after: float) -> None:
    from services.strategy_config import CB_CONSEC_LIMIT, CB_PAUSE_HOURS
    text = (
        f"⏸ <b>Circuit-breaker activated</b>\n"
        f"  {CB_CONSEC_LIMIT} losing trades in a row · pause {CB_PAUSE_HOURS}h\n"
        f"  Equity: ${equity_after:.2f}"
    )
    notify(text)

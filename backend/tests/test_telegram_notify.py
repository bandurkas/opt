"""Unit tests for telegram_notify.py — 2026-06-27 bot-label tagging +
balance/total-P&L enrichment of notify_open/notify_close.

Monkeypatches requests.post (no real network call) and is_enabled() (so
tests run without TELEGRAM_BOT_TOKEN/CHAT_ID configured), and overrides
BOT_LABEL directly to test the tagging behavior deterministically regardless
of which MC_ACCOUNT_NAME (if any) happens to be set in the test environment.

Run: cd backend && PYTHONPATH=. python3 -m pytest tests/test_telegram_notify.py -v
Or standalone: cd backend && PYTHONPATH=. python3 tests/test_telegram_notify.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import telegram_notify as tn


def _capture():
    """Patch is_enabled+requests.post so notify() runs its full text-building
    logic and we can inspect exactly what would have been sent."""
    sent = []
    orig_enabled = tn.is_enabled
    orig_post = tn.requests.post
    tn.is_enabled = lambda: True
    tn._CHAT_IDS[:] = ["fake_chat"]

    class _Resp:
        status_code = 200

    def fake_post(url, json, timeout):
        sent.append(json["text"])
        return _Resp()

    tn.requests.post = fake_post
    return sent, orig_enabled, orig_post


def _restore(orig_enabled, orig_post):
    tn.is_enabled = orig_enabled
    tn.requests.post = orig_post


def test_bot_label_prefixes_every_message():
    sent, oe, op = _capture()
    orig_label = tn.BOT_LABEL
    tn.BOT_LABEL = "Sniper1"
    try:
        tn.notify("hello")
    finally:
        tn.BOT_LABEL = orig_label
        _restore(oe, op)
    assert sent == ["<b>[Sniper1]</b> hello"]


def test_no_label_means_no_prefix():
    sent, oe, op = _capture()
    orig_label = tn.BOT_LABEL
    tn.BOT_LABEL = None
    try:
        tn.notify("hello")
    finally:
        tn.BOT_LABEL = orig_label
        _restore(oe, op)
    assert sent == ["hello"]


def test_notify_open_includes_balance():
    sent, oe, op = _capture()
    orig_label = tn.BOT_LABEL
    tn.BOT_LABEL = "Grogu1"
    try:
        tn.notify_open(pid=42, symbol="ETH-1JAN26-1500-C-USDT", side="C",
                       strike=1500.0, spot=1495.0, n_lots=2, contracts=0.2,
                       premium_recv=12.34, margin_locked=80.0, entry_fee=0.5,
                       source="bybit", equity_now=812.34, asset="ETH")
    finally:
        tn.BOT_LABEL = orig_label
        _restore(oe, op)
    text = sent[0]
    assert "[Grogu1]" in text
    assert "OPENED #42" in text
    assert "Balance now: <b>$812.34</b>" in text
    assert "Premium received: <b>$12.34</b>" in text


def test_notify_close_includes_balance_and_total_pnl():
    sent, oe, op = _capture()
    orig_label = tn.BOT_LABEL
    tn.BOT_LABEL = "Boba1"
    try:
        tn.notify_close(pid=7, side="P", strike=1500.0, reason="tp2",
                        pnl_pct=77.4, pnl_usd=5.51, equity_after=812.34,
                        total_pnl_usd=12.34, hold_h=96)
    finally:
        tn.BOT_LABEL = orig_label
        _restore(oe, op)
    text = sent[0]
    assert "[Boba1]" in text
    assert "This trade: <b>+$5.51</b>" in text
    assert "Balance now: <b>$812.34</b>" in text
    assert "Total P&amp;L since start: <b>+$12.34</b>" in text


def test_notify_close_negative_total_pnl_shows_minus_sign():
    sent, oe, op = _capture()
    orig_label = tn.BOT_LABEL
    tn.BOT_LABEL = "Sniper1"
    try:
        tn.notify_close(pid=8, side="C", strike=1525.0, reason="sl",
                        pnl_pct=-62.3, pnl_usd=-11.73, equity_after=780.0,
                        total_pnl_usd=-20.0, hold_h=24)
    finally:
        tn.BOT_LABEL = orig_label
        _restore(oe, op)
    text = sent[0]
    assert "This trade: <b>-$11.73</b>" in text
    assert "Total P&amp;L since start: <b>-$20.00</b>" in text


def test_notify_close_cluster_stop_reason_label():
    sent, oe, op = _capture()
    orig_label = tn.BOT_LABEL
    tn.BOT_LABEL = None
    try:
        tn.notify_close(pid=9, side="C", strike=1525.0, reason="cluster_stop_worst_leg",
                        pnl_pct=-50.0, pnl_usd=-9.14, equity_after=790.86,
                        total_pnl_usd=-9.14, hold_h=24)
    finally:
        tn.BOT_LABEL = orig_label
        _restore(oe, op)
    assert "Reason: cluster-stop (worst leg)" in sent[0]


def test_disabled_sends_nothing():
    sent = []
    orig_enabled = tn.is_enabled
    orig_post = tn.requests.post
    tn.is_enabled = lambda: False
    tn.requests.post = lambda *a, **k: sent.append(1)
    try:
        tn.notify("should not send")
    finally:
        tn.is_enabled = orig_enabled
        tn.requests.post = orig_post
    assert sent == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"OK: {t.__name__}")
    print(f"\n{len(tests)} tests passed")

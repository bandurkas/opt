"""Unit tests for window_fail_step — the FLICKER_TOLERANCE persistence rule
(2026-06-25, sniper_persistence_backtest.py).

Sniper1's per-minute debounce used to require ALL 5 one-minute checks in a
5m window to pass ("ready") before the close-tick fire attempt. Backtest
found a single flickering minute (almost always the MTF-alignment gate)
killed otherwise-good windows: tolerating exactly 1 flicker
(FLICKER_TOLERANCE=1) lifted trade count +5.4% with equal/better avg PnL,
and the rescued trades were themselves profitable (not noise).

This is safe regardless of which minute flickers or what side it showed,
because the close-tick fire still re-validates through check_new_signal's
real generator gate — window_fail_step only gates whether that check is
even attempted.

Run:  cd backend && PYTHONPATH=. python3 tests/test_persistence_tolerance.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.paper_loop import window_fail_step, FLICKER_TOLERANCE


def test_all_five_ready_never_disqualifies():
    fail_count = 0
    for _ in range(5):
        fail_count, disq = window_fail_step(fail_count, True)
    assert fail_count == 0
    assert disq is False
    print("✓ 5/5 ready -> never disqualified")


def test_single_flicker_is_tolerated():
    # ready, ready, NOT ready, ready, ready — exactly 1 flicker.
    sequence = [True, True, False, True, True]
    fail_count = 0
    disq = False
    for r in sequence:
        fail_count, disq = window_fail_step(fail_count, r)
    assert fail_count == 1
    assert disq is False, "exactly 1 flicker must NOT disqualify under tolerance=1"
    print("✓ 1/5 flicker -> tolerated, window survives to the close-tick check")


def test_two_flickers_disqualifies():
    sequence = [True, False, True, False, True]
    fail_count = 0
    disq = False
    for r in sequence:
        fail_count, disq = window_fail_step(fail_count, r)
    assert fail_count == 2
    assert disq is True, "2 flickers must disqualify under tolerance=1"
    print("✓ 2/5 flickers -> disqualified")


def test_disqualification_is_sticky_within_window():
    # Even if later minutes recover to ready=True, a window that already
    # crossed the tolerance threshold must stay disqualified (matches the
    # live loop never resetting window_fail_count until the NEXT window).
    sequence = [False, False, True, True, True]
    fail_count = 0
    disq_seen = []
    for r in sequence:
        fail_count, disq = window_fail_step(fail_count, r)
        disq_seen.append(disq)
    assert disq_seen == [False, True, True, True, True], disq_seen
    print("✓ disqualification is sticky once tolerance is exceeded mid-window")


def test_tolerance_is_one_by_default():
    assert FLICKER_TOLERANCE == 1, (
        "default tolerance changed — update this test and the backtest "
        "comment in paper_loop.py if intentional")
    print(f"✓ FLICKER_TOLERANCE default is {FLICKER_TOLERANCE}")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} persistence-tolerance tests passed ✓")


if __name__ == "__main__":
    main()

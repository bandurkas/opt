"""Unit tests for is_new_signal — the Sniper1 double-fire fix (found 2026-06-23,
re-keyed from idx_5m to ts_ms 2026-06-25).

check_new_signal() re-walks the FULL k5 history every tick with no memory of
its own, and accepts a 2-bar-wide window (idx_5m in {last-1, last}) to tolerate
candle-close timing jitter. Live evidence (signal_audit + paper_positions on
2026-06-23): the SAME cooldown-spaced occurrence got rediscovered as "new" on
the tick 5 min after it first fired, opening a near-duplicate position —
positions #1/#2 (08:59/09:04) and #3/#4 (09:29/09:34), each pair exactly
30 min (= cooldown_bars) after the previous pair. is_new_signal() persists the
ts_ms of the last occurrence actually acted on so the second tick of a pair
can tell "I already saw this one." Re-keyed from idx_5m to ts_ms because
idx_5m is a position within check_new_signal's sliding 2100-bar window — the
SAME calendar bar gets a DIFFERENT idx_5m every tick, so comparing positions
across ticks (rather than within one tick's 2-bar window) is meaningless.

Run:  cd backend && PYTHONPATH=. python3 tests/test_signal_dedup.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.paper_strategy import is_new_signal

BAR_MS = 300_000  # 5m bar, matches paper_loop.BAR_MS_5M


def test_first_signal_ever_is_new():
    assert is_new_signal(1_700_000_000_000, None) is True
    print("✓ no prior signal -> always new")


def test_same_ts_is_not_new():
    # This is the exact bug: tick T accepts a signal at ts=T0 via the 2-bar
    # window, tick T+1 re-discovers the same calendar bar's signal -> must
    # now be rejected.
    ts = 1_700_000_000_000
    assert is_new_signal(ts, ts) is False
    print("✓ same ts_ms as last-acted-on -> rejected (the bug, fixed)")


def test_earlier_ts_is_not_new():
    assert is_new_signal(1_700_000_000_000 - BAR_MS, 1_700_000_000_000) is False
    print("✓ ts_ms older than last-acted-on -> rejected")


def test_strictly_later_ts_is_new():
    # The next real occurrence after a fresh cooldown (e.g. +6 bars later).
    base = 1_700_000_000_000
    assert is_new_signal(base + 6 * BAR_MS, base) is True
    print("✓ ts_ms strictly newer than last-acted-on -> accepted")


# ───────────────────── reproduces the live double-fire sequence ─────────────────────

class FakeRepo:
    """Stand-in for paper_repo.update_state, mirroring test_cb_race.py's FakeRepo
    pattern: just enough to track last_signal_ts_ms across simulated ticks."""

    def __init__(self):
        self.row = {"last_signal_ts_ms": None}

    def update_state(self, *, last_signal_ts_ms=None, **_ignored):
        if last_signal_ts_ms is not None:
            self.row["last_signal_ts_ms"] = last_signal_ts_ms


def _tick(repo: FakeRepo, ts_ms: int) -> bool:
    """Mirrors the paper_loop callsite: accept iff is_new_signal, then persist."""
    accepted = is_new_signal(ts_ms, repo.row["last_signal_ts_ms"])
    if accepted:
        repo.update_state(last_signal_ts_ms=ts_ms)
    return accepted


def test_live_double_fire_sequence_now_deduped():
    """Replays the exact live pattern (08:59, 09:04, 09:29, 09:34 — bars
    5 min apart within a pair, pairs 30 min/6 bars apart) and asserts the
    second tick of each pair is now rejected instead of opening a duplicate."""
    repo = FakeRepo()
    base = 1_700_000_000_000  # ts_ms at 08:59 (first real occurrence)

    # 08:59 — genuinely new occurrence.
    assert _tick(repo, base) is True
    # 09:04 (one bar later) — old code treated this as new too (2-bar window
    # rediscovering the same occurrence); the generator's internal cooldown
    # walk still reports the SAME occurrence's ts (base), since 6 bars
    # haven't passed.
    assert _tick(repo, base) is False, "must reject the duplicate at +5min"

    # 30 min later (6 bars) — a genuinely new cooldown-spaced occurrence.
    next_occurrence = base + 6 * BAR_MS
    assert _tick(repo, next_occurrence) is True
    # And its own 5-min-later echo must also be rejected.
    assert _tick(repo, next_occurrence) is False, "must reject the duplicate at +5min"

    print("✓ live double-fire sequence (08:59/09:04, 09:29/09:34) -> "
          "second tick of each pair now rejected, only 2 real opens instead of 4")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} signal-dedup tests passed ✓")


if __name__ == "__main__":
    main()

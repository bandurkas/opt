"""Unit tests for is_new_signal — the Sniper1 double-fire fix (found 2026-06-23).

check_new_signal() re-walks the FULL k5 history every tick with no memory of
its own, and accepts a 2-bar-wide window (idx_5m in {last-1, last}) to tolerate
candle-close timing jitter. Live evidence (signal_audit + paper_positions on
2026-06-23): the SAME cooldown-spaced occurrence got rediscovered as "new" on
the tick 5 min after it first fired, opening a near-duplicate position —
positions #1/#2 (08:59/09:04) and #3/#4 (09:29/09:34), each pair exactly
30 min (= cooldown_bars) after the previous pair. is_new_signal() persists the
idx_5m of the last occurrence actually acted on so the second tick of a pair
can tell "I already saw this one."

Run:  cd backend && PYTHONPATH=. python3 tests/test_signal_dedup.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.paper_strategy import is_new_signal


def test_first_signal_ever_is_new():
    assert is_new_signal(111768, None) is True
    print("✓ no prior signal -> always new")


def test_same_idx_is_not_new():
    # This is the exact bug: tick T accepts idx=111768 via the 2-bar window,
    # tick T+1 (last_idx=111769) re-discovers the same idx=111768 via the
    # same window (idx in {111768, 111769}) -> must now be rejected.
    assert is_new_signal(111768, 111768) is False
    print("✓ same idx_5m as last-acted-on -> rejected (the bug, fixed)")


def test_earlier_idx_is_not_new():
    assert is_new_signal(111760, 111768) is False
    print("✓ idx_5m older than last-acted-on -> rejected")


def test_strictly_later_idx_is_new():
    # The next real occurrence after a fresh cooldown (e.g. +6 bars later).
    assert is_new_signal(111774, 111768) is True
    print("✓ idx_5m strictly newer than last-acted-on -> accepted")


# ───────────────────── reproduces the live double-fire sequence ─────────────────────

class FakeRepo:
    """Stand-in for paper_repo.update_state, mirroring test_cb_race.py's FakeRepo
    pattern: just enough to track last_signal_idx_5m across simulated ticks."""

    def __init__(self):
        self.row = {"last_signal_idx_5m": None}

    def update_state(self, *, last_signal_idx_5m=None, **_ignored):
        if last_signal_idx_5m is not None:
            self.row["last_signal_idx_5m"] = last_signal_idx_5m


def _tick(repo: FakeRepo, idx_5m: int) -> bool:
    """Mirrors the paper_loop callsite: accept iff is_new_signal, then persist."""
    accepted = is_new_signal(idx_5m, repo.row["last_signal_idx_5m"])
    if accepted:
        repo.update_state(last_signal_idx_5m=idx_5m)
    return accepted


def test_live_double_fire_sequence_now_deduped():
    """Replays the exact live idx pattern (08:59, 09:04, 09:29, 09:34 — bars
    5 min apart within a pair, pairs 30 min/6 bars apart) and asserts the
    second tick of each pair is now rejected instead of opening a duplicate."""
    repo = FakeRepo()
    base = 111768  # idx_5m at 08:59 (first real occurrence)

    # 08:59 — genuinely new occurrence.
    assert _tick(repo, base) is True
    # 09:04 (one bar later) — old code treated this as new too (2-bar window
    # rediscovering `base`); the generator's internal cooldown walk still
    # reports the SAME occurrence idx (base), since 6 bars haven't passed.
    assert _tick(repo, base) is False, "must reject the duplicate at +5min"

    # 30 min later (6 bars) — a genuinely new cooldown-spaced occurrence.
    next_occurrence = base + 6
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

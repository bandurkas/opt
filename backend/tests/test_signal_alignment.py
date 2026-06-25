"""Unit tests for check_new_signal's cooldown-alignment fix (2026-06-25).

Live symptom: the dashboard gauge showed ready=True/100% repeatedly (the bot
WAS in a continuously-qualifying state) but check_new_signal kept returning
None ("no signal") for hours. Root cause: check_new_signal only looked at
the last 2 positions (idx_5m in {last-1, last}) of its caller's k5 array —
but k5 is always the latest `window_5m=2100` bars (a SLIDING window), so
"the last position" is always array index 2099, a POSITION, not a calendar
identity. The generator's own cooldown_bars walk schedules fires every
cooldown_bars CALENDAR bars starting from whenever the qualifying run began
— a fixed, deterministic calendar schedule — but the live poll only ever
checks a 2-bar-wide slice of it once per tick, so it only catches a
scheduled fire on ~2 of every cooldown_bars ticks (~33% for cooldown_bars=6)
and silently misses the other ~67%, even though nothing about the entry
conditions was actually deficient.

Fix: call the generator with cooldown_bars=0 (report every gate-passing bar,
not just its own blind-walk-cooldown subset), keep the tight 2-bar freshness
check, and enforce the REAL cooldown using ts_ms (calendar-stable, unlike
idx_5m) against state.last_signal_ts_ms.

These tests replace gen_sell_premium_iv_high with a fake that always reports
the requested side as eligible at every bar position — i.e. simulates a
market that continuously satisfies vol/regime/mtf/bull for many ticks in a
row — and replays check_new_signal across many simulated live ticks (the
sliding window advancing by exactly one bar per tick, exactly like
paper_loop's real per-5-minute poll), threading state.last_signal_ts_ms
forward exactly like the real call site does.

Run:  cd backend && PYTHONPATH=. python3 tests/test_signal_alignment.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.paper_loop as paper_loop_mod
from services.paper_loop import check_new_signal, is_new_signal, BARS_7D, BAR_MS_5M

ETH_FLAT = 2000.0
BASE_TS = 1_700_000_000_000


def _flat_k5(n: int, ret_7d_anchor_side: str | None = None) -> list[dict]:
    """Flat 5m history, long enough for compute_ret_7d. If
    ret_7d_anchor_side is set, nudges the BARS_7D-back bar to force that
    side's trend zone (mirrors test_mtf_anchor.py's helper) -- otherwise
    leaves price flat (range zone, both sides allowed)."""
    bars = [{"start_ms": i * 60_000, "open": ETH_FLAT, "high": ETH_FLAT,
             "low": ETH_FLAT, "close": ETH_FLAT, "volume": 1.0} for i in range(n)]
    if ret_7d_anchor_side is not None:
        anchor_idx = (n - 1) - BARS_7D
        bars[anchor_idx]["close"] = ETH_FLAT * (1.05 if ret_7d_anchor_side == "C" else 0.95)
    return bars


class FakeAlwaysReadyGen:
    """Stand-in for gen_sell_premium_iv_high: reports the requested side as
    eligible at EVERY bar position in the array it's given, with ts_ms
    computed from a mutable `tick` counter the test advances once per
    simulated live poll -- mirrors how the SAME array position (e.g. the
    last one) represents an ever-later calendar bar as live time advances,
    while idx_5m (position) stays the same across ticks."""

    def __init__(self):
        self.tick = 0

    def __call__(self, k5, k15, k1h, **kwargs):
        side = kwargs.get("side")
        return [{"idx_5m": i, "ts_ms": BASE_TS + (self.tick + i) * BAR_MS_5M,
                 "close": ETH_FLAT, "side": side, "position": "short_premium"}
                for i in range(len(k5))]


def test_continuous_qualification_fires_every_cooldown_bars_ticks():
    fake = FakeAlwaysReadyGen()
    orig_gen = paper_loop_mod.gen_sell_premium_iv_high
    paper_loop_mod.gen_sell_premium_iv_high = fake
    try:
        k5 = _flat_k5(BARS_7D + 5, ret_7d_anchor_side="C")
        last_signal_ts_ms = None
        fires = []
        n_ticks = 60
        cooldown_bars = 6  # CALL_GEN_KWARGS live value
        for t in range(n_ticks):
            fake.tick = t
            sig = check_new_signal(k5, [], [], last_signal_ts_ms=last_signal_ts_ms)
            if sig is not None:
                fires.append(t)
                last_signal_ts_ms = sig["ts_ms"]
        # Continuously qualifying for n_ticks ticks at cooldown_bars spacing
        # -> floor(n_ticks/cooldown_bars) fires, evenly spaced -- not the
        # ~33% catch rate the alignment bug produced.
        expected = n_ticks // cooldown_bars
        assert len(fires) == expected, (fires, expected)
        gaps = [fires[i + 1] - fires[i] for i in range(len(fires) - 1)]
        assert all(g == cooldown_bars for g in gaps), gaps
        print(f"✓ continuous qualification -> {len(fires)} fires across {n_ticks} ticks, "
              f"evenly spaced every {cooldown_bars} ticks (not ~33% alignment-miss rate)")
    finally:
        paper_loop_mod.gen_sell_premium_iv_high = orig_gen


def test_cooldown_uses_real_elapsed_time_not_tick_count():
    """If the bot is polled irregularly (gaps in ticks, e.g. a restart),
    cooldown must still be governed by REAL elapsed calendar time (ts_ms),
    not by how many simulated ticks happened to occur -- this is the whole
    point of switching from idx_5m (position) to ts_ms (calendar-stable)."""
    fake = FakeAlwaysReadyGen()
    orig_gen = paper_loop_mod.gen_sell_premium_iv_high
    paper_loop_mod.gen_sell_premium_iv_high = fake
    try:
        k5 = _flat_k5(BARS_7D + 5, ret_7d_anchor_side="C")

        fake.tick = 0
        sig1 = check_new_signal(k5, [], [], last_signal_ts_ms=None)
        assert sig1 is not None
        last_ts = sig1["ts_ms"]

        # Only 2 bars later (< cooldown_bars=6) -> must still be rejected.
        fake.tick = 2
        sig2 = check_new_signal(k5, [], [], last_signal_ts_ms=last_ts)
        assert sig2 is None, sig2

        # Exactly cooldown_bars later -> eligible again.
        fake.tick = 6
        sig3 = check_new_signal(k5, [], [], last_signal_ts_ms=last_ts)
        assert sig3 is not None, sig3
        print("✓ cooldown enforced against real elapsed ts_ms (2 bars too early "
              "rejected, 6 bars later accepted)")
    finally:
        paper_loop_mod.gen_sell_premium_iv_high = orig_gen


def test_is_new_signal_defense_in_depth_matches_ts_ms():
    """The is_new_signal secondary guard in paper_loop's call site must agree
    with check_new_signal's own cooldown decision when fed the same ts_ms
    values (no double-rejection or accidental pass-through)."""
    base = BASE_TS
    assert is_new_signal(base, None) is True
    assert is_new_signal(base, base) is False
    assert is_new_signal(base + 6 * BAR_MS_5M, base) is True
    print("✓ is_new_signal agrees with ts_ms-based cooldown semantics")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} signal-alignment tests passed ✓")


if __name__ == "__main__":
    main()

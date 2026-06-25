"""Unit tests for entry_proximity — the dashboard entry-proximity gauge score.

Display/observability only (ADX-score sizing was rejected by backtest). The key
invariant: 100 is reserved for `ready` AND a *confirmed* live debounce window
(paper_loop's FLICKER_TOLERANCE persistence check) that is not disqualified —
the gauge must never show a full signal the bot wouldn't actually fire on, in
either direction. Confirmation requires window_status to be both fresh AND for
the SAME 5m window as the live cond snapshot; anything else (missing, stale,
or from a different window) is `debounce_unknown` and the gauge stays below
100 (see 2026-06-25 gauge/entry-logic desync fix: gauge was missing regime_ok,
had no visibility into the bot's debounce state, and — in a first pass at this
fix — trusted a stale/cross-window status as confirmation by mistake).

Run:  cd backend && PYTHONPATH=. python3 tests/test_proximity.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.paper_strategy import entry_proximity, window_id

# now_ms used throughout: epoch_min = 1_000_500 // 60_000 = 16 -> wid = 16//5 = 3
NOW_MS = 1_000_500
SAME_WID = window_id(NOW_MS // 60_000)
assert SAME_WID == 3, SAME_WID
OTHER_WID = SAME_WID - 1


def test_ready_without_window_status_is_capped_below_100():
    # ready=True but no window_status at all -> debounce state unconfirmed,
    # gauge must stay conservative (never claim "entry" on cond alone).
    p = entry_proximity({"ready": True, "mtf_aligned_count": 3,
                         "vol_pctile": 1.0, "regime_ok": True,
                         "bull_filter_ok": True}, 10.0)
    assert p["proximity_pct"] < 100.0, p
    assert p["zone"] != "entry", p
    assert p["debounce_unknown"] is True, p
    print("✓ ready with no window_status -> capped below 100%, debounce_unknown=True")


def test_not_ready_is_capped_below_100():
    # All factors maxed but ready=False (e.g. regime still transition) -> < 100.
    p = entry_proximity({"ready": False, "mtf_aligned_count": 3,
                         "vol_pctile": 1.0, "regime_ok": True,
                         "bull_filter_ok": True}, 10.0)
    assert p["proximity_pct"] == 99.0, p
    assert p["zone"] == "ready", p
    print("✓ not-ready never reaches 100% (capped 99)")


def test_empty_factors_is_waiting():
    p = entry_proximity({"ready": False, "mtf_aligned_count": None,
                         "vol_pctile": None, "regime_ok": False,
                         "bull_filter_ok": False}, 0.0)
    assert p["proximity_pct"] == 0.0, p
    assert p["zone"] == "waiting", p
    print("✓ no factors -> 0%, zone=waiting")


def test_weighted_blend_value():
    # adx=10/10=1 (w.30), mtf=2/3 (w.20), vol=0.5 (w.15), regime ok (w.20), bull ok (w.15)
    # = 100*(.30 + .20*0.6667 + .15*0.5 + .20 + .15) = 100*(.30+.13333+.075+.20+.15)=85.83
    p = entry_proximity({"ready": False, "mtf_aligned_count": 2,
                         "vol_pctile": 0.5, "regime_ok": True,
                         "bull_filter_ok": True}, 10.0)
    assert abs(p["proximity_pct"] - 85.8) < 0.2, p
    assert p["zone"] == "ready", p
    print(f"✓ weighted blend = {p['proximity_pct']}% (zone={p['zone']})")


def test_clamps_out_of_range_inputs():
    # adx_score above 10 and vol_pctile above 1 must clamp, not overflow.
    p = entry_proximity({"ready": False, "mtf_aligned_count": 9,
                         "vol_pctile": 5.0, "regime_ok": True,
                         "bull_filter_ok": True}, 50.0)
    assert p["proximity_pct"] == 99.0, p
    assert all(0.0 <= v <= 1.0 for v in p["factors"].values()), p
    print("✓ out-of-range inputs clamp to [0,1] factors")


def test_preparing_zone_midrange():
    # adx=9/10=0.9, mtf=3/3=1, vol=0.5, regime not ok, bull not ok
    # = 100*(.30*.9 + .20*1 + .15*.5 + 0 + 0) = 100*(.27+.20+.075)=54.5
    p = entry_proximity({"ready": False, "mtf_aligned_count": 3,
                         "vol_pctile": 0.5, "regime_ok": False,
                         "bull_filter_ok": False}, 9.0)
    assert 50.0 <= p["proximity_pct"] < 80.0, p
    assert p["zone"] == "preparing", p
    print(f"✓ midrange -> {p['proximity_pct']}%, zone=preparing")


def test_regime_failure_measurably_lowers_the_gauge():
    # Before the fix, entry_proximity had no regime factor at all — a window
    # that failed only regime_ok (vol/mtf/bull all passing) could still show
    # a near-100% gauge. Now regime_ok is weighted like bull/vol: failing it
    # must visibly drag the composite down, not be free.
    with_regime = entry_proximity({"ready": False, "mtf_aligned_count": 3,
                                   "vol_pctile": 1.0, "regime_ok": True,
                                   "bull_filter_ok": True}, 10.0)
    without_regime = entry_proximity({"ready": False, "mtf_aligned_count": 3,
                                      "vol_pctile": 1.0, "regime_ok": False,
                                      "bull_filter_ok": True}, 10.0)
    assert with_regime["proximity_pct"] > without_regime["proximity_pct"], (
        with_regime, without_regime)
    print("✓ regime_ok=False measurably lowers the gauge vs regime_ok=True")


def test_window_disqualified_blocks_entry_zone_even_when_ready():
    # The bot's debounce check (paper_loop FLICKER_TOLERANCE) disqualified this
    # window — even though the live cond snapshot says ready, the bot will NOT
    # fire this window, so the gauge must not claim "entry" either.
    fresh_disqualified = {"wid": SAME_WID, "disqualified": True, "checked_at_ms": NOW_MS - 5_000}
    p = entry_proximity({"ready": True, "mtf_aligned_count": 3,
                         "vol_pctile": 1.0, "regime_ok": True,
                         "bull_filter_ok": True}, 10.0,
                         window_status=fresh_disqualified, now_ms=NOW_MS)
    assert p["proximity_pct"] < 100.0, p
    assert p["zone"] != "entry", p
    assert p["window_disqualified"] is True, p
    assert p["debounce_unknown"] is False, p
    print("✓ disqualified window caps the gauge below 'entry' despite ready=True")


def test_window_not_disqualified_and_same_window_allows_entry_zone():
    fresh_ok = {"wid": SAME_WID, "disqualified": False, "checked_at_ms": NOW_MS - 5_000}
    p = entry_proximity({"ready": True, "mtf_aligned_count": 3,
                         "vol_pctile": 1.0, "regime_ok": True,
                         "bull_filter_ok": True}, 10.0,
                         window_status=fresh_ok, now_ms=NOW_MS)
    assert p["proximity_pct"] == 100.0, p
    assert p["zone"] == "entry", p
    assert p["debounce_unknown"] is False, p
    print("✓ confirmed (fresh + same window) status, not disqualified -> 100%, zone=entry")


def test_stale_window_status_falls_back_to_unconfirmed():
    # paper_loop stopped writing window_status (or the process died) — a stale
    # timestamp must not be trusted as confirmation, in either direction.
    stale = {"wid": SAME_WID, "disqualified": True, "checked_at_ms": 0}
    p = entry_proximity({"ready": True, "mtf_aligned_count": 3,
                         "vol_pctile": 1.0, "regime_ok": True,
                         "bull_filter_ok": True}, 10.0,
                         window_status=stale, now_ms=1_000_000_000)
    assert p["debounce_unknown"] is True, p
    assert p["proximity_pct"] < 100.0, p
    assert p["zone"] != "entry", p
    print("✓ stale window_status -> debounce_unknown=True, capped below 100%")


def test_cross_window_status_is_not_trusted_even_if_fresh():
    # Fresh by clock (5s old, well under the staleness threshold) but it's the
    # PREVIOUS window's status — e.g. paper_loop just rolled into a new window
    # and hasn't run that window's first per-minute check yet. Must not be
    # applied to the new window's cond snapshot in either direction.
    fresh_but_other_window = {"wid": OTHER_WID, "disqualified": False,
                              "checked_at_ms": NOW_MS - 5_000}
    p = entry_proximity({"ready": True, "mtf_aligned_count": 3,
                         "vol_pctile": 1.0, "regime_ok": True,
                         "bull_filter_ok": True}, 10.0,
                         window_status=fresh_but_other_window, now_ms=NOW_MS)
    assert p["debounce_unknown"] is True, p
    assert p["proximity_pct"] < 100.0, p
    assert p["zone"] != "entry", p
    print("✓ fresh-but-different-window status is not trusted -> debounce_unknown=True")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} proximity tests passed ✓")


if __name__ == "__main__":
    main()

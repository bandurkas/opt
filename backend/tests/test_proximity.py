"""Unit tests for entry_proximity — the dashboard entry-proximity gauge score.

Display/observability only (ADX-score sizing was rejected by backtest). The key
invariant: 100 is reserved for `ready` (every gate passes), so the gauge never
shows a full signal the bot wouldn't take.

Run:  cd backend && PYTHONPATH=. python3 tests/test_proximity.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.paper_strategy import entry_proximity


def test_ready_is_full_and_entry_zone():
    # ready=True must pin to 100 even if a raw factor is missing.
    p = entry_proximity({"ready": True, "mtf_aligned_count": 3,
                         "vol_pctile": 1.0, "bull_filter_ok": True}, 10.0)
    assert p["proximity_pct"] == 100.0, p
    assert p["zone"] == "entry", p
    print("✓ ready -> 100%, zone=entry")


def test_not_ready_is_capped_below_100():
    # All factors maxed but ready=False (e.g. regime still transition) -> < 100.
    p = entry_proximity({"ready": False, "mtf_aligned_count": 3,
                         "vol_pctile": 1.0, "bull_filter_ok": True}, 10.0)
    assert p["proximity_pct"] == 99.0, p
    assert p["zone"] == "ready", p
    print("✓ not-ready never reaches 100% (capped 99)")


def test_empty_factors_is_waiting():
    p = entry_proximity({"ready": False, "mtf_aligned_count": None,
                         "vol_pctile": None, "bull_filter_ok": False}, 0.0)
    assert p["proximity_pct"] == 0.0, p
    assert p["zone"] == "waiting", p
    print("✓ no factors -> 0%, zone=waiting")


def test_weighted_blend_value():
    # adx=10/10=1 (w.40), mtf=2/3 (w.25), vol=0.5 (w.20), bull=ok (w.15)
    # = 100*(.40 + .25*0.6667 + .20*0.5 + .15) = 100*(.40+.16667+.10+.15)=81.67
    p = entry_proximity({"ready": False, "mtf_aligned_count": 2,
                         "vol_pctile": 0.5, "bull_filter_ok": True}, 10.0)
    assert abs(p["proximity_pct"] - 81.7) < 0.2, p
    assert p["zone"] == "ready", p
    print(f"✓ weighted blend = {p['proximity_pct']}% (zone={p['zone']})")


def test_clamps_out_of_range_inputs():
    # adx_score above 10 and vol_pctile above 1 must clamp, not overflow.
    p = entry_proximity({"ready": False, "mtf_aligned_count": 9,
                         "vol_pctile": 5.0, "bull_filter_ok": True}, 50.0)
    assert p["proximity_pct"] == 99.0, p
    assert all(0.0 <= v <= 1.0 for v in p["factors"].values()), p
    print("✓ out-of-range inputs clamp to [0,1] factors")


def test_preparing_zone_midrange():
    # adx=5/10=0.5 only -> 100*.40*.5 = 20 ... bump mtf to land in 50-80
    p = entry_proximity({"ready": False, "mtf_aligned_count": 3,
                         "vol_pctile": 0.5, "bull_filter_ok": False}, 6.0)
    # 100*(.40*.6 + .25*1 + .20*.5 + 0) = 100*(.24+.25+.10)=59
    assert 50.0 <= p["proximity_pct"] < 80.0, p
    assert p["zone"] == "preparing", p
    print(f"✓ midrange -> {p['proximity_pct']}%, zone=preparing")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} proximity tests passed ✓")


if __name__ == "__main__":
    main()

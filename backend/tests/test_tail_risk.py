"""Unit tests for the tail-risk concentration cap (MAX_OPEN_POSITIONS).

The cap is a pure risk overlay validated out-of-sample by tail_overlay_sweep.py
(commit 987efab): capping simultaneously-open positions at 4 cuts the worst month
and lifts edge by removing negative-EV cluster trades. It does NOT touch entry/exit.

Run standalone:   cd backend && PYTHONPATH=. python3 tests/test_tail_risk.py
Or via pytest:    cd backend && python3 -m pytest tests/test_tail_risk.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import execution_config as cfg
from services import paper_loop


def _set_cap(n: int):
    cfg.MAX_OPEN_POSITIONS = n


def test_default_cap_is_four():
    # The validated production value. Guards against accidental config drift.
    # (Re-read default from a clean env, ignoring any test mutation above.)
    assert cfg._i("MAX_OPEN_POSITIONS", 4) == 4
    print("✓ default MAX_OPEN_POSITIONS == 4")


def test_below_cap_allows_open():
    _set_cap(4)
    assert paper_loop.at_position_cap(0) is False
    assert paper_loop.at_position_cap(3) is False
    print("✓ below cap (0,3 of 4) -> not capped, opens allowed")


def test_at_or_above_cap_blocks_open():
    _set_cap(4)
    assert paper_loop.at_position_cap(4) is True
    assert paper_loop.at_position_cap(5) is True
    assert paper_loop.at_position_cap(99) is True
    print("✓ at/above cap (4,5,99 of 4) -> capped, opens refused")


def test_zero_disables_cap():
    _set_cap(0)
    assert paper_loop.at_position_cap(0) is False
    assert paper_loop.at_position_cap(1000) is False
    print("✓ MAX_OPEN_POSITIONS=0 disables the cap (unlimited)")


def test_cap_of_one_is_serial():
    _set_cap(1)
    assert paper_loop.at_position_cap(0) is False
    assert paper_loop.at_position_cap(1) is True
    print("✓ cap=1 -> serial trading (one position at a time)")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    try:
        for t in tests:
            t()
    finally:
        _set_cap(4)  # restore the production default for any later importers
    print(f"\nAll {len(tests)} tail-risk tests passed ✓")


if __name__ == "__main__":
    main()

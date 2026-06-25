"""Unit tests for the CALL-only 1h MTF anchor (2026-06-25).

Backtest (sniper_mtf_loosen_backtest.py, 388 days real ETH OHLCV, 70/30
train/holdout): the live MTF gate (>=2/3 of 5m/15m/1h aligned with the
required direction) was loosened to "1h's own direction decides" for the
CALL side only — train avg -0.78%->+4.00%, holdout +0.79%->+2.69%, n
1128->1239. Same anchor applied to PUT degraded train to -8.98% — PUT is
explicitly NOT changed.

direction_filter_ok() (momentum_mtf.py) is the single source of truth used
by BOTH the real generator (strategy_registry.gen_sell_premium_iv_high) and
the dashboard/debounce gauge (paper_strategy.evaluate_conditions) — these
tests exercise that shared function plus the evaluate_conditions plumbing
around it (active_side resolution, trend-zone back-compat, the
ready != active_side-is-not-None regression caught while building this).

Run:  cd backend && PYTHONPATH=. python3 tests/test_mtf_anchor.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.momentum_mtf import direction_filter_ok
from services.paper_strategy import evaluate_conditions, BARS_7D
from services.strategy_config import CALL_GEN_KWARGS, PUT_GEN_KWARGS

ETH_FLAT = 2000.0


# ───────────── config sanity (regression guard against accidental copy to PUT) ─────────────

def test_call_has_anchor_put_does_not():
    assert CALL_GEN_KWARGS.get("mtf_anchor_tf") == "1h", CALL_GEN_KWARGS
    assert PUT_GEN_KWARGS.get("mtf_anchor_tf") is None, PUT_GEN_KWARGS
    print("✓ CALL_GEN_KWARGS has mtf_anchor_tf='1h', PUT_GEN_KWARGS does not")


# ───────────── direction_filter_ok (shared gate, unit-level) ─────────────

def _mtf(direction: str, aligned: int, d1h: str) -> dict:
    return {"direction": direction, "tfs_aligned": aligned,
            "tf_1h": {"direction": d1h}}


def test_filter_none_always_ok():
    assert direction_filter_ok(_mtf("neutral", 1, "neutral"), None) is True
    print("✓ mtf_filter=None always passes (no directional requirement)")


def test_current_mode_requires_majority_and_matching_direction():
    # 3/3 aligned down, filter='down' -> ok
    assert direction_filter_ok(_mtf("down", 3, "down"), "down") is True
    # 2/3 aligned down, filter='down' -> ok (>= min_aligned default 2)
    assert direction_filter_ok(_mtf("down", 2, "down"), "down") is True
    # consensus says 'neutral' (no majority) even if 1h happens to be down -> rejected
    assert direction_filter_ok(_mtf("neutral", 1, "down"), "down") is False
    # consensus is 'up' (wrong direction) -> rejected regardless of aligned count
    assert direction_filter_ok(_mtf("up", 2, "down"), "down") is False
    print("✓ default (no anchor) mode: needs consensus direction match + >=2/3 aligned")


def test_anchor_1h_uses_only_1h_direction():
    # 5m/15m disagree (consensus='neutral'), but 1h alone says 'down' -> anchor passes
    assert direction_filter_ok(_mtf("neutral", 1, "down"), "down", anchor_tf="1h") is True
    # 1h says 'up' even though overall consensus claims 'down' (contrived, but the
    # anchor must ignore the consensus field entirely and look at tf_1h only)
    assert direction_filter_ok(_mtf("down", 2, "up"), "down", anchor_tf="1h") is False
    # 1h neutral -> never matches a concrete filter
    assert direction_filter_ok(_mtf("down", 3, "neutral"), "down", anchor_tf="1h") is False
    print("✓ anchor_tf='1h' mode: only tf_1h.direction matters, consensus/aligned ignored")


def test_anchor_relaxes_strictly_more_than_current():
    # Anything current() accepts, anchor() also accepts when 1h matches (since
    # current requiring 2/3 aligned with 'down' implies 1h is one of the down votes
    # only in the 3/3 case for certain — check the actual implication directly via
    # the two clearest comparable cases instead of a general proof).
    mtf_3of3 = _mtf("down", 3, "down")
    assert direction_filter_ok(mtf_3of3, "down") is True
    assert direction_filter_ok(mtf_3of3, "down", anchor_tf="1h") is True
    # The whole POINT of anchor mode: a window current() rejects (neutral, 1h down)
    # that anchor() rescues.
    mtf_rescued = _mtf("neutral", 1, "down")
    assert direction_filter_ok(mtf_rescued, "down") is False
    assert direction_filter_ok(mtf_rescued, "down", anchor_tf="1h") is True
    print("✓ anchor mode rescues the exact population it was backtested on "
          "(consensus neutral, 1h-only agreement)")


# ───────────── evaluate_conditions integration (real candle construction) ─────────────

def _flat_bars(n: int, price: float = ETH_FLAT, step_ms: int = 60_000) -> list[dict]:
    return [{"start_ms": i * step_ms, "open": price, "high": price,
              "low": price, "close": price, "volume": 1.0} for i in range(n)]


def _ramp_bars(n: int, start_price: float, end_price: float, step_ms: int) -> list[dict]:
    out = []
    for i in range(n):
        price = start_price + (end_price - start_price) * i / max(1, n - 1)
        out.append({"start_ms": i * step_ms, "open": price, "high": price,
                     "low": price, "close": price, "volume": 1.0})
    return out


def _k5_forcing_trend_side(side: str, n: int = BARS_7D + 260, price: float = ETH_FLAT) -> list[dict]:
    """Flat 5m history except the bar exactly BARS_7D back, moved far enough
    to force ret_7d past +-0.5% — single forced side, bypassing MTF for
    active_side selection (trend zone). Mirrors test_bull_filter_gauge.py's
    helper."""
    bars = _flat_bars(n, price=price)
    anchor_idx = (n - 1) - BARS_7D
    bars[anchor_idx]["close"] = price * (1.05 if side == "C" else 0.95)
    return bars


def test_trend_zone_call_anchor_overrides_majority():
    """Force the Call-only trend zone (ret_7d < -0.5%), then build 5m/15m
    ramping UP (would normally vote 'up', wrong direction for Call) while 1h
    ramps DOWN. Under the live CALL_GEN_KWARGS (anchor_tf='1h'), the 1h
    direction alone must decide -> mtf_direction_ok=True despite 5m/15m
    voting the opposite way."""
    price = ETH_FLAT
    k5 = _k5_forcing_trend_side("C", price=price)
    # Tail (last 240 bars of analyze_tf's window) ramps UP for 5m/15m.
    tail_up_5m = _ramp_bars(260, price * 0.97, price * 1.03, 60_000)
    tail_up_15m = _ramp_bars(260, price * 0.97, price * 1.03, 900_000)
    k5 = k5[:-260] + tail_up_5m
    k15 = tail_up_15m
    # 1h ramps DOWN strongly enough to set direction='down', rsi<50.
    k1h = _ramp_bars(260, price * 1.10, price * 0.90, 3_600_000)

    ev = evaluate_conditions(k5, k15, k1h)
    assert ev["active_side"] == "C", ev
    assert ev["mtf_direction"] == "up" or ev["mtf_aligned_count"] < 2, (
        f"test fixture must produce a 3-way consensus that is NOT a clean "
        f"'down' majority for this to be a meaningful test: {ev}")
    assert ev["mtf_direction_ok"] is True, (
        f"CALL's 1h-anchor must accept on 1h-down alone, ignoring the "
        f"5m/15m 'up' votes: {ev}")
    print(f"✓ CALL anchor accepts on 1h-down alone (consensus direction was "
          f"{ev['mtf_direction']!r}, aligned={ev['mtf_aligned_count']})")


def test_trend_zone_call_rejects_when_1h_disagrees():
    """Same trend zone, but now 1h also ramps UP (matches 5m/15m) — the
    anchor must REJECT, proving it isn't a rubber stamp; it actively checks
    1h's own direction, not just 'anything goes for Call'."""
    price = ETH_FLAT
    k5 = _k5_forcing_trend_side("C", price=price)
    tail_up_5m = _ramp_bars(260, price * 0.97, price * 1.03, 60_000)
    tail_up_15m = _ramp_bars(260, price * 0.97, price * 1.03, 900_000)
    k5 = k5[:-260] + tail_up_5m
    k15 = tail_up_15m
    k1h = _ramp_bars(260, price * 0.90, price * 1.10, 3_600_000)  # also UP

    ev = evaluate_conditions(k5, k15, k1h)
    assert ev["active_side"] == "C", ev
    assert ev["mtf_direction_ok"] is False, (
        f"CALL's 1h-anchor must reject when 1h itself is 'up', not 'down': {ev}")
    print("✓ CALL anchor rejects when 1h itself disagrees with the needed direction")


def test_put_side_unaffected_still_needs_majority():
    """Force the Put-only trend zone with the exact same kind of mismatch
    (5m/15m one way, 1h the other) — PUT_GEN_KWARGS has no anchor, so it
    must keep using the >=2/3 consensus majority rule unchanged."""
    price = ETH_FLAT
    k5 = _k5_forcing_trend_side("P", price=price)
    # 5m/15m DOWN, 1h UP — consensus picks the 5m/15m majority (down, 2/3),
    # but PUT needs 'up' -> must reject under the unchanged majority rule.
    tail_down_5m = _ramp_bars(260, price * 1.03, price * 0.97, 60_000)
    tail_down_15m = _ramp_bars(260, price * 1.03, price * 0.97, 900_000)
    k5 = k5[:-260] + tail_down_5m
    k15 = tail_down_15m
    k1h = _ramp_bars(260, price * 0.90, price * 1.10, 3_600_000)  # up

    ev = evaluate_conditions(k5, k15, k1h)
    assert ev["active_side"] == "P", ev
    assert ev["mtf_direction"] == "down", ev  # 2/3 majority (5m+15m) wins
    assert ev["mtf_direction_ok"] is False, (
        f"PUT must still require the 3-way majority to say 'up' — no anchor "
        f"rescue for PUT: {ev}")
    print("✓ PUT side unaffected: still rejects on a 'down' 2/3 majority needing 'up'")


def test_trend_zone_ready_reflects_real_gate_not_just_forced_side():
    """Regression: a refactor pass briefly set ready = (active_side is not
    None), which is ALWAYS true in trend zone (active_side is forced
    regardless of gates) — silently reporting ready=True even when vol/regime/
    mtf/bull all fail. ready must reflect the forced side's actual gate
    outcome."""
    price = ETH_FLAT
    k5 = _k5_forcing_trend_side("C", price=price)
    k15 = _flat_bars(260, price=price)
    k1h = _flat_bars(260, price=price)  # flat -> mtf direction 'neutral', vol low -> not ready

    ev = evaluate_conditions(k5, k15, k1h)
    assert ev["active_side"] == "C", ev
    assert ev["ready"] is False, (
        f"flat/neutral market must NOT be ready just because trend zone "
        f"forces active_side='C': {ev}")
    print("✓ ready stays False in trend zone when the forced side's own gates fail")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} MTF-anchor tests passed ✓")


if __name__ == "__main__":
    main()

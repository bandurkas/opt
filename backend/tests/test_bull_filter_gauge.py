"""Unit tests for evaluate_conditions's bull-market filter (fixed 2026-06-25).

Bug: the filter was gated on `active_side == "P"`, but PUT_GEN_KWARGS sets
bull_market_ratio_max=None (never filters) while CALL_GEN_KWARGS sets 1.05
(the real cap) — so the live "Условия входа" gauge never actually enforced
this filter for either side, even though the real generator
(gen_sell_premium_iv_high, strategy_registry.py) applies it unconditionally
by side. Confirmed via direct comparison against the real generator's raw
gate-pass set on full ETH history: 0/1500 mismatches after the fix (was a
real but rarely-binding gap — EMA50/EMA200 ratio stays close to 1.0 during
the range/transition regimes Call requires, so it didn't cause bad live
trades, but the gauge display and the per-minute persistence check were
both silently skipping a real gate for Calls).

Run:  cd backend && PYTHONPATH=. python3 tests/test_bull_filter_gauge.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.paper_strategy import evaluate_conditions, BARS_7D

ETH_FLAT = 2000.0


def _flat_bars(n: int, start_ms: int = 0, step_ms: int = 60_000, price: float = ETH_FLAT) -> list[dict]:
    return [{"start_ms": start_ms + i * step_ms, "open": price, "high": price,
              "low": price, "close": price, "volume": 1.0} for i in range(n)]


def _ramped_1h(n: int, start_price: float, end_price: float) -> list[dict]:
    """1h closes ramping linearly from start_price to end_price — used to
    drive EMA50/EMA200 ratio above the 1.05 Call cap."""
    out = []
    for i in range(n):
        price = start_price + (end_price - start_price) * i / max(1, n - 1)
        out.append({"start_ms": i * 3_600_000, "open": price, "high": price,
                     "low": price, "close": price, "volume": 1.0})
    return out


def _k5_forcing_side(side: str, n: int = BARS_7D + 10, price: float = ETH_FLAT) -> list[dict]:
    """Flat 5m history except the bar exactly BARS_7D back, which is moved
    far enough to force ret_7d past the ±0.5% trend-zone threshold — this
    makes allowed_sides() return a single forced side, bypassing the MTF
    preference branch entirely so the test doesn't depend on analyze_tf."""
    bars = _flat_bars(n, price=price)
    anchor_idx = (n - 1) - BARS_7D
    bars[anchor_idx]["close"] = price * (1.05 if side == "C" else 0.95)
    return bars


def test_bull_filter_now_applies_to_call_side():
    # Force the Call-only trend zone (ret_7d < -0.5%) so active_side='C'
    # regardless of MTF, then give it a 1h history whose EMA50/EMA200 ratio
    # breaches CALL_GEN_KWARGS's bull_market_ratio_max=1.05 — exactly the
    # "short-term dip inside a broader uptrend" scenario the filter exists
    # to catch (calls get crushed if the bull trend resumes).
    k1h = _ramped_1h(260, 1500.0, 2200.0)
    k5 = _k5_forcing_side("C", price=k1h[-1]["close"])
    k15 = _flat_bars(260, price=k1h[-1]["close"])

    ev = evaluate_conditions(k5, k15, k1h)
    assert ev["active_side"] == "C", ev
    assert ev["ema_ratio"] is not None, "ema_ratio must be computed for the Call side too"
    assert ev["ema_ratio"] > 1.05, f"test fixture didn't breach the cap: {ev['ema_ratio']}"
    assert ev["bull_filter_ok"] is False, (
        f"bull filter must reject Call when ema_ratio={ev['ema_ratio']} > 1.05 — "
        f"this was the bug: it only ever applied when active_side=='P', so this "
        f"exact Call-side bull breach was silently waved through by the gauge")
    print(f"✓ bull filter correctly rejects Call at ema_ratio={ev['ema_ratio']}")


def test_put_side_unaffected_bull_market_ratio_max_is_none():
    # PUT_GEN_KWARGS.bull_market_ratio_max is None (no-op by design) — same
    # high-ratio 1h history, but forced to the Put-only trend zone, must
    # leave bull_filter_ok True (nothing to enforce for Put today).
    k1h = _ramped_1h(260, 1500.0, 2200.0)
    k5 = _k5_forcing_side("P", price=k1h[-1]["close"])
    k15 = _flat_bars(260, price=k1h[-1]["close"])

    ev = evaluate_conditions(k5, k15, k1h)
    assert ev["active_side"] == "P", ev
    # bull_max is None for Put, so the block is skipped entirely (not even
    # computed) — ema_ratio stays None, same as before this side was ever
    # touched by the bull filter.
    assert ev["ema_ratio"] is None, ev
    assert ev["bull_filter_ok"] is True, "PUT's bull_market_ratio_max=None must never reject"
    print("✓ Put side unaffected (bull_market_ratio_max=None -> filter skipped entirely)")


def test_bull_filter_passes_flat_market():
    k1h = _flat_bars(260, price=ETH_FLAT)
    k5 = _k5_forcing_side("C", price=ETH_FLAT)
    k15 = _flat_bars(260, price=ETH_FLAT)

    ev = evaluate_conditions(k5, k15, k1h)
    assert ev["ema_ratio"] is not None
    assert abs(ev["ema_ratio"] - 1.0) < 0.01, ev["ema_ratio"]
    assert ev["bull_filter_ok"] is True
    print("✓ flat market -> ema_ratio≈1.0, bull_filter_ok=True")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} bull-filter-gauge tests passed ✓")


if __name__ == "__main__":
    main()

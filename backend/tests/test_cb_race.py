"""Unit tests for the circuit-breaker counter — race condition fix (FUTURE_WORK §5.2).

Before the fix, record_trade_result() advanced consec_losses with a split
read-modify-write (paper_repo.get_state() -> compute -> paper_repo.update_state())
across two transactions. When two positions close in one loop iteration, the
second close could read a stale snapshot and lose an increment, so the breaker
could miss 5 consecutive losses. The fix moves the whole transition into one
locked transaction (paper_repo.record_trade_outcome + the pure _next_cb_state).

These tests cover (a) the pure decision function and (b) that record_trade_result
no longer loses an increment, simulated with a fake repo whose get_state() is
deliberately stale (the old failure mode would route through it and stick at 1).

Run standalone:   cd backend && PYTHONPATH=. python3 tests/test_cb_race.py
Or via pytest:    cd backend && python3 -m pytest tests/test_cb_race.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import paper_strategy
from services.strategy_config import CB_CONSEC_LIMIT, CB_PAUSE_HOURS

NOW = 1_700_000_000_000  # fixed ms timestamp for deterministic cooldown math
PAUSE_MS = CB_PAUSE_HOURS * 60 * 60 * 1000


# ───────────────────────── pure transition (_next_cb_state) ─────────────────────────

def test_single_loss_increments():
    s = paper_strategy._next_cb_state(0, 0, [], -3.0, NOW)
    assert s["consec_losses"] == 1, s
    assert s["cb_cooldown_until_ms"] == 0, s
    print("✓ single loss -> consec 1, breaker not armed")


def test_win_resets_counter():
    s = paper_strategy._next_cb_state(3, 0, [], +2.0, NOW)
    assert s["consec_losses"] == 0, s
    print("✓ a win resets consec_losses to 0")


def test_fifth_loss_arms_breaker_and_resets():
    consec, cb_until, pnls = 0, 0, []
    for i in range(CB_CONSEC_LIMIT):
        s = paper_strategy._next_cb_state(consec, cb_until, pnls, -1.0, NOW)
        consec, cb_until, pnls = (
            s["consec_losses"], s["cb_cooldown_until_ms"], s["recent_pnls"])
    # On the CB_CONSEC_LIMIT-th loss the breaker arms and the counter resets.
    assert cb_until == NOW + PAUSE_MS, cb_until
    assert consec == 0, consec
    print(f"✓ {CB_CONSEC_LIMIT} losses -> breaker armed (+{CB_PAUSE_HOURS}h), counter reset")


def test_breaker_not_armed_before_limit():
    consec, cb_until, pnls = 0, 0, []
    for _ in range(CB_CONSEC_LIMIT - 1):
        s = paper_strategy._next_cb_state(consec, cb_until, pnls, -1.0, NOW)
        consec, cb_until, pnls = (
            s["consec_losses"], s["cb_cooldown_until_ms"], s["recent_pnls"])
    assert consec == CB_CONSEC_LIMIT - 1, consec
    assert cb_until == 0, cb_until
    print(f"✓ {CB_CONSEC_LIMIT - 1} losses -> not yet armed")


def test_recent_pnls_capped_at_50():
    consec, cb_until, pnls = 0, 0, []
    for i in range(60):
        s = paper_strategy._next_cb_state(consec, cb_until, pnls, float(i), NOW)
        consec, cb_until, pnls = (
            s["consec_losses"], s["cb_cooldown_until_ms"], s["recent_pnls"])
    assert len(pnls) == 50, len(pnls)
    assert pnls[0] == 10.0 and pnls[-1] == 59.0, (pnls[0], pnls[-1])
    print("✓ recent_pnls rolling window capped at last 50")


# ───────────────────────── race: no lost increment via record_trade_result ─────────────────────────

class FakeRepo:
    """Stand-in for paper_repo. `record_trade_outcome` models the locked,
    single-transaction path (reads the authoritative row, applies the pure
    decision, writes back). `get_state` is deliberately STALE — it hands out a
    frozen pre-trade snapshot, mirroring the pooled-connection snapshot that made
    the old split read/update lose increments. Any code that advances the breaker
    through get_state()->update_state() would stick at the stale value and the
    race tests below would fail."""

    def __init__(self):
        self.row = {"consec_losses": 0, "cb_cooldown_until_ms": 0,
                    "recent_pnls_json": []}
        self._stale = dict(self.row)  # snapshot frozen at construction time

    def ensure_state(self, start_equity_usd):
        return dict(self.row)

    def get_state(self):
        return dict(self._stale)  # STALE on purpose — never reflects later writes

    def update_state(self, *, cb_cooldown_until_ms=None, consec_losses=None,
                     recent_pnls=None):
        if cb_cooldown_until_ms is not None:
            self.row["cb_cooldown_until_ms"] = cb_cooldown_until_ms
        if consec_losses is not None:
            self.row["consec_losses"] = consec_losses
        if recent_pnls is not None:
            self.row["recent_pnls_json"] = recent_pnls

    def record_trade_outcome(self, pnl_pct, now_ms, decide):
        nxt = decide(int(self.row["consec_losses"]),
                     int(self.row["cb_cooldown_until_ms"]),
                     list(self.row["recent_pnls_json"]), pnl_pct, now_ms)
        self.row["consec_losses"] = nxt["consec_losses"]
        self.row["cb_cooldown_until_ms"] = nxt["cb_cooldown_until_ms"]
        self.row["recent_pnls_json"] = nxt["recent_pnls"]
        return nxt


def _with_fake_repo(fn):
    orig = paper_strategy.paper_repo
    fake = FakeRepo()
    paper_strategy.paper_repo = fake
    try:
        fn(fake)
    finally:
        paper_strategy.paper_repo = orig


def test_two_losses_one_iteration_no_lost_update():
    def body(fake):
        paper_strategy.record_trade_result(-5.0)
        r2 = paper_strategy.record_trade_result(-5.0)
        # The §5.2 bug would lose the 2nd increment (stale read) -> 1.
        assert r2["consec_losses"] == 2, r2
        assert fake.row["consec_losses"] == 2, fake.row
    _with_fake_repo(body)
    print("✓ two losses in one iteration -> consec 2 (no lost update)")


def test_five_losses_arm_breaker_through_record_trade_result():
    def body(fake):
        last = None
        for _ in range(CB_CONSEC_LIMIT):
            last = paper_strategy.record_trade_result(-5.0)
        assert last["cb_cooldown_until_ms"] > 0, last
        assert fake.row["cb_cooldown_until_ms"] > 0, fake.row
        assert fake.row["consec_losses"] == 0, fake.row
    _with_fake_repo(body)
    print(f"✓ {CB_CONSEC_LIMIT} losses via record_trade_result -> breaker armed")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} CB-race tests passed ✓")


if __name__ == "__main__":
    main()

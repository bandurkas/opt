"""Dollar-margin stop-loss for the ETH unconditional short straddle.

Same rationale as btc_straddle_sl.py (a %-of-premium stop understates real
tail loss near expiry since margin doesn't collapse the way premium does) —
see ETH_STRADDLE_PAPER_BOT_HANDOFF.md. Parameters are ETH's OWN re-swept
optimum, NOT a copy of BTC's. Original sweep: SL_DOLLAR_FRAC=0.3 (not BTC's
2.0), confirmed via the account-sim sweep (totRet +47.5%->+64.5%, maxDD
25.4%->12.0% at $2000 going from frac=2.0 to frac=0.3 on ETH).

2026-06-24: grogu_sl_optimization.py re-swept this with the leakage bug
fixed and reported FRAC=0.35 as best (Sharpe 5.44 holdout) — but that sweep
runs with USE_IV_RANK_FILTER=True (services/grogu_sl_optimization.py:39),
i.e. on the IV-Rank-PRE-FILTERED cycle population, not the unconditional
population this bot currently trades. Confirmed via eth_straddle_sl_resweep.py
(no filter, same leakage fix, all 381 cycles): best train-only frac is 0.15,
not 0.35, and the whole grid's Sharpe is weak (0.10-0.21) — not a confident
signal on its own. FRAC=0.35 is therefore NOT deployed standalone; it's only
valid bundled with the IV Rank + VRP entry filter (currently shadow-only,
see eth_straddle_loop.py's shadow_filter_check()). Revisit together when/if
that filter goes live — see sweep_results/grogu_sl_optimization.json and
sweep_results/grogu_window_sensitivity.json.

Pure / dependency-free — unit-tests without DB or network, same as
btc_straddle_sl.py / live_safety.py.
"""
from __future__ import annotations

IM_RATE = 0.10          # initial-margin rate estimate: IM_RATE * strike + premium
LOT_ETH = 0.10          # Bybit ETH option lot (min qty / qty step)
SL_DOLLAR_FRAC = 0.3    # ETH's own leg-Sharpe optimum — do NOT reuse BTC's 2.0 (see note above re: 0.35)
TP2_PCT = 0.80          # take-profit at 80% of premium decayed
CYCLE_H = 24.0          # hours per cycle


def margin_per_lot(strike: float, entry_premium: float, *,
                   im_rate: float = IM_RATE, lot: float = LOT_ETH) -> float:
    """Margin (USDT) posted for ONE lot at entry. Constant for the position's life."""
    if strike <= 0 or entry_premium < 0 or lot <= 0:
        return 0.0
    return (im_rate * strike + entry_premium) * lot


def sl_dollar_trip(margin_per_lot_usd: float, *, sl_dollar_frac: float = SL_DOLLAR_FRAC) -> float:
    """Dollar loss tripwire for ONE lot — constant for the position's life."""
    return sl_dollar_frac * margin_per_lot_usd


def is_tripped(*, entry_credit: float, current_buyback_ask: float, qty: float,
              sl_trip_per_lot_usd: float, lot: float = LOT_ETH) -> bool:
    """True when the unrealized loss on the short leg has reached the dollar stop.

    ``qty`` is the position's total contracts (multiple of ``lot``); the trip
    scales linearly with however many lots are actually open.
    """
    if lot <= 0 or qty <= 0:
        return False
    unrealized_loss = (current_buyback_ask - entry_credit) * qty
    trip = sl_trip_per_lot_usd * (qty / lot)
    return unrealized_loss >= trip

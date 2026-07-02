"""Dollar-margin stop-loss for the BTC unconditional short straddle.

A %-of-premium stop (the ETH bot's PUT_EXIT/CALL_EXIT pattern) is unsound here:
an ATM option's premium decays toward $0 near expiry, but its sensitivity to a
move does NOT, so a %-of-premium cap badly understates real tail loss close to
expiry (confirmed via BS re-pricing: per-leg losses up to -405% of premium —
see BTC_STRADDLE_HANDOFF.md §2.5). Margin is dominated by the strike term and
doesn't collapse near expiry, so a stop sized in dollars relative to posted
margin stays a meaningful, constant-magnitude tripwire through the option's
whole life. This is why this is its OWN module, not bent into the existing
strategy_config.py %-based shape.

Pure / dependency-free — unit-tests without DB or network, same as live_safety.py.
"""
from __future__ import annotations

IM_RATE = 0.10          # initial-margin rate estimate: IM_RATE * strike + premium
LOT_BTC = 0.01          # Bybit BTC option lot (min qty / qty step)
SL_DOLLAR_FRAC = 2.0    # trip when unrealized loss >= this many multiples of posted margin
TP2_PCT = 0.80          # take-profit at 80% of premium decayed — used only for the rare
                        # orphaned-leg fallback now; paired positions use QUICK_TP_COMBINED_USD
CYCLE_H = 24.0          # hours per cycle (matches Bybit's shortest BTC option tenor)

# Quick-scalp combined exit (2026-06-26 rewrite, see straddle_quick_scalp_backtest.py):
# close BOTH legs of a pair together once their COMBINED unrealized profit hits
# this many dollars, then immediately reopen a fresh pair — instead of riding
# one pair per 24h to TP2/SL/time-stop.
#
# Raised $2 -> $22 on 2026-07-03 (see btc_straddle_quickscalp_account_sim.py /
# btc_straddle_param_search.py): the ORIGINAL $2 selection (straddle_quick_
# scalp_backtest.py) never modeled fees or account-level margin sizing. Once
# both were added, $2 reopens ~8-12x/day and fee drag alone (~0.03% notional
# per leg per open+close) turned the live paper account net-negative over
# 60d/120d/365d windows despite gross edge being real. Sweeping target_usd on
# the real $-account engine (start=$2000, MARGIN_PCT=0.15, real fees, no CB —
# matches btc_straddle_loop.py exactly) found a clean peak at $22 (~1.1
# reentries/day): $2.65-5.37/day across 60d/120d/365d windows vs -$0.88 to
# -$1.34/day at the old $2, confirmed out-of-sample (120d/365d windows were
# not part of the 60d tuning window). sl_dollar_frac was swept too and found
# NOT to help — tightening it made both profit and maxDD worse, so it's
# unchanged. See finding_boba1_quickscalp_target_fee_fix memory for the full
# writeup.
QUICK_TP_COMBINED_USD = 22.0


def margin_per_lot(strike: float, entry_premium: float, *,
                   im_rate: float = IM_RATE, lot: float = LOT_BTC) -> float:
    """Margin (USDT) posted for ONE lot at entry. Constant for the position's life."""
    if strike <= 0 or entry_premium < 0 or lot <= 0:
        return 0.0
    return (im_rate * strike + entry_premium) * lot


def sl_dollar_trip(margin_per_lot_usd: float, *, sl_dollar_frac: float = SL_DOLLAR_FRAC) -> float:
    """Dollar loss tripwire for ONE lot — constant for the position's life."""
    return sl_dollar_frac * margin_per_lot_usd


def is_tripped(*, entry_credit: float, current_buyback_ask: float, qty: float,
              sl_trip_per_lot_usd: float, lot: float = LOT_BTC) -> bool:
    """True when the unrealized loss on the short leg has reached the dollar stop.

    ``qty`` is the position's total contracts (multiple of ``lot``); the trip
    scales linearly with however many lots are actually open.
    """
    if lot <= 0 or qty <= 0:
        return False
    unrealized_loss = (current_buyback_ask - entry_credit) * qty
    trip = sl_trip_per_lot_usd * (qty / lot)
    return unrealized_loss >= trip

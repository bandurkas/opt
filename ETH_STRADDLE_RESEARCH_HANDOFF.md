# ETH unconditional 24h straddle — research handoff

## Question to answer

The BTC bot runs a **mechanical, unconditional** 24h short straddle (every cycle:
SELL Call + SELL Put, no signal/regime filter, dollar-margin SL — see
`BTC_STRADDLE_HANDOFF.md` and live `btc_straddle_loop.py`). The current ETH bot
is a **signal-based** hybrid (`strategy_config.py`: only sells Call on downtrend
signal, Put on uptrend signal, with `vol_threshold`/`regime_filter`/`cooldown_bars`).

User's question: why not test whether BTC's mechanical two-leg approach also
works on ETH? Not a replacement for the current ETH bot — a **separate,
parallel research question**: does ETH support the same unconditional-straddle
edge BTC does?

## What's already confirmed (no new code needed to start)

The BTC backtest harnesses are **already coin-parametrized**, not BTC-hardcoded:

- `services/multi_coin_signals.py:46` — `COINS = ["eth", "btc", "sol", "xrp", "xaut"]`,
  `load_coin(prefix, data_dir)` already supports `"eth"`.
- `services/btc_straddle_dollar_stop.py` — `COIN = sys.argv[1] or "btc_long"`,
  calls `load_coin(COIN, ...)` via `build_cycles()`. Just run with `eth` as arg 1.
- `services/btc_straddle_dollar_account_sim.py` — same pattern, `sys.argv[1]`.
- `services/btc_straddle_gap_stress.py` — same pattern (crash stress test, the
  one that found the original %-of-premium SL broke near expiry — re-run this
  for ETH to see if the same gap-risk failure mode applies there).

So the very first step is **just re-running these with `eth` instead of
`btc_long`/`btc`** — this alone tells us whether the mechanical-straddle edge
transfers, before writing a single line of new code.

```bash
cd /Users/sabar/Desktop/options/backend
python3 services/btc_straddle_dollar_stop.py eth 1095
python3 services/btc_straddle_gap_stress.py eth 1095
python3 services/btc_straddle_dollar_account_sim.py eth 1095
```

Watch for: does `load_coin("eth", ...)` actually have a `btc_straddle`-style
periodic-signal builder working correctly (`build_periodic_signals` in
`btc_straddle_sweep.py` — confirm it isn't accidentally BTC-specific, e.g.
strike rounding or sigma clamp tuned for BTC's price scale). ETH-specific
parameter to watch: `STRIKE_ROUND=500` in `btc_straddle_dollar_stop.py` is a
BTC price-scale constant — for ETH this should be `25` (matches `CALL_24`/
`PUT_96`'s existing strike rounding in `iv_mixed_deposit.py`/`paper_loop.py`).
Likely needs a coin-conditional override before numbers mean anything.

## Locked BTC params, to mirror exactly as the starting point (not yet tuned for ETH)

```
CYCLE_H=24   TP2_PCT=0.80   SL_DOLLAR_FRAC=2.0   IV_RV_MULT(MULT)=1.10
SIGMA_CLAMP=(0.20,1.50)   SPREAD_PCT=2.0   IM_RATE=0.10
STRIKE_ROUND=500 (BTC) → use 25 for ETH (see above)
LOT_BTC=0.01 → LOT_ETH=0.10 (services/live_sizing.py:26, already the live ETH lot)
```

These are starting values to re-sweep for ETH, not assumed-correct — BTC's
`SL_DOLLAR_FRAC=2.0` was itself the output of a sweep (`btc_straddle_sweep.py`),
not a given. Expect ETH to need its own sweep since ETH's vol/premium scale
relative to strike differs from BTC's.

## How to evaluate (same bar as every other change in this project)

Per `feedback_options_workflow_order` (architecture → code → review → test →
review → deploy) and the methodology used for the recent ETH Call dollar-SL
work (see memory `finding_eth_dollar_sl_mixed.md`):

1. Raw %-PnL / per-leg backtest first (`btc_straddle_dollar_stop.py` style).
2. Crash/gap stress test (`btc_straddle_gap_stress.py` — this is what killed
   BTC's original %-premium SL; confirm ETH's dollar-SL survives ETH's own
   crash periods, e.g. the known 2025-07/2025-08 losing months already seen
   in the current ETH Call bot).
3. $-account-level engine with REAL constraints (margin, MAX_OPEN, compounding,
   circuit breaker) — `btc_straddle_dollar_account_sim.py` style — train/holdout
   split, monthly breakdown to rule out single-period mirages.
4. Compare straddle-on-ETH's holdout $ outcome against the CURRENT ETH bot's
   live numbers (not just against itself) — the bar to adopt anything is
   beating the existing signal-based hybrid, not just "being profitable".
5. Only if it clears that bar: plan a parallel paper-bot (mirrors how the BTC
   straddle was built as its own loop/tables alongside the ETH bot, not a
   replacement) — same "build live-ready, flip env var later" pattern.

## Explicitly NOT in scope for this research pass

- Touching/replacing the current ETH signal-based bot's code or config.
- Implementing or deploying anything — this handoff is for the
  research/backtest phase only. Decide whether to proceed to implementation
  AFTER the numbers come back, per the project's "measure before build" rule
  (see memory `finding_carry_rotation_rejected` for why that rule exists —
  same project owner, same discipline applies here).

## Where to pick this up

Start a new session, read this file, then re-run the 3 commands above with
`eth` and report what comes back before writing any new code.

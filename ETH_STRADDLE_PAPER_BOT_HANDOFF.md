# ETH unconditional straddle — paper bot implementation handoff

## Context / why

Research phase (`ETH_STRADDLE_RESEARCH_HANDOFF.md`) tested whether BTC's
mechanical, unconditional 24h short-straddle (no signal/regime filter — sell
ATM Call + ATM Put every cycle, dollar-margin SL) also works on ETH. Verdict:
**comparable to, not clearly superior to, the current signal-based ETH bot**
on pure risk-adjusted holdout return (see memory
`finding_eth_unconditional_straddle_comparable.md`).

BUT: the current signal-based ETH bot is idle ~77% of days (live MIXED-24
config: active only 80/354 days, max silent streak 66 days, median gap 3d,
p90 gap 8d — measured via `iv_mixed_deposit.py`'s active-days analysis). The
straddle is mechanical/clock-driven and trades ~358/365 days regardless of
the signal bot's state. User's call: don't trust the backtest comparison
alone — **build it as a separate paper bot and let it prove itself live,
without touching the existing live signal bot.**

## Architecture decision: full financial isolation, not idle-detection

Do **not** make the straddle conditional on "signal bot has no open
position." That would require it to read the signal bot's live state in
real time — coupling that risks bugs in the *existing* live bot's critical
path for no real benefit.

Instead: **separate paper wallet** — own DB tables, own equity counter, own
circuit breaker, own margin ledger. Runs unconditionally every 24h
regardless of what the signal bot is doing. Because the signal bot is idle
most of the time anyway, the time-overlap with its silent stretches happens
automatically, with zero coordination code and zero chance of resource
contention in paper mode (different Postgres tables, no shared counters).
This exactly mirrors how `btc_straddle_loop.py` already runs as a separate
book from the ETH V3 signal bot, in the same Postgres instance.

**Going live later (NOT in scope for this paper build):** user has decided
to use a **second, separate Bybit account** for the ETH straddle when/if it
goes live, specifically to sidestep a real bug found in `reconcile.py`:
`exchange_position_sizes(client, base_coin="ETH")` queries *all* ETH option
positions on the account and diffs them against only the calling bot's own
DB table — running two live ETH strategies on the *same* Bybit account would
have each one flag the other's positions as "untracked" and **block its own
new opens** (`reconcile.py:103-130`, `_blocked = bool(untracked)`). Two
accounts avoids needing to make `reconcile.py` strategy-aware. Don't forget
this when arming live — it's not yet fixed in code, just avoided by account
separation.

## Locked parameters (from research, ETH-specific)

```
CYCLE_H            = 24       # re-swept directly on ETH (12/24/48/72/168 x TP2 grid);
                               # BTC's locked value held up — no robust ETH-specific
                               # winner found (the formal "best", 72h/tp2=0.50, had only
                               # 36 holdout cycles and its Sharpe collapsed 0.21->0.03 —
                               # overfit noise, don't use it)
TP2_PCT            = 0.80     # same reasoning — held up against re-sweep
SL_DOLLAR_FRAC      = 0.3      # ETH's OWN leg-Sharpe optimum — do NOT reuse BTC's
                               # locked 2.0, it's meaningfully worse for ETH (account-
                               # sim totRet +47.5%->+64.5%, maxDD 25.4%->12.0% at $2000
                               # going from frac=2.0 to frac=0.3)
IV_RV_MULT (MULT)   = 1.10
SIGMA_CLAMP         = (0.20, 1.50)
SPREAD_PCT          = 2.0
IM_RATE             = 0.10
STRIKE_ROUND        = 25      # NOT 500 — that's BTC's price-scale constant. Using 500
                               # for ETH silently produces garbage strikes (~15-20% of
                               # spot instead of ATM). This bug existed in the BTC
                               # harnesses until fixed during research (see below).
LOT                 = 0.10    # ETH lot, matches services/live_sizing.py's live ETH lot
MARGIN_PCT_PER_CYCLE = 0.15   # default; same knob as BTC_STRADDLE_MARGIN_PCT
```

**Deposit**: start at **$1200** (knee of the deposit curve for frac=0.3 — ROI%
roughly flattens above this; below it, lot-size granularity drags ROI down).

**Holdout-only sanity numbers to expect** (pure OOS, ~110 most-recent days,
$1200, MARGIN_PCT=0.15): totRet +17.9%, maxDD 7.8%, monthly-compounded
≈+5.04%/mo. Full ~365d period: totRet +51.0%, maxDD 12.1%, ≈+3.57%/mo. If the
live paper numbers come in wildly different from this, something in the
live execution (real spreads/fees/fills) diverges from the backtest
assumptions — investigate before scaling.

## Bugs already fixed in the BTC research harnesses (carry these fixes forward)

Both already patched in
`backend/services/btc_straddle_dollar_stop.py` and
`backend/services/btc_straddle_gap_stress.py` — **the live loop script must
use the same fixed logic, not the original BTC-only constants**:

1. `STRIKE_ROUND`/`LOT` were hardcoded to BTC's price scale (500 / 0.01).
   Now coin-conditional: `STRIKE_ROUND_BY_COIN = {"btc": 500.0, "btc_long":
   500.0, "eth": 25.0, "xaut": 25.0}`, `LOT_BY_COIN = {"btc": 0.01,
   "btc_long": 0.01, "eth": 0.10}`.
2. `btc_straddle_dollar_stop.py`'s sweep picked its "best" `SL_DOLLAR_FRAC`
   by Sharpe over the FULL pooled period (peeking at holdout) — fixed to
   select on TRAIN-only Sharpe, matching `btc_straddle_sweep.py`'s own
   stated discipline. (Conclusion, frac=0.3, was unchanged by the fix — not
   a prior false positive, but don't reintroduce the look-ahead bug when
   porting logic into the live loop.)

A separate, NOT-yet-fixed naming gotcha to watch for if reusing any research
script: `load_coin()` in `multi_coin_signals.py` returns `(k5, k15, k1h)` in
that order. It's easy to mis-unpack as `k5, k1h, _ = load_coin(...)` (looks
plausible, silently grabs 15m data into a variable called `k1h`) — this
produced a sign-flipped, nonsense backtest during this research (BTC's own
`build_cycles()` unpacks it correctly as `k5, k15, k1h = load_coin(...)`).
Triple-check unpacking order in any new code that calls `load_coin`.

## Files to create (mirror the BTC straddle pattern exactly)

The BTC paper bot (`btc_straddle_loop.py`) is the reference implementation —
same script handles paper AND live, gated on `broker.is_live()`. Copy this
pattern, do not redesign it.

| New file | Mirrors | Key changes |
|---|---|---|
| `backend/services/eth_straddle_loop.py` | `services/btc_straddle_loop.py` | `SPOT_SYMBOL="ETHUSDT"`, `BASE_COIN="ETH"`, `STRIKE_ROUND=25.0`, `CYCLE_MS`/params per above, env vars prefixed `ETH_STRADDLE_*` |
| `backend/db/eth_straddle_repo.py` | `db/btc_straddle_repo.py` | same function surface (singleton state + positions + equity snapshots), table names `eth_straddle_*` |
| schema addition in `backend/db/schema.sql` | the `btc_straddle_positions` / `btc_straddle_equity_snapshots` / `btc_straddle_state` block (lines ~139-187) | same columns, `eth_straddle_*` table names |
| docker-compose service `eth_straddle_paper` | `btc_paper` service in `docker-compose.yml` | `command: python services/eth_straddle_loop.py`; **depends_on `poller`, NOT `btc_poller`** — ETH klines are already fed by the existing default poller, no new poller container needed (this is simpler than the BTC case, which needed its own poller) |
| API routes `/api/v1/eth-straddle/{state,positions,equity_history}` | `main.py:349-410` (`/api/v1/btc-straddle/*`) | same handler shape, calls `eth_straddle_repo` instead |
| frontend dashboard section | `frontend/app/page.tsx` + `frontend/app/lib/api.ts` BTC-straddle section | new card/section for ETH straddle, third section alongside the existing ETH signal book and BTC straddle book |

## What must NOT change

`paper_loop.py`, `paper_*` tables, the existing ETH signal bot's API routes
and dashboard section — zero modifications. The whole point of the
isolation design is that this is purely additive.

## Build order (per this project's standing workflow discipline)

architecture (this doc) → code → review → test (paper, watch real
cycles accumulate) → review → deploy. Code review is mandatory before
declaring this done — don't skip straight to "looks right, ship it."

## Open question for later (explicitly deferred, not blocking the paper build)

`reconcile.py`'s coin-keyed (not strategy-keyed) untracked-position check —
only matters once *live* trading is being considered, and only if both ETH
strategies were to share one Bybit account, which they won't (separate
accounts decided). No code change needed for the paper bot itself.

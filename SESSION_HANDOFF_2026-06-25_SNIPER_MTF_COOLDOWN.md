# Session handoff — 2026-06-25 — Sniper1 MTF anchor, cooldown-alignment bug, cross-bot vol research

## Sync state (verified at end of session)
- **local / GitHub / VPS3 all on commit `4fe604d0`**, fast-forward, clean.
- VPS3 (`root@187.127.114.34:/root/opt-app`) containers `backend` + `paper`
  rebuilt + force-recreated 3x this session (one per deploy below); SHA256
  of every changed file verified identical across local/backend/paper
  containers after each deploy. `frontend`/other bots untouched.
- Untracked stray files (local `backend/.venv311/`, `backend/eth_sl_deep_analysis.py`;
  VPS3 `.paper_monitor_state`, `fetch_eth_1m.py`, `paper_cron.err`) are
  pre-existing, not from this session — leave them alone.
- New script (not deployed, dev-only, lives in repo): `backend/services/
  sniper_cooldown_portfolio_sweep.py` — the $-account cooldown sweep tool,
  reusable for future sweeps.

## What shipped this session (3 deploys, all live and validated)

### 1. CALL-only 1h MTF anchor (commit `69b27e17`)
User wanted Sniper1's MTF entry gate loosened slightly so close-but-not-quite
trades fire — but ONLY if backtested first. Tested loosening the alignment
COUNT (1/3 vs current 2/3) — rejected, more trades but lower quality, flat on
holdout. What worked: for **CALL only**, let the 1h timeframe's own direction
decide instead of the 3-way 5m/15m/1h consensus
(`CALL_GEN_KWARGS["mtf_anchor_tf"]="1h"` in `strategy_config.py`). Same lever
on PUT degrades train avg to -8.98% — **PUT unchanged, do not copy**.

New shared helper `direction_filter_ok()` in `momentum_mtf.py`, used by BOTH
the real generator (`strategy_registry.gen_sell_premium_iv_high`) and the
gauge (`paper_strategy.evaluate_conditions`) so they can't diverge again.
`evaluate_conditions` was also restructured to check each allowed side
independently in range zone (previously picked one side from a single
shared consensus before checking per-side gates — couldn't represent CALL's
anchor-based eligibility when it diverged from the 3-way consensus).

Full 388-day backtest (`sniper_persistence_backtest.py`, run through actual
production code): live debounce rule n 1513→1647, holdout avg
+9.02%→+9.83%. Improves train AND holdout together.

### 2. Gauge close-tick gate (commit `1f8b0c91`)
User caught live: gauge showed 100%/"Вход" as early as minute 1 of the
5-minute debounce window — before 3-4 more per-minute checks that could
still disqualify it. `entry_proximity()` (paper_strategy.py) now also
requires `window_status.min_in_window == SIGNAL_CHECK_EVERY_MIN - 1` (the
same minute paper_loop's `fire_now` actually checks) before pinning to 100.
13 tests in `test_proximity.py` (was 11).

### 3. Cooldown-alignment bug — the big one (commit `4fe604d0`)
Found while live-verifying #2: gauge showed ready=True/100%/non-disqualified
repeatedly for ~20 hours with ZERO trades opening. Root cause:
`check_new_signal()` (paper_loop.py) only checks the last 2 ARRAY POSITIONS
of its `k5` input (`idx_5m in {last-1, last}`) — but `k5` is always the
latest sliding `window_5m=2100` bars, so "the last position" is always
array index 2099, a POSITION, not a calendar identity. The generator's own
`cooldown_bars=6` walk schedules fires every 6 CALENDAR bars from wherever
a qualifying run began (deterministic in calendar time) — but the live poll
only catches a scheduled fire when it happens to land in that 2-position
slice, ~2 of every 6 ticks (~33%), silently missing the rest even with
conditions fully met. Confirmed empirically on VPS3 (nearest real candidate
was 3 bars behind "now" while gauge read ready=True).

**Fix**: call the generator with `cooldown_bars=0` (report every
gate-passing bar instead of the generator's own blind-walk subset), keep
the tight 2-bar freshness check, enforce the REAL cooldown using `ts_ms`
(calendar-stable on every signal) against `state.last_signal_ts_ms` — new
column, idempotent migration via existing `apply_schema()`. Re-keyed
`is_new_signal()` from `idx_5m` to `ts_ms` for the same reason.

**Live-validated within minutes of deploy**: the next confirmed-ready
close-tick window actually opened position #6 (short Call 1625, credit
$23.92, margin $112.29) — first real proof, not just backtest/unit tests.
Position later hit TP1 (`#6 TP1 @ mid $12.53`), confirming exit/monitoring
(untouched this session) still works end-to-end.

8 new/updated tests: `test_signal_alignment.py` (3, new — monkeypatches
`gen_sell_premium_iv_high` with a fake to deterministically prove the fix
fires every `cooldown_bars` ticks under continuous qualification, and that
cooldown uses real elapsed `ts_ms` not tick count), `test_signal_dedup.py`
(re-keyed to ts_ms). Full suite = 40 tests, verified green 2x after every
change, both locally (`options-backend-1` Mac container) and on VPS3
(`opt-app-backend-1`).

**Known accepted gap, not fixed**: on a cold/fresh deploy,
`last_signal_ts_ms` starts NULL, so the cooldown check is skipped for the
FIRST signal check after restart (could theoretically double-fire if a real
trade had JUST opened seconds before a restart). Low-probability, same
category of risk `last_signal_idx_5m` already had when introduced
2026-06-23 without backfill. Not worth a backfill given the narrow window;
mention if revisited.

Process used for all 3: architecture→code→review (self, no subagents,
diffs were small enough)→test (2x full suite)→review→deploy→SHA256 verify
→live behavior verify. Memory:
`~/.claude/projects/-Users-sabar/memory/project_sniper1_mtf_anchor_and_cooldown_fix.md`.

## Tested and explicitly REJECTED/inconclusive (do not redo without new angle)

### Cooldown_bars sweep — shortening rejected, lengthening inconclusive
User asked: "what about 15 min instead of 30?" Built
`services/sniper_cooldown_portfolio_sweep.py` — reuses `deposit_sim.py`'s
REAL $-account engine (margin-based sizing, MAX_OPEN_POSITIONS=4, 80% port
margin cap, fees, dyn-size, circuit breaker, dynamic-sigma pricing), fed by
cooldown-varying event replay on the cached real `evaluate_conditions`
reconstruction. (First attempt naively reused `tail_overlay_sweep.py`'s
`RISK_FRAC=0.10`-of-equity sizing — produced nonsense -99% maxDD for EVERY
candidate including live, because that sizing convention was calibrated for
a different signal set's pnl_pct scale (-200%..+80% range here vs whatever
`variant_backtest.generate('v3')` produces). Don't reuse RISK_FRAC sizing
for this strategy again — use the margin-based engine.)

- **Shortening (15/20/25 min): REJECTED**, confirmed at both $400 and $800
  starting capital — holdout return and maxDD both worse than live 30min
  across the board. Validates the original concentration-risk concern.
- **Lengthening (40/50/60 min): INCONCLUSIVE.** A single 70/30 holdout split
  made 50min look clearly better (+7.8% vs live's -9.6%, lower maxDD) — but
  a 4-quarter walk-forward (fresh equity each quarter, isolating
  period-by-period edge from lucky compounding) showed live (30min)
  actually WINS Q2 (+14.6% vs +12.1%) and Q3 (+25.3% vs +16.5%) by a wide
  margin; 50min only wins Q1/Q4. Summed across quarters the two are within
  noise of each other (~25% vs ~27%) — classic overfit-to-recent-data, same
  lesson as `feedback_check_backtest_population_before_deploy.md`.

**Decision: cooldown_bars=6 (30min) stays unchanged on both sides.** Memory:
`finding_sniper1_cooldown_sweep_inconclusive.md`.

## In-progress research — NOT finished, this is the "big work" to continue

### Cross-bot vol-spillover hypothesis (Sniper1 → Grogu1/Boba1)
User's observation: when Sniper1 is about to fire (or fires), a sharp
directional move tends to follow — which is bad for Grogu1 (ETH straddle)
and Boba1 (BTC straddle), short-premium sellers that profit from QUIET
markets and lose to gamma risk on big moves. Hypothesis: pause/close
Grogu1+Boba1 around Sniper1-ready moments.

**Mechanism check (done, confirms the hypothesis directionally):**
Using the cached `evaluate_conditions` reconstruction (8834 "ready" close-
tick moments, 1647 actual cooldown-deduped fires) and real ETH 5m closes,
measured forward max-min price range vs a baseline sample:

| horizon | baseline avg range% | after Sniper "ready" | after Sniper "fired" |
|---|---|---|---|
| 1h  | 0.727% | 0.825% (+13.5%) | 0.837% (+15.1%) |
| 4h  | 1.665% | 1.888% (+13.4%) | 1.897% (+13.9%) |
| 8h  | 2.491% | 2.731% (+9.6%)  | 2.744% (+10.2%) |

**Cycle-level PnL check (done, strong signal):** built ETH/BTC straddle
cycles via `btc_straddle_account_sim.build_cycle_trades(coin, days_back)`
(reused as-is for BTC=Boba1's actual live params; used approximated/generic
24h-cycle params for ETH=Grogu1 — NOT Grogu1's exact live SL mechanics,
which use a dollar-margin SL (`eth_straddle_sl.py`, `SL_DOLLAR_FRAC=0.15`,
`TP2_PCT=0.90`) rather than `build_cycle_trades`'s hardcoded %-of-premium
TP1=0.50/TP2=0.80/SL=0.75 — **this is an approximation gap to fix before
trusting exact numbers**, see below). Flagged any cycle containing a
Sniper-ready timestamp during its open window:

| bot | WITH Sniper-ready in cycle | WITHOUT |
|---|---|---|
| Grogu1 (ETH) | n=98, avg **+11.25%** | n=259, avg **+29.73%** (2.6x better) |
| Boba1 (BTC)  | n=81, avg **+19.57%** | n=284, avg **+25.52%** |

**Critical caveat, NOT yet addressed — this is the actual next-session
work:** correlation ≠ "intervention helps." Two prior, directly analogous
investigations on this same straddle bot found that intervening on a
correlated-risk signal made things WORSE, not better:
- `finding_eth_remaining_leg_management_rejected.md` — tighter SL/time-stop/
  perp-hedge on a "lagging" leg all underperform doing nothing.
- `finding_eth_event_driven_reopen_rejected.md` — reopening immediately
  instead of waiting for the midnight-aligned grid is WORSE; the gap is an
  accidental vol-clustering cooldown, not wasted time.

Both rejections share the same shape as this new hypothesis: a bad-looking
window doesn't mean ACTING on it (closing early / skipping the next open)
beats just riding it out — the position might recover, or the act of
closing+reopening costs more (spread, missed decay, whipsaw) than it saves.

**Next session must build, before recommending ANY pause/close logic:**
1. Fix the ETH cycle-building approximation — use Grogu1's actual exit
   params (`eth_straddle_sl.py`'s dollar-margin SL=0.15, TP2=0.90) instead
   of `build_cycle_trades`'s hardcoded BTC-style %-premium exits, OR find/
   build an ETH-specific equivalent of `build_cycle_trades`.
2. **Simulate the actual proposed intervention**, not just the correlation:
   e.g. "skip opening the next Grogu1/Boba1 cycle if Sniper1 fired/was
   ready in the last N hours" vs baseline "always open on schedule" — same
   $-account replay discipline as everywhere else this session (train+
   holdout, walk-forward by quarter given today's overfit lesson on the
   cooldown sweep).
3. Decide the exact trigger definition: "Sniper ready" (gauge-visible,
   8834 events, very frequent — would pause straddles very often, maybe
   too often to be practical) vs "Sniper actually fired" (1647 events,
   rarer, stronger signal) — test both, they likely give very different
   practical answers (pausing on EVERY "ready" moment might leave the
   straddle bots idle most of the time).
4. Check whether BTC (Boba1) really warrants pausing on an ETH-only
   signal, or whether that correlation is mostly incidental BTC/ETH
   co-movement — might want a BTC-specific leading indicator instead/also.

No code/deploy changes were made for this research — discovery only.
Memory note to write next session once this is resolved (none exists yet
for the cross-bot hypothesis itself, only the building blocks referenced
above already exist).

## How to resume
- Repo: `bandurkas/opt`, local `~/Desktop/options`, VPS3 `/root/opt-app`
  (`root@187.127.114.34`, no SSH alias — use IP directly).
- Workflow convention: architecture → code → review → test → review →
  deploy. Review mandatory before deploy. Full-period (388d local OHLCV)
  backtest + walk-forward (not just one holdout split) before trusting any
  "this is better" result — today's cooldown sweep is the cautionary tale.
- VPS3 is 1 CPU — scope `docker compose build` to 1-2 services. `paper`
  shares `backend`'s build context but is a **separate image** — must
  `docker compose build paper` explicitly too, easy to forget (bit us
  again this session, caught immediately by hash verification).
- Always verify deploys with SHA256 hash comparison (local file vs
  `docker exec <container> sha256sum <path>`) for every changed file in
  every affected container — this caught the `paper` image staleness
  immediately both times it happened.

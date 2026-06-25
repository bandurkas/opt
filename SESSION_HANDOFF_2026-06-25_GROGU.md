# Grogu1 (ETH straddle) — session handoff 2026-06-25

## Starting point

User noticed live paper trading showing huge SL losses ($25-40/leg) vs tiny
TP2 wins ($5-7/leg) — looked like ~1:4-8 ratio, "a few good days erased by
one SL." Investigated and worked through the whole exit-mechanics and
structural-risk space. Bot: `eth_straddle_loop.py` on VPS3
(`opt-app-eth_straddle_paper-1`), $1200 paper capital, TRADING_MODE=paper.

## What got deployed (in order)

1. **`SL_DOLLAR_FRAC` 0.3 → 0.15** (commit `826872a0`/`48bd5894`). The old
   frac sized the dollar-stop at ~$23/leg vs TP2's ~$5-6/leg by design —
   already a ~4:1 ratio before any execution overshoot. 0.15 is the honest
   unconditional-population optimum (`eth_straddle_sl_resweep.py`, NOT the
   filter-bundled 0.35 — see below).
2. **`TP2_PCT` 0.80 → 0.90** (commit `53896509`/`16d68caf`). Swept TP2 x FRAC
   on the full 381-cycle/762-leg history: 0.90 beats 0.80 at every FRAC
   tested, train AND holdout, no exceptions.

Both changes: code-reviewed, unit-tested, deployed via local Mac build →
VPS3 git tree → `docker compose build` on VPS3 (NOT Mac — VPS3 build/deploy
is fine, only heavy multi-service builds were the original resource
concern) → container recreate. Verified live via startup log
(`tp2=0.9 sl_frac=0.15`) and confirmed 0 restarts after redeploy each time.

## Key reframing finding

The live 1:4-8 ratio was **small-sample noise from 2 unusually bad SL
draws**, not the true long-run shape. Full 381-cycle history at FRAC=0.15
shows the real ratio is **~$3.17 avg TP2 win vs ~$5.71 avg SL loss (≈1:1.8)**,
net **+$337/762 legs**. The strategy was already net-positive before any
fix — the live sample just got unlucky. **Always check the full-history
backtest before trusting a small live sample's ratio.**

## What got tested and REJECTED (don't revisit without new angle)

All of these were measured on the same 381-cycle full ETH history
(`eth_straddle_sl_resweep.py`'s `build_cycles_full`), train/holdout-checked
where it mattered:

- **Iron butterfly (buy OTM wings to cap SL tail)** — every wing width
  $25-500 underperforms the no-wing baseline on REAL Bybit spread costs
  (measured from `option_snapshots`: ATM spread ~2%, rising to 10-28% by
  3-8% OTM). Same mechanism that killed gold's iron butterfly.
- **OTM strangle (sell away from ATM)** — win-rate rises but avg win shrinks
  faster than avg loss; net$ collapses monotonically as offset widens.
- **Vol-aware position sizing** (size down on high trailing-sigma entries)
  — every config tested is worse than flat sizing; trailing realized vol at
  entry does NOT predict which cycle will be the bad one (worst cycle's $
  loss is unchanged or worse under scaling, not reduced).
- **Event-driven cycle reopen** (open the next cycle immediately when both
  legs close, instead of waiting for the next UTC-midnight boundary) — user
  spotted that `current_cycle_id() = now_ms // CYCLE_MS` accidentally
  aligns to midnight, causing idle gaps of up to ~16h after early closes.
  Tested removing the gap: WORSE net$ (-$37 vs +$378/386 days) despite 47%
  more cycles. SL-hit rate rises 32.3%→35.0% — re-entering right after a
  cycle's volatile resolution (esp. an SL) walks back into still-elevated
  vol (clustering). **The midnight gap is an accidental but real cooldown,
  not wasted time.** Don't touch `current_cycle_id()`.
- **Reactive management of a lagging leg** (one leg closed via TP2, the
  other still open and underwater — tested tightening its SL, shortening
  its time-stop, and a perp delta-hedge once unrealized loss crosses 50% of
  the SL trip). Built a synchronized bar-by-bar dual-leg simulator for
  this. Every intervention underperforms doing nothing; perp-hedge is worst
  by far (holdout edge ~$0.01/cycle vs baseline's ~$0.75/cycle) — hedging
  neutralizes the position right as a reversion would have paid off, and
  pays fees both ways for it. Loosening the SL (1.5x/2x) is roughly
  net-neutral to slightly positive but with a worse tail — not a clear win
  either way.

**The common thread:** this strategy's tail risk doesn't have an exploitable
structural fix at reasonable cost — most adverse moves that look like
they're heading for a bad SL would have reverted (~55-60% whipsaw rate
across the whole FRAC grid), and every reactive/structural attempt to cut
losses early or hedge them away has cut off more recoveries than it
prevented disasters. The exit-parameter axis (FRAC=0.15, TP2=0.90) is near
its ceiling; the strategy's edge is thin (Sharpe ~0.2) but real.

## Deferred, not done (pick up later, not urgent)

- **Execution-guard for quote spikes**: `current_mark()` in
  `eth_straddle_loop.py` takes raw `ask` with no spread sanity check.
  Backtested a `jump_detector` variant (reject a tick if ask moves >50% vs
  last accepted ask) against real Bybit quotes — same protection as a
  static 15% spread cap, but only skips 0-6% of ticks vs 5-35% (spread is
  routinely wide on these contracts, not rare). Confirmed a real outlier
  print exists in the data (~7x fair value). BUT: neither guard would have
  fixed the actual two live SL incidents (-$24.58, -$39.64) — their trip
  ticks had clean spreads in the 30s-cadence data, so the real overshoot
  was likely a sub-30s transient. Low priority, real but modest expected
  value. Harness was in scratchpad, not saved to repo — rebuild from
  `finding_eth_ironfly... ` — actually see memory note
  `project_grogu_execution_guard_deferred.md` for the exact numbers if
  picking this up.
- **Shadow IV Rank + VRP entry filter**: still shadow-only
  (`SHADOW_FILTER_LIVE=false`). IV history collector only ~8 days deep vs
  the 720h/30d window it needs — don't flip live until ~early Aug 2026 (or
  sooner if Grogu1 turns net-negative live, per the original agreement).

## Where things stand

Grogu1 live on VPS3, FRAC=0.15, TP2=0.90, paper mode, $1200 capital, no
real money. This is believed to be at or near the practical ceiling for
this exit-mechanics+structure combination on the backtest evidence
gathered this session. Memory files (all under
`~/.claude/projects/-Users-sabar/memory/`, prefixed `finding_eth_*` /
`project_grogu_*`) have the full numeric detail behind every bullet above
if more precision is needed than this summary gives.

## Next session: pivot to Sniper1

User wants to move on to investigating what's left outstanding for
Sniper1 (the ETH signal/paper-loop bot, `paper_loop.py`, separate from
Grogu1). Known open threads going in:
- Sniper1's entry persistence-window TIMING (fire on first-ready-minute vs
  waiting for the full 5-minute window) was flagged earlier this session as
  untested and distinct from the already-rejected vol_threshold/richness
  levers (`finding_options_entry_frequency_rejected.md`) — worth checking
  if still relevant.
- Check current Sniper1 live state (equity, recent signals, any open
  positions) before resuming research — state may have moved since last
  checked.

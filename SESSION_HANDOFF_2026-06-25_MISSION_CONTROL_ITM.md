# Session handoff — 2026-06-25 — Mission Control ITM/OTM + countdown

## Sync state (verified at end of session)
- **local / GitHub / VPS3 all on commit `f87f469f`**, fast-forward, clean.
- VPS3 (`root@187.127.114.34:/root/opt-app`) containers: `backend`, `frontend`,
  `paper` rebuilt+restarted this session; all others untouched and healthy.
- Untracked stray files on VPS3 (`fetch_eth_1m.py`, `.paper_monitor_state`,
  `paper_cron.err`) and locally (`backend/.venv311/`, `backend/eth_sl_deep_analysis.py`)
  are pre-existing, not part of this work — leave them alone.

## What shipped this session

### 1. Sniper1 gauge/debounce sync (commit `8bb024fe`, prior session, recapped)
Dashboard "speedometer" now requires a confirmed (fresh + same 5m-window,
non-disqualified) debounce status to show 100%/"entry" — previously could
show 100% without the bot actually being about to fire. See
`~/.claude/projects/-Users-sabar/memory/project_sniper1_gauge_debounce_sync.md`.

### 2. Mission Control: ITM/OTM status + live expiry countdown (commits `f97852e2`, `f87f469f`)
New feature, built via `/frontend-design` skill, HUD-consistent with existing
MissionControl/StraddleChart styling:
- **Global "Active Contracts" rail** under the header — every open option
  position across all 3 bots (Sniper1/Boba1/Grogu1), sorted soonest-to-expire,
  click a chip → slide-in drawer with full detail (big countdown, ITM/OTM
  distance, entry credit, open time).
- **Inline** ITM/OTM badge + live (1s-ticking) countdown added to each bot's
  existing open-positions list.
- New file `frontend/app/components/ActiveContracts.tsx`. New backend route
  `GET /api/v1/market/btc-price` (mirrors `/market/eth-price`) — BTC straddle
  had no live-spot source before this.
- **Domain note**: these bots are short-premium SELLERS, so OTM=safe(green),
  ITM=risk(red) — inverted vs. a typical long-option UI. Already correct in
  the shipped code, don't "fix" it later by mistake.

Full multi-angle code review (8 finder angles) ran before deploy and caught
a real bug pre-merge: `bybit_client.get_spot_price()` returns `0.0` (not an
error) on a Bybit outage, which would have flashed every short PUT as falsely
"ITM" during any API hiccup. Fixed by treating `spot <= 0` as "no data" in
`itmInfo()`. Also fixed: a duplicate independent 1s timer, an unmemoized sort
re-running every tick, and a hand-duplicated bot-color map (now sourced from
`MissionControl.tsx`'s exported `BOT_META`).

### 3. Post-deploy incident — React error #310, found and fixed same day
User got "This page couldn't load" right after the first deploy. Root cause:
the new `allContracts` `useMemo` in `Dashboard` (`page.tsx`) was placed
**after** the existing `if (!state) return <Loading/>` early return. First
render (state still null) never calls the hook; once data loads the early
return stops firing and the hook suddenly runs — different hook count
between renders → React error #310 ("rendered more hooks than during the
previous render"), fatal in production, crashes the whole tree (shown to the
user as a generic browser "couldn't load", not a React error overlay, since
prod builds strip the dev overlay). **`tsc`/`next build` do not catch this**
— it's a runtime-only invariant that only surfaces on an authenticated
render (curl/anonymous checks only ever hit the pre-auth redirect). Fixed by
moving the useMemo above the early return. User confirmed fixed after reload.

**Takeaway for next session**: any new hook added to `Dashboard` in
`page.tsx` MUST go above the `if (!state) return` line (~line 156 area,
search for it). Don't trust a clean `next build` alone for hook-order bugs.

Memory: `~/.claude/projects/-Users-sabar/memory/project_mission_control_itm_countdown.md`.

## Open / not done (not requested this session, no action needed unless asked)
- Visual confirmation in-browser was done by the user directly (not me) —
  confirmed working after the #310 fix.
- No new pending work was requested. Standing backlog items (Boba1 ETH
  collateral disabled, Grogu1 execution-guard, straddle live rollout ramp,
  etc.) are unchanged from before this session — see MEMORY.md for those.

## How to resume
- Repo: `bandurkas/opt`, local clone `~/Desktop/options`, VPS3 clone
  `/root/opt-app` (`root@187.127.114.34`, no SSH alias configured — use the
  IP directly).
- Workflow convention for this project: architecture → code → review → test
  → review → deploy. Code review is mandatory before any deploy.
- VPS3 is 1 CPU — full multi-service `docker compose build` can stall SSH for
  40+ min. Scope builds to 1-2 services at a time (as done this session:
  `backend frontend paper`). `paper` shares `backend`'s build context but is
  a **separate image** — must `docker compose build paper` explicitly too,
  easy to forget.

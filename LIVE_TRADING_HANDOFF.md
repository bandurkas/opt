# Live Bybit trading — build handoff

**Status:** Phase 0 (scaffolding) ~80% done, **NOT deployed, NOT funded, paper bot untouched & still running.** Last updated 2026-06-15.

Goal: take the proven paper strategy (`backend/services/paper_loop.py`) to **real-money Bybit options trading**, "carefully thought through so live execution runs cleanly." Approved plan: `~/.claude/plans/glimmering-churning-kite.md`.

---

## Locked decisions (from the user)
- **Live-first, manual oversight.** User overrode the original testnet-first plan: test **directly on mainnet with the existing keys**, watching manually, ready to stop (kill-switch) or close trades by hand on the Bybit UI. (So testnet is effectively skipped; the `trader` container will run `TRADING_MODE=live`.)
- **Fully autonomous** open/close + kill-switch.
- **Sizing = "as much as margin allows, calculated correctly"** — NOT fixed lot caps. Size off the **real available USDT balance** + real Bybit option margin, with a safety buffer; exchange is the final authority.
- Capital: **$500–1000 USDT** (to be funded; account is at ~$0 now).
- Orders: **limit at mid → market fallback** after timeout.

## Account facts (verified via read-only probe, mainnet keys in VPS `.env`)
- **UTA = 1** (Unified account ✓, required for options).
- Key **readOnly = 0**, permissions include **`Options: ['OptionsTrade']`** ✓ and Derivatives; **no Withdraw** ✓ (safe).
- **ETH options are USDT-settled** — symbols `ETH-26MAR27-1400-P-USDT`, `settleCoin=USDT`, `quoteCoin=USDT`. (Earlier I wrongly assumed USDC — corrected, see P1.)
- Balance: dust only (~$0.86 total: TON/USDT/etc.). **Must fund USDT before any live trade.**

---

## What's built & verified (Phase 0)
All new code defaults to **paper mode + kill-switch OFF** → inert until explicitly armed. Nothing deployed yet.

| File | Purpose | Verified |
|---|---|---|
| `backend/requirements.txt` | pinned `pybit==5.16.0` (money path) | ✓ |
| `backend/services/execution_config.py` | mode (`paper\|testnet\|live`), kill-switch (env `LIVE_ENABLED` + file `LIVE_KILLSWITCH_FILE`), caps, exec tuning. All USDT. Margin-bound sizing (caps default 0=∞) + `LIVE_MARGIN_UTILIZATION=0.5` buffer | ✓ safe defaults asserted |
| `backend/services/execution.py` | `ExecutionClient`: auth probe, `wallet_usdt`/`available_usdt`, `positions`, instrument rounding (tick/qty), `place/cancel/amend`, `sell_to_open`/`buy_to_close` (limit→market fallback, reads real fills) | ✓ 6 unit tests pass |
| `backend/tests/test_execution.py` | mock-session tests of fill/fallback (full/partial/no-fill/failure/sub-min) | ✓ all pass |
| `backend/services/bybit_probe.py` | read-only readiness probe (UTA + perms + USDT wallet) | ✓ ran on mainnet |
| `backend/services/telegram_notify.py` | added fill/error/reconcile/killswitch/cap/slippage/start alerts | ✓ compiles |

Run tests: `cd backend && PYTHONPATH=. python3 tests/test_execution.py`
Run probe (inside a container with keys): `PYTHONPATH=. python3 services/bybit_probe.py`

---

## Code review — paper ↔ live correspondence
Strong guarantee: the **signal logic is shared** (`check_new_signal`, `evaluate_conditions`, debounce, fire-at-:50). Live only swaps **open/close**. So divergence risk is confined to: instrument pick, sizing/margin, price/fees, exit execution.

| # | Aspect | Paper | Live requirement | Status |
|---|---|---|---|---|
| 1 | Settle coin | symbols `…-USDT`, parser strips suffix ok | USDT margin/PnL | ✅ **FIXED** (was USDC) |
| 2 | Instrument pick | `pick_bybit_atm_option`, full `symbol` | same symbol to order | ✅ matches |
| 3 | **Sizing/margin** | model `IM=10%×strike+premium`, base `$400` from DB | **real available USDT** + real Bybit short-IM + buffer + reduce-on-reject | 🔴 **P2 pending** |
| 4 | Entry price | `mid×(1−1%)` modeled | **real fill avg_price** | 🟡 P3 pending |
| 5 | Fees | modeled 0.03% | **real `cumExecFee`** | 🟡 P3 pending |
| 6 | Close PnL | modeled entry−exit | real entry vs real exit fill | 🟡 P3 pending |
| 7 | TP/SL/time triggers | mark-based; TP1 = marker only (no partial); full PnL at TP2/SL/time | same triggers → real `buy_to_close` | ✅ trigger logic matches; exec pending |
| 8 | Equity base | `start+realized` from DB | **real wallet equity** from Bybit | 🔴 P4 pending |
| 9 | Qty/lots | `n_lots×0.1 ETH`, min 0.1 | qty=ETH, round to `qtyStep` | ✅ matches (`_round_qty`) |

### Findings
- **P1 USDC→USDT — FIXED** (this session). Zero USDC refs remain.
- **P2 (critical) — live sizing/margin.** Must size from real available USDT, not the `$400`/10%-IM model. Plan: new `backend/services/live_sizing.py` — `available_usdt × LIVE_MARGIN_UTILIZATION ÷ per_lot_IM`, then exchange-authoritative reduce-on-reject. Need Bybit's real short-option IM (approx is fine since exchange is final authority).
- **P3 — record real fills.** Broker indirection must write actual avg_price/fees/PnL from fills, not `apply_entry_spread`/`fee_per_side`, or the live journal will lie.
- **P4 — equity from exchange.** `compute_equity` reads DB; live must read wallet.
- **P5 (not a bug) — event-loop blocks** up to `LIMIT_TIMEOUT_S` during an order (sync sleep in async loop). Acceptable for v1; note for later async.

---

## Pending work (next session)
1. **P2 — `live_sizing.py`**: correct margin-bound sizing off real USDT balance + buffer + reduce-on-reject. ⬅ do first.
2. **Broker indirection in `paper_loop.py`**: when `TRADING_MODE != paper`, route `open_paper_position` → `execution.sell_to_open` and `_do_close` → `execution.buy_to_close`; persist **real fills** (P3); take equity from wallet (P4). On no-fill/None → skip + audit + Telegram, never assume a fill.
3. **Safety controls in `loop()` before open**: kill-switch, `LIVE_MIN_WALLET_USDT`, `LIVE_DAILY_LOSS_LIMIT_USDT`, `MAX_SPREAD_PCT`/liquidity guard, post-fill slippage alert. Reuse existing circuit breaker.
4. **`backend/services/reconcile.py`**: on startup + every N min, compare `execution.positions()` vs DB open positions; exchange wins; heal + alert (handles the user's manual closes). Block opens while unreconciled.
5. **Data isolation**: trader container uses a **separate DB** (`options_trader` via its own `DATABASE_URL`) → zero `paper_repo` churn, no mixing with paper. (Refinement over the plan's `mode` column.) Need to create that DB + `apply_schema`.
6. **`docker-compose.yml`**: add `trader` service (same image, `command: python services/paper_loop.py`, `TRADING_MODE=live`, separate `DATABASE_URL`, keys, restart). Keep `paper` running as shadow.
7. Deploy (build on Mac per VPS-resource memory; commit→push→VPS pull), arm carefully, fund USDT, watch first live cycle.

## User action items (before going live)
- Fund **USDT** $500–1000 on the mainnet UTA account.
- Decide initial `LIVE_MARGIN_UTILIZATION` (default 0.5) and whether to set any ceiling caps (currently ∞ / margin-bound).
- Keep the Bybit app handy to watch + manually close if needed (reconciler will sync such closes).

## Safety model (built, inert until armed)
- `trading_armed()` = mode≠paper AND `LIVE_ENABLED=true` AND no kill-switch file. Default OFF.
- File kill-switch: `touch /data/STOP_TRADING` halts new opens with no redeploy.
- Telegram alert on every fill/error/cap/slippage/reconcile/killswitch.
- `sell_to_open`/`buy_to_close` never assume a fill — fills read back from exchange; place failure → None → signal skipped.

## Key references
- Strategy/loop: `backend/services/paper_loop.py` (open `open_paper_position`, close `_do_close`/`check_and_close_position`, sizing helpers in `paper_strategy.py`).
- Market data + symbol parser: `backend/services/bybit_client.py`.
- VPS: `187.127.114.34`, repo `/root/opt-app`, paper container `opt-app-paper-1`. Keys in `/root/opt-app/.env` (`BYBIT_API_KEY/SECRET`).

---

## Connection & access (for a fresh session)

> ⚠️ Secrets (API keys, DB password, Telegram token) are **not** in this file — it is committed to a public repo. They live in `/root/opt-app/.env` on the VPS. Never paste secret values into committed files.

**GitHub**
- Repo: `git@github.com:bandurkas/opt.git` · branch **`main`**
- Local clone (Mac, authoritative for edits): `~/Desktop/options`
- VPS clone (deploy target): `/root/opt-app`
- Workflow: edit on Mac → `git commit` → `git push origin main` → on VPS `git pull --ff-only origin main`.

**VPS (Hostinger VPS3)**
- SSH: `ssh root@187.127.114.34` (key-based, no password prompt from this Mac).
- 1 CPU / ~3.8 GB RAM — **build images on the Mac**, do NOT run a full `docker compose build` on the VPS (stalls). Backend cached rebuild (~1 min) and frontend (builds natively on VPS) are fine; cross-building the frontend on Apple Silicon segfaults, so frontend must build on the VPS.
- App dir: `/root/opt-app` (docker-compose project `opt-app`).

**Containers** (`docker ps`): `opt-app-paper-1` (strategy loop), `opt-app-backend-1` (API), `opt-app-frontend-1` (dashboard), `opt-app-poller-1` (Bybit data feed), `opt-app-postgres-1` (DB), `opt-app-redis-1`.

**Ports / URLs**: dashboard `http://187.127.114.34:3000` · API `http://187.127.114.34:8000` (`/api/v1/...`).

**Postgres**: container `opt-app-postgres-1`, db `options_assistant`, user `user` (creds in compose/.env). Query: `ssh root@187.127.114.34 "docker exec -i opt-app-postgres-1 psql -U user -d options_assistant" <<'SQL' ... SQL`. Live trader will use a **separate DB** `options_trader` (to be created).

**Env (`/root/opt-app/.env`)**: `BYBIT_API_KEY`/`BYBIT_API_SECRET` (mainnet, OptionsTrade-enabled), `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, `NEXT_PUBLIC_API_URL=http://187.127.114.34:8000/api/v1`. To go live add: `TRADING_MODE=live`, `LIVE_ENABLED=true`, and (separate) `DATABASE_URL` for the trader service.

**Run the probe / tests from the Mac clone**:
- Tests (no network): `cd ~/Desktop/options/backend && PYTHONPATH=. python3 tests/test_execution.py`
- Probe (needs keys → run in a VPS container): `ssh root@187.127.114.34 "docker exec -e PYTHONWARNINGS=ignore opt-app-paper-1 sh -c 'cd /app && PYTHONPATH=. python3 services/bybit_probe.py'"` (after the new image is built; until then use the inline read-only snippet from the session).

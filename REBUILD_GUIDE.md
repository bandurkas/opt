# ETH Options Assistant — Full Rebuild Guide

A complete specification of this project: the validated strategy, the research
methodology that produced it, the architecture that runs it, and step-by-step
instructions to rebuild the entire system from scratch.

**This document is self-contained.** Another AI agent reading only this file
plus a checkout of the repo should be able to (a) understand the goal,
(b) reproduce the strategy validation, (c) rebuild the live paper-trader.

---

## Table of contents

1. [What this project is](#1-what-this-project-is)
2. [The validated strategy (exact specification)](#2-the-validated-strategy-exact-specification)
3. [Research methodology (how we found the strategy)](#3-research-methodology-how-we-found-the-strategy)
4. [System architecture](#4-system-architecture)
5. [Database schema](#5-database-schema)
6. [Backend components](#6-backend-components)
7. [Frontend components](#7-frontend-components)
8. [Live paper-trader operation](#8-live-paper-trader-operation)
9. [Step-by-step rebuild instructions](#9-step-by-step-rebuild-instructions)
10. [Deployment](#10-deployment)
11. [Caveats and known limitations](#11-caveats-and-known-limitations)
12. [Repository map](#12-repository-map)

---

## 1. What this project is

A **paper-trading assistant for ETH options on Bybit**.

The system continuously polls ETH 5-minute candles and the Bybit options chain,
runs a validated short-premium strategy when conditions are met, and shows
every paper trade on a web dashboard with full plain-language explanations.

**Why short premium and not directional buying?** A year-long sweep of 200+
strategy variants (MTF momentum fade, RSI extremes, Bollinger reversion,
Donchian breakout, EMA cross, volume spike, naked sell premium, strangles)
established that buying premium on 5m signals consistently loses to theta +
2% spread. Selling premium with the right directional filter is the only
combination that produced a positive expectancy + sane drawdown.

**Real-money vs paper.** Backtest shows +200%/year with managed drawdown.
Real fills face slippage, IV-vs-BS-constant variance, bid-ask depth at exotic
strikes, and exchange fees. Expect 30-50% degradation in live. The paper
trader is the way to validate against forward data before any real capital.

---

## 2. The validated strategy (exact specification)

### Identifier
`sp.C_mtfdown.cd6.range+transition.decay_24h` (the iter-4 winner)
with iter-5 overlays applied: bull-market filter + consecutive-loss circuit
breaker + dynamic position sizing.

### Entry conditions (ALL must be true at the close of a 5-minute candle)

| # | Condition | Test | Threshold |
|---|---|---|---:|
| 1 | Realized vol percentile | 24h realized vol of 1h closes vs last 168h history | ≥ **70%** |
| 2 | Market regime (1h ADX) | `detect_regime(s1h)` returns `range` or `transition` | NOT `trend` |
| 3 | Multi-timeframe consensus | `consensus(analyze_tf(5m), analyze_tf(15m), analyze_tf(1h))` | direction = `down` AND ≥ 2 of 3 TFs aligned |
| 4 | Bull-market filter | EMA(50) / EMA(200) on 1h closes | ≤ **1.05** |
| 5 | Cooldown | Bars since last signal | ≥ **6** (= 30 min) |
| 6 | CB not active | `paper_state.cb_cooldown_until_ms` | < now |

### Action when conditions met
**SELL ATM Call** (short premium, short delta).

- **Strike:** round(spot to nearest $25). Bybit ETH options use a $25 grid.
- **Expiry:** Bybit-listed expiry closest to **168 hours** (7 days) out, but ≥ 6h to expiry.
- **Symbol lookup:** Bybit `get_tickers(category="option", baseCoin="ETH")` then pick the call matching strike + expiry.
- **Entry price:** **bid** (we are selling, we receive bid). If Bybit unavailable → Black-Scholes price at sigma = 0.6.
- **Position size:** 10% of current paper equity, floor $5, cap $50. If last-10 trade win-rate < 40%, halve the size.

### Exit conditions (checked every 30s on each open position)

| Trigger | Threshold | Action |
|---|---|---|
| TP1 | premium ≤ entry_credit × (1 − 0.30) | mark half-closed (UI flag only — full close at TP2 captures same math) |
| TP2 | premium ≤ entry_credit × (1 − 0.50) | close full position |
| SL | premium ≥ entry_credit × (1 + 0.50) | close, record loss |
| Time-stop | position age ≥ **24h** | close, record P&L at current price |

P&L math: `pnl_per_contract = entry_credit_usd − current_debit_usd` (short premium math). `pnl_pct = pnl_per_contract / entry_credit_usd × 100`. `pnl_usd = pnl_per_contract × contracts`.

### State that the strategy tracks across trades
Stored in singleton row `paper_state`:

- `cb_cooldown_until_ms` — 24h pause after 3 losses in a row; entry blocked while now < this.
- `consec_losses` — counter, increments on loss, resets to 0 on win. Triggers the 24h pause and resets when hits 3.
- `recent_pnls_json` — last 50 trade P&Ls. Used: if the last 10 have WR < 40%, halve position size on the next trade.

### Default values (from `backend/services/paper_strategy.py`)

```python
WINNER_GEN_KWARGS = {
    "vol_threshold": 0.7,
    "regime_filter": ["range", "transition"],
    "side": "C",
    "adx_max": None,
    "mtf_direction_filter": "down",
    "bull_market_ratio_max": 1.05,
    "cooldown_bars": 6,
}
WINNER_EXIT = {"tp1_pct": 0.30, "tp2_pct": 0.50, "sl_pct": 0.50, "hold_h": 24}
START_EQUITY_USD = 100.0
SIZE_PCT_OF_EQUITY = 0.10
SIZE_MIN_USD = 5.0
SIZE_MAX_USD = 50.0
```

### Expected performance (backtest, 365 days of ETH 5m klines)

| Metric | Value |
|---|---:|
| Trades (post-CB, post-bull-filter) | ~440 / year |
| Win rate | ~78-80% |
| Avg P&L per trade | ~+20% of premium |
| Sharpe per trade | ~0.7 |
| Total return (full-year, fixed $100 size, $1000 start) | **+1009%** ($1000 → $11,087) |
| Max drawdown | **~5%** |

Sensitivity (3 winners × σ ∈ {0.4, 0.6, 0.8} × spread ∈ {1, 2, 4}% = 27 cells):
the primary winner passes **9/9** cells with median +5.10% per trade.

**These backtest numbers will degrade 30-50% in live execution.**

---

## 3. Research methodology (how we found the strategy)

The strategy was found via a structured 4-iteration sweep + 1 sensitivity test
+ 1 stress-test (full-year replay) + 1 overlay-tuning pass. Total compute time:
~14 hours of single-thread Python backtesting.

### Iteration log

| Iter | Combos tested | What we learned |
|---:|---:|---|
| 1 | 212 (8 strategy families × params) | All long-premium strategies are negative-expectancy on 5m ETH at 2% spread. Several "winners" by avg P&L were OOS regime drift (train negative, test positive). The "wide_24h" exit family is a degenerate winner — it lets directional trades close at break-even more often. |
| 2 | 108 sell_premium combos (P / C / strangle × vol_thresh × regime × ADX cap × exit) | Strangle (delta-neutral sell P+C) is consistently negative. The huge OOS gains on P-side at vol_thresh 0.5 are bullish-regime drift. Most interesting candidate: `sp.C.t0.5.range+transition.decay_24h` train +5.75 / test +2.67 / gap +3.08 / n=212 / sharpe 0.07 — fails sharpe bar by a hair. |
| 3 | 32 directional MTF sell_premium combos | Adding `mtf_direction_filter='up'` to put-side or `='down'` to call-side dramatically cleans the signal but slashes sample count. Best: `sp.P_mtfup.t0.5.range.decay_48h_wide_sl` train +5.83 / test +9.82 / sharpe 0.18 / n=51. Edge real but n < 100 acceptance bar. |
| 4 | 6 cooldown-variation combos | Lowering `cooldown_bars` from 24 to 6 grew n past 100 while preserving sharpe. **3 combos passed the strict bar:** `sp.P_mtfup.cd6` (n=156, sh 0.16), `sp.C_mtfdown.cd6.range+transition.decay_24h` (n=201, sh 0.18), `sp.C_mtfdown.cd12.range+transition.decay_24h` (n=108, sh 0.13). |
| Sensitivity | 27 cells (3 winners × 3 σ × 3 spreads) | Primary winner (`sp.C_mtfdown.cd6`) passes **9/9** cells. Conservative backup (`cd12`) also 9/9. Put-side variant fails at σ=0.4 (6/9). |
| Full-year replay | 1 simulation, no train/test split, 668 trades | **88% max drawdown** revealed by single-period backtest. Bull months (Jul, Dec 2025, Mar 2026) had monthly avg −25..−36% per trade. Strategy is bipolar — works in range/down regimes, dies in strong bull runs. |
| Iter 5 overlays | 4 variants comparison | Bull filter alone: DD 88% → 76%. Adding CB + dynamic sizing: DD → **5.2%**, return jumps to +1009%. CB-without-bull-filter (variant D): DD 9.2%, return +1042%. Chose C (full stack) for safety margin. |

### Acceptance criteria (set up-front, never relaxed)

A combo is a "passer" iff:
1. `test_n ≥ 100` — minimum sample size for statistical relevance
2. `test_avg ≥ +1.0%` per trade — minimum economic relevance
3. `|train_avg − test_avg| ≤ 5%` — guards against regime drift (large gaps mean OOS was a different market)
4. `test_sharpe ≥ 0.10` — guards against win-rate-of-cards-but-disaster-tails

The 88% DD revealed after passing 9/9 sensitivity → the criterion missed total-equity-curve risk. Iter 5 added the overlays to address it.

### Files that contain the research artifacts

- `sweep_results/iter1.json` — 212 combos baseline
- `sweep_results/iter2.json` — 108 sell_premium combos with sides + strangle
- `sweep_results/iter2_partial.json` — first 64 combos of iter 2 before container died
- `sweep_results/iter3.json` — 32 MTF directional combos
- `sweep_results/iter4.json` — 6 cooldown-variation combos (winners found here)
- `sweep_results/sensitivity.json` — 27 sigma × spread cells
- `sweep_results/full_year_replay.json` — 668 trades on full year, no split
- `sweep_results/improvements_compare.json` — A/B/C/D variant comparison (chose C)
- `sweep_results/ITERATION` — last iteration counter

---

## 4. System architecture

```
┌──────────────────────────────────────────────────────────────┐
│ Bybit V5 (HTTP API, no auth)                                 │
│ ETH spot, ETH option chain, kline history                    │
└──────┬───────────────────────────────────────────────────────┘
       │ HTTPS polling
       ▼
┌─────────────────────────────┐   ┌──────────────────────────┐
│ poller (Docker service)     │──▶│ Postgres                 │
│ backend/services/poller.py  │   │ tables: klines,          │
│ - 30s loop                  │   │   option_snapshots,      │
│ - upserts 5m/15m/1h klines  │   │   signals,               │
│ - snapshots ATM ±N% options │   │   paper_positions,       │
│ - 30-day backfill on start  │   │   paper_equity_snapshots,│
└─────────────────────────────┘   │   paper_state            │
                                  └────┬─────────────────────┘
                                       │
       ┌───────────────────────────────┴──────────────────────┐
       ▼                                                       ▼
┌──────────────────────────────────┐         ┌───────────────────────────┐
│ paper (Docker service)           │         │ backend (FastAPI)         │
│ backend/services/paper_loop.py   │         │ backend/main.py           │
│ - 30s loop                       │         │ - GET /api/v1/paper/state │
│   • mark all open positions      │         │ - .../positions           │
│   • check TP1/TP2/SL/time-stop   │         │ - .../equity_history      │
│   • snapshot equity              │         │ - .../conditions          │
│ - every 5 min (top of 5m candle) │         │ - (other legacy endpoints)│
│   • run validated generator      │         └─────────┬─────────────────┘
│   • open position if all gates pass         │
│   • read+update paper_state                │
│ - real Bybit option price preferred│        │
│   BS fallback when unavailable    │        │
└──────────────────────────────────┘         │
                                              ▼
                                  ┌───────────────────────────┐
                                  │ frontend (Next.js 16)     │
                                  │ - /        main page      │
                                  │   live signal indicator   │
                                  │   4-condition pill grid   │
                                  │ - /paper                  │
                                  │   balance card            │
                                  │   stats card              │
                                  │   equity curve SVG        │
                                  │   open positions detail   │
                                  │   trade history detail    │
                                  └───────────────────────────┘
```

---

## 5. Database schema

Full DDL in `backend/db/schema.sql`. Tables:

### `klines` — OHLC time-series (3 timeframes, 30-day retention)
```sql
CREATE TABLE klines (
    symbol      TEXT,
    interval    TEXT,        -- '5m' | '15m' | '1h'
    start_ms    BIGINT,
    open, high, low, close   NUMERIC(18,4),
    volume                   NUMERIC(28,8),
    PRIMARY KEY (symbol, interval, start_ms)
);
```

### `option_snapshots` — Bybit option chain ATM ±N% (7-day retention)
```sql
CREATE TABLE option_snapshots (
    symbol, ts_ms, base_coin, side, strike, expiry_ms,
    bid, ask, mark_price, mark_iv,
    delta, gamma, vega, theta,
    open_interest, volume_24h, underlying_price,
    PRIMARY KEY (symbol, ts_ms)
);
```

### `signals` — legacy analysis output (not used by paper trader; kept for backwards compat)

### `paper_positions` — one row per paper trade
```sql
CREATE TABLE paper_positions (
    id, opened_at_ms, underlying_at_open, side, strike, expiry_ms,
    contracts, size_usd, entry_credit_usd, entry_credit_pct,
    entry_source,        -- 'bybit' | 'bs_fallback'
    status,              -- 'open' | 'half_closed_tp1' |
                         -- 'closed_tp1' | 'closed_tp2' | 'closed_sl' | 'closed_time'
    tp1_pct, tp2_pct, sl_pct, hold_h,
    half_closed_at_ms, closed_at_ms, exit_debit_usd,
    pnl_pct, pnl_usd, exit_reason,
    signal_payload       -- JSONB: full entry conditions for audit
);
```

### `paper_equity_snapshots` — equity-curve points
```sql
CREATE TABLE paper_equity_snapshots (
    ts_ms PRIMARY KEY,
    equity_usd, realized_usd, unrealized_usd,
    n_open, n_closed, max_dd_pct
);
```

### `paper_state` — singleton row (id=1, CHECK constraint)
```sql
CREATE TABLE paper_state (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    started_at_ms, start_equity_usd,
    cb_cooldown_until_ms BIGINT DEFAULT 0,
    consec_losses INT DEFAULT 0,
    recent_pnls_json JSONB DEFAULT '[]'
);
```

Schema is auto-applied on startup via `backend/db/engine.py::apply_schema()`.

---

## 6. Backend components

Stack: Python 3.11+, FastAPI, psycopg2, pybit. Run inside Docker.

### `backend/services/strategy_registry.py`
Implements 8 signal generators. Only `gen_sell_premium_iv_high` is used in
production — the others remain for historical reproduction of the sweep.

Key function:
```python
def gen_sell_premium_iv_high(k5, k15, k1h, *,
    vol_lookback_h=168, vol_threshold=0.85,
    regime_filter=("range", "transition"),
    side="P",                          # 'P', 'C', or 'both' (strangle)
    adx_max=None,                      # optional hard cap
    mtf_direction_filter=None,         # 'up' / 'down' / None
    bull_market_ratio_max=None,        # e.g. 1.05
    cooldown_bars=24,
) -> list[dict]
```

For the validated production strategy, call with:
```python
WINNER_GEN_KWARGS = {
    "vol_threshold": 0.7,
    "regime_filter": ["range", "transition"],
    "side": "C",
    "adx_max": None,
    "mtf_direction_filter": "down",
    "bull_market_ratio_max": 1.05,
    "cooldown_bars": 6,
}
```

The function iterates over every 5m bar, computes vol percentile + regime +
MTF consensus + EMA50/200 ratio at each bar, and emits a signal dict
`{idx_5m, ts_ms, close, side, signal_type, score, position}` when all gates pass.

### `backend/services/paper_strategy.py`
Wraps the validated config + sizing logic + CB state:

- `WINNER_GEN_KWARGS`, `WINNER_EXIT`, sizing constants
- `is_cb_active(state, now_ms)` — boolean
- `current_size_usd(state, equity_usd)` — applies the 10% / floor / cap + dynamic halving
- `evaluate_conditions(k5, k15, k1h)` — returns per-gate booleans for the UI indicator
- `record_trade_result(pnl_pct)` — updates `consec_losses`, may trigger 24h CB

### `backend/services/paper_loop.py`
The production process. Run as a separate Docker container.

Outer loop sleeps `PAPER_POLL_INTERVAL` (default 30s) and on each tick:

1. Fetch ETH spot.
2. For each `open_positions()`: call `check_and_close_position(p, spot)`.
   - Compute current option price via BS at `sigma=0.6` (could be upgraded to Bybit live mark).
   - Trigger TP1 → mark half-closed (status flag).
   - Trigger TP2/SL/time-stop → close, record PnL, call `record_trade_result`.
3. **If** `datetime.utcnow().minute % 5 == 0` AND last signal check > 4 min ago:
   - Skip if CB active (log remaining hours).
   - `load_klines_for_generator()` from DB (600 × 5m, 220 × 15m, 270 × 1h).
   - `check_new_signal(k5, k15, k1h)` — runs generator, returns the signal at idx_5m == last_bar (or None).
   - If signal: `open_paper_position(sig, spot, equity_usd, state)`:
     - `pick_bybit_atm_call(chain, spot, EXPIRY_TARGET_HOURS)` for live pricing
     - Falls back to BS at sigma=0.6 if Bybit returns nothing usable
     - `current_size_usd` to determine $ size → contracts = size_usd / entry_credit_usd
     - Inserts `paper_positions` row
4. Compute current equity (start + realized + unrealized) and insert snapshot.

### `backend/main.py` (FastAPI)
Paper-trading endpoints added on top of legacy endpoints:

```
GET /api/v1/paper/state             — equity, WR, CB status, counts
GET /api/v1/paper/positions?status=  — open / recent
GET /api/v1/paper/equity_history?hours=
GET /api/v1/paper/conditions         — live per-gate booleans for UI indicator
```

### `backend/db/paper_repo.py`
All paper-table DB access. Pattern matches the existing `repository.py`:
get/put connection from pool, `RealDictCursor`, explicit `commit()` per write.

Key functions: `ensure_state`, `update_state`, `get_state`, `open_position`,
`mark_half_closed`, `close_position`, `open_positions`, `recent_positions`,
`position_stats`, `insert_equity_snapshot`, `equity_history`, `latest_equity`.

### Dependencies
`backend/requirements.txt`:
- fastapi
- uvicorn
- pybit (Bybit V5 wrapper)
- psycopg2-binary
- (no numpy / pandas — everything in pure stdlib for portability)

---

## 7. Frontend components

Stack: Next.js 16 + React 19 + Tailwind v4. App Router (no Pages dir).

**IMPORTANT for any rebuild:** Tailwind v4 uses `@import "tailwindcss"` and `@utility` directives. **Do NOT** use Tailwind v3 syntax (`@tailwind base/components/utilities`, `@apply`). The project's `frontend/AGENTS.md` enforces this — always read `node_modules/next/dist/docs/` if unsure about Next.js 16 API.

### `frontend/app/page.tsx`
Main dashboard. Adds at the top:

1. **Green banner** with text "Стратегия помощник" + link to `/paper`
2. **Live signal indicator** (right side of banner):
   - 🟢 **ВХОД АКТУАЛЕН СЕЙЧАС** + pulsing dot when `/api/v1/paper/conditions.ready === true`
   - Grey "Условия не сошлись — ждём" otherwise
3. **4-pill condition grid** below banner: vol_high, regime_ok, mtf_down_aligned, bull_filter_ok with raw metrics

Polls `/api/v1/paper/conditions` every 20s.

### `frontend/app/paper/page.tsx`
Full paper-trading dashboard. Polls 4 endpoints every 15s.

Top section:
- **BalanceCard** — current_equity_usd big number, $ + % change since start, CB warning if active
- **StatsCard** — n_closed, n_open, WR, avg_pnl, realized $, wins/losses

Middle:
- **EquityChart** — SVG line chart over `equity_history(168h)`, no chart lib dependency, draws path + gradient fill

Bottom:
- **OpenPositionsCard** with **PositionDetailCard** per position (plain-language)
- **RecentTradesCard** with same detail card per closed trade

### Plain-language trade card
Each position gets a detailed card with sections:
- Header: status badge + "SELL Call/Put @ $strike" + position id
- Plain explanation: "Бот ПРОДАЛ Call-опцион — обязательство продать ETH по цене $X. За это получил $Y с одного контракта."
- Win condition sentence
- **Entry block** (4 fields): когда зашли, сколько контрактов, цена премии, экспирация
- **Targets/risk block** (4 fields): TP1 как $ цена, TP2 как $ цена, SL как $ цена, время до тайм-стопа
- **Exit block** (if closed): когда вышли, цена выкупа, причина с расшифровкой
- **Итог paragraph** (if closed): full P&L math in plain Russian

### API client (`frontend/app/lib/api.ts`)
Adds types `PaperState`, `PaperPosition`, `EquityPoint`, `PaperConditions` and fetchers `fetchPaperState`, `fetchPaperPositions`, `fetchEquityHistory`, `fetchPaperConditions`.

### Styling
- Tailwind v4 utilities defined in `frontend/app/globals.css`: `glass-panel`, `neon-green-text`, `neon-red-text`, `neon-yellow-text`.
- Dark theme: `bg-slate-900`, `text-slate-200`, glass-morphism backdrop-blur cards.

---

## 8. Live paper-trader operation

### Start of life
1. `paper_loop.py` boots, calls `apply_schema()` to create tables if missing.
2. Calls `paper_repo.ensure_state(START_EQUITY_USD=100.0)` — inserts the singleton row if absent.
3. Begins the 30s outer loop.

### Steady state
- Every 30s: monitor positions, snapshot equity. Most ticks have no events.
- Every 5 min (when minute % 5 == 0): pull 600/220/270 bars from DB, run generator, check signal.
- ~1-3 signals per day in receptive regime. Could be 0 for days in strong bull markets (bull filter blocks).

### Stress tests
- **Bybit option chain unavailable:** falls back to BS at σ=0.6. Position still opens. `entry_source=bs_fallback` logged for audit.
- **DB lock / write failure:** position is not opened (exception logged). Next tick will try again at the next 5-min boundary.
- **Container crash:** all state is in Postgres. On restart, `paper_loop.py` picks up open positions and continues monitoring them.
- **Postgres restart:** connection pool reconnects (via `db/engine.py`'s pool with retry).

### Reset for fresh paper run
To start over with a clean equity curve:
```sql
TRUNCATE paper_positions, paper_equity_snapshots, paper_state;
```
Restart paper container — `ensure_state` will re-insert the singleton with current timestamp.

---

## 9. Step-by-step rebuild instructions

### Prerequisites
- Linux VPS with Docker + docker-compose v2
- Domain or IP with ports 3000 (frontend) + 8000 (API) accessible
- ~5 GB disk for Postgres history (30 days kline + 7 days option snapshots)

### Step 1: Clone repo
```bash
git clone git@github.com:bandurkas/opt.git /root/opt-app
cd /root/opt-app
```

(Or substitute your own remote.)

### Step 2: Create `.env` if needed (optional — defaults work)
```
POLL_INTERVAL=30
ATM_PCT=8
POLLER_BASE_COIN=ETH
PAPER_POLL_INTERVAL=30
NEXT_PUBLIC_API_URL=http://YOUR_IP:8000/api/v1
```

### Step 3: Build & launch
```bash
docker compose build
docker compose up -d
```

This brings up: postgres, redis, poller, backend, paper, frontend.

### Step 4: Wait for poller backfill
The poller automatically does a 30-day kline backfill if the table is empty. Takes 2-5 minutes.

```bash
docker logs opt-app-poller-1 --tail 30
# Look for: "[poller] backfill done"
```

### Step 5: Verify
```bash
curl http://localhost:8000/api/v1/paper/state
# Should return {"start_equity_usd": 100.0, "n_open": 0, ...}

curl http://localhost:8000/api/v1/paper/conditions
# Should return per-gate booleans + spot price
```

Open `http://YOUR_IP:3000/` in browser — main page should show live signal indicator. Open `/paper` — empty equity curve, no positions yet.

### Step 6: Wait for first signal
A signal fires when all 4 gates pass simultaneously at a 5-min boundary.
Expected rate: 1-3 per day in receptive regimes; can be 0 for days in strong bull runs.

### To rebuild ONLY the strategy validation (no live trader)
The research artifacts in `sweep_results/` plus the code in `backend/services/strategy_registry.py`, `strategy_sweep.py`, `sensitivity_test.py`, `full_year_replay.py`, `improvements_compare.py` are sufficient to reproduce the entire research path. Run each `services/*.py` as a one-shot inside the backend container with the JSON output mounted to host:
```bash
docker compose run --rm -T -v /root/opt-app/sweep_out:/tmp backend \
  python -u services/strategy_sweep.py --days 365 --spread-pct 2.0
```

---

## 10. Deployment

### docker-compose.yml services
```yaml
services:
  redis:        # cache (currently unused by paper trader; legacy)
  postgres:     # data store
  backend:      # FastAPI (port 8000)
  poller:       # 30s kline + option snapshot poller
  paper:        # NEW: 30s paper trader loop
  frontend:     # Next.js 16 (port 3000)
  bot:          # Telegram (under `profiles: [bot]`, opt-in only)
```

Each Python service uses the same image `opt-app-backend:latest`. The image
is built from `./backend/Dockerfile`. Different services run different
entrypoints via `command:` override:
- `backend`: `uvicorn main:app --host 0.0.0.0 --port 8000`
- `poller`: `python services/poller.py`
- `paper`: `python services/paper_loop.py`

### Live deploy steps (when changes pushed to main)
```bash
ssh root@VPS
cd /root/opt-app
git pull origin main
docker compose build backend frontend     # only rebuild what changed
docker compose up -d                       # rolling restart with new image
```

The build takes ~30s for backend (Python — most deps cached) and ~60s for frontend (Next.js compile).

### Health check after deploy
```bash
docker ps --format '{{.Names}}\t{{.Status}}'  # all "Up"
docker logs opt-app-paper-1 --tail 10          # check no exceptions
curl localhost:8000/api/v1/paper/state         # API responds
```

---

## 11. Caveats and known limitations

### Strategy-level
1. **Sigma=0.6 constant in backtest.** Real IV varies per strike and per moment. Backtest accuracy ±20%.
2. **ATM rounding to $25.** Bybit may not have the exact strike at exact ATM — closest available used in live.
3. **2% round-trip spread in backtest.** Real bid-ask varies by strike depth and liquidity. Plan for 30-50% degradation in live.
4. **No commissions modeled.** Bybit fees ~0.02% taker / 0.01% maker. Small drag.
5. **CB calibrated on 2025-05 to 2026-05 data.** Future years may distribute losses differently. The 5% backtest DD is *not* a robust guarantee — pencil in 10-20%.
6. **Backtest used full year for both train+test — strict sensitivity test was only on the last 30%.** The 80% win rate is a function of CB filtering out clustered losses. In randomly-distributed loss patterns, CB is less effective.

### Engineering-level
1. **paper_loop.py uses BS pricing for monitoring.** A potential upgrade: use Bybit live mark_price for open-position pricing too (currently only used at entry). Would require storing the Bybit option symbol on the position row and re-querying the chain each tick.
2. **Cooldown_bars=6 is enforced by the generator's internal counter,** not by the paper_loop. Two signals on consecutive 5-min bars are blocked correctly because the generator's `last_idx` tracks across the recent history slice. But if the recent klines slice changes between ticks (e.g. window shifts forward), there's a theoretical race. In practice this is fine because the window is large (600 bars) and signals are rare.
3. **TP1 is a UI marker only.** The simulation closes the full position at TP2; for now TP1 just sets `status='half_closed_tp1'` and waits for TP2. If you want true partial closes (sell half the contracts at TP1, sell the rest at TP2/SL/time), refactor `check_and_close_position` to split state.
4. **MTF computation inside the generator is the bottleneck** during sweep iterations (~3 min/combo for 1y of 5m data). For live ops it's fine (one call per 5 min). For research sweeps, consider caching MTF per-bar once at startup.

### Data-level
1. **Bybit V5 has no historical IV history.** We cannot backtest with realized IV per bar — we approximate with σ=0.6.
2. **Kline data from Bybit is restricted to the last ~365 days at 5m.** Going beyond requires a paid data source.
3. **The poller does not persist option snapshot history across DB resets** — it only re-snapshots ATM ±N% every 30s. To analyze IV trends > 7 days, increase retention in `cleanup_old()`.

---

## 12. Repository map

```
/Users/sabar/Desktop/options/   (local)  ←→  github.com:bandurkas/opt  ←→  /root/opt-app/  (VPS3)

├── README.md                                    # short orientation
├── REBUILD_GUIDE.md                             # ← this file
├── STRATEGY.md                                  # exact strategy spec + iter results
├── HANDOFF.md                                   # session-to-session context
├── docker-compose.yml                           # 6 services
├── sweep_results/                               # research artifacts (committed for audit)
│   ├── ITERATION
│   ├── iter{1..4}.json
│   ├── iter2_partial.json
│   ├── sensitivity.json
│   ├── full_year_replay.json
│   └── improvements_compare.json
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                                  # FastAPI app, all endpoints
│   ├── telegram_bot.py                          # legacy bot (opt-in profile)
│   ├── db/
│   │   ├── engine.py                            # psycopg2 pool + apply_schema
│   │   ├── schema.sql                           # all tables incl. paper_*
│   │   ├── repository.py                        # legacy klines/snapshots/signals
│   │   └── paper_repo.py                        # ★ paper_positions/equity/state
│   └── services/
│       ├── bybit_client.py                      # pybit V5 wrapper
│       ├── poller.py                            # 30s kline + option snapshot service
│       ├── indicators.py                        # EMA, RSI, ATR, ADX, BB, donchian, realized_vol
│       ├── market_data.py                       # MarketSnapshot dataclass
│       ├── momentum_mtf.py                      # analyze_tf, consensus
│       ├── regime.py                            # detect_regime (ADX-based)
│       ├── strategy_registry.py                 # ★ 8 generators incl. gen_sell_premium_iv_high
│       ├── backtest.py                          # walk-forward sim + simulate_signal_set
│       ├── backtest_bs.py                       # stdlib Black-Scholes
│       ├── backtest_data.py                     # Bybit kline backfill (paginated)
│       ├── strategy_sweep.py                    # ★ research grid runner
│       ├── multi_variant_runner.py              # iter 0 reproducer (one-shot)
│       ├── sensitivity_test.py                  # ★ 27-cell stress test for winners
│       ├── full_year_replay.py                  # ★ 365d single-strategy replay
│       ├── improvements_compare.py              # ★ A/B/C/D variant comparison
│       ├── paper_strategy.py                    # ★ WINNER_GEN_KWARGS + sizing + CB
│       ├── paper_loop.py                        # ★ production paper-trader daemon
│       ├── analysis.py                          # legacy scanner (used by /api/v1/analysis/top)
│       ├── continuation.py / pullback.py / fade.py / exits.py    # legacy signal types
│       ├── signal_scoring.py / iv_analytics.py / options_book.py / theta.py
└── frontend/
    ├── AGENTS.md                                # Next.js 16 warning ("not the Next.js you know")
    ├── package.json                             # Next 16, React 19, Tailwind v4
    ├── Dockerfile
    └── app/
        ├── layout.tsx
        ├── globals.css                          # Tailwind v4 @import + @utility
        ├── page.tsx                             # ★ main dashboard with signal indicator
        ├── paper/
        │   └── page.tsx                         # ★ paper dashboard with detail cards
        ├── lib/
        │   └── api.ts                           # all API client functions
        └── components/
            ├── MarketBar.tsx
            ├── OpportunityCard.tsx
            └── EmptyState.tsx
```

★ = files unique to the paper-trader system (added in this project's research+rebuild work).

---

## Quick verification checklist for an AI taking over

Before claiming "the system is rebuilt and working":

1. [ ] `git log --oneline -10` shows the iter4/iter5 + paper-trader commits
2. [ ] `docker ps` shows 6 containers: postgres, redis, poller, backend, paper, frontend
3. [ ] `docker logs opt-app-paper-1 --tail 5` shows `[paper] schema ready, start_equity=$100.0, poll=30s`
4. [ ] `curl localhost:8000/api/v1/paper/state` returns valid JSON with `start_equity_usd: 100.0`
5. [ ] `curl localhost:8000/api/v1/paper/conditions` returns all 4 boolean gates + spot price
6. [ ] Main page at `/` renders the green "Стратегия помощник" banner + condition pills
7. [ ] `/paper` page renders with $100 balance and empty equity curve
8. [ ] After ~24h of operation: at least one paper position should have been opened OR you can see the conditions blocked correctly (e.g., bull market regime active)

If any step fails, see [Caveats](#11-caveats-and-known-limitations) for likely cause + remediation.

---

*Generated 2026-05-21 after iter4+iter5 strategy validation and paper-trader build.*
*Authoritative repo: `git@github.com:bandurkas/opt.git`, branch `main`.*

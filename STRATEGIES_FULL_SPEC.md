# Options Assistant — Full Strategy Spec (3 books)

Written 2026-06-22 as a from-scratch implementation reference. Any agent reading
only this file should be able to rebuild all three trading bots byte-for-byte
without access to the original conversation history.

**Project root:** `~/Desktop/options` (local, authoritative for edits) /
`git@github.com:bandurkas/opt.git` (`main`) / VPS3 `root@187.127.114.34:/root/opt-app`
(deploy target, same repo, `docker compose` project `opt-app`).

**Stack:** FastAPI backend (`backend/`) + Next.js 16/React 19/Tailwind v4 frontend
(`frontend/`), Postgres (`psycopg2`, `RealDictCursor`), Redis (poller cache),
Docker Compose multi-service orchestration. Each strategy is one long-running
async Python service (`backend/services/*_loop.py`) talking to its own Postgres
tables, polled by the dashboard via FastAPI routes.

---

## 0. Shared architecture across all 3 books

### 0.1 Financial isolation
Each book has **its own** Postgres tables, **own** equity counter, **own**
circuit-breaker state, **own** API routes, **own** dashboard section. No book
reads or writes another book's tables. This is deliberate — a bug or a margin
squeeze in one book cannot corrupt or starve another. The only shared
infrastructure is: the Postgres instance itself, the `klines`/`option_snapshots`
tables (written by the poller, read-only to the strategy loops), and the Bybit
account (capital is NOT exchange-partitioned between live traders — see §4).

### 0.2 Paper vs. live — same script, one gate
Every strategy loop is written to run identically in paper and live mode. All
live-only code is gated behind `services.broker.is_live()` (defined as
`mode != "paper" AND LIVE_ENABLED=true AND no kill-switch file`). In paper mode
every `broker.is_live()` branch is skipped and the bot behaves exactly as a
pure simulation against modeled fills. Going live is a **docker-compose
service + env flip**, never a rewrite. This is the single most important
design invariant — preserve it in any reimplementation.

### 0.3 Common execution/safety stack (`backend/services/`)
These modules are written once and reused by all three loops (each loop passes
its own `repo_module`/`base_coin`/`lot_size` where the module needs to be
strategy-aware):

- **`execution_config.py`** — mode (`paper|testnet|live`), kill-switch (env
  `LIVE_ENABLED` + file `LIVE_KILLSWITCH_FILE`), `LIVE_MARGIN_UTILIZATION=0.5`,
  `LIVE_MIN_WALLET_USDT=50`, `LIVE_DAILY_LOSS_LIMIT_USDT=100`,
  `MAX_SPREAD_PCT=15`, `MAX_SLIPPAGE_PCT=25`, `LIMIT_TIMEOUT_S=20`,
  `RECONCILE_EVERY_MIN`.
- **`execution.py`** — `ExecutionClient`: Bybit auth probe, `wallet_usdt`/
  `available_usdt`, `positions()`, instrument tick/qty rounding, `place/cancel/
  amend`, `sell_to_open`/`buy_to_close` (limit at mid → market fallback after
  `LIMIT_TIMEOUT_S`, reads back REAL fills — never assumes a fill).
- **`live_sizing.py`** — margin-bound sizing off the real available USDT
  balance × `LIVE_MARGIN_UTILIZATION` ÷ per-lot IM, with reduce-on-reject
  (exchange is the final sizing authority, not the model).
- **`broker.py`** — thin indirection: `is_live()`, `wallet_equity_usdt()`,
  `live_open(symbol, strike, premium_mid, lot_size=...)`, `live_close(symbol,
  contracts, mark, lot_size=...)`. Routes to `execution.py` under the hood;
  every strategy loop calls only `broker.*`, never `execution.py` directly.
- **`live_safety.py`** — pure pre-open gates: `spread_ok`/`spread_pct`
  (liquidity guard), `slippage_pct`/`slippage_alarming` (post-fill alert, does
  not block), `daily_loss_limit_hit` (halts new opens for the UTC day),
  `utc_day_start_ms`.
- **`reconcile.py`** — on startup + every `RECONCILE_EVERY_MIN`, diffs real
  Bybit option positions (`base_coin`-filtered) against the strategy's own DB
  open positions. Exchange wins: DB-open-but-flat-on-exchange → healed/closed
  with reason `reconciled`; on-exchange-but-not-in-DB → `is_blocked()=True`,
  refuses new opens until resolved (never auto-adopts an untracked position).
  **Known landmine:** keyed by `base_coin` only, NOT by strategy. If two live
  traders that both touch the same coin (e.g. a future ETH-signal trader AND
  an ETH-straddle trader) run live simultaneously on the same Bybit account,
  each will see the other's legitimate positions as "untracked" and block
  itself. Fix before ever running two same-coin live traders together
  (tag positions by strategy, key the diff by tag not just coin).
- **`telegram_notify.py`** — fill/error/reconcile/killswitch/cap/slippage/
  start alerts, with an `asset` kwarg so messages are unambiguous across books.
- **`backtest_bs.py`** — Black-Scholes pricer, `bs.price(side, spot, strike,
  T_years, sigma)`, used as a fallback when no live Bybit contract is pickable.
- **`bybit_client.py`** — `bybit_client.get_spot_price(symbol)`,
  `bybit_client.get_options_tickers(base_coin)` → list of chain dicts with
  `side` (`C`/`P`), `strike`, `expiry_ms`, `bid`, `ask`, `mark_price`, `symbol`.

### 0.4 Sizing/friction conventions (apply unless a book overrides)
- Bybit option IM (IM_RATE) modeled as **10% of strike-notional** plus the
  premium itself: `margin_per_lot = (IM_RATE * strike + premium) * lot_size`.
- Entry/exit friction modeled as a **2% round-trip spread** (1% half-spread
  each side): sell at `mid * (1 - 0.01)`, buy back at `mid * (1 + 0.01)`.
- Fee: **0.03% of notional**, capped at **12.5% of the premium** that side
  handles: `fee = min(notional * 0.0003, abs(premium_total) * 0.125)`.
- These are simulation assumptions for paper mode. In live mode all of this is
  replaced by real fills/fees read back from the exchange (`broker.live_open`/
  `broker.live_close`).

### 0.5 Database conventions
Each book gets 3 tables: `<prefix>_positions`, `<prefix>_equity_snapshots`,
`<prefix>_state` (singleton row, `id` fixed). Repo modules
(`db/<prefix>_repo.py`) expose: `ensure_state(start_equity)`, `get_state()`,
`update_state(**kwargs)`, `open_position(**kwargs)→id`, `open_positions()`,
`close_position(id, ...)`, `position_stats()` (realized/unrealized/n_open/
n_closed/wins/losses/win_rate/avg_pnl_pct/exit_counts), `peak_equity_since
(started_at_ms)`, `realized_pnl_since(ts_ms)`, `insert_equity_snapshot(...)`.
The straddle books' repo additionally key positions by `cycle_id` + `leg`
(`C`/`P`) instead of the signal book's single `side`.

### 0.6 API/frontend conventions
FastAPI routes per book: `GET /api/v1/<prefix>/state`, `GET /api/v1/<prefix>/
positions?status=open|recent&limit=N`, `GET /api/v1/<prefix>/equity_history?
hours=N`. The frontend (`frontend/app/lib/api.ts`) defines a typed fetcher per
route; `frontend/app/page.tsx` renders one dashboard section per book (StatCard
grid + equity chart + open-legs table + closed-cycles journal).

### 0.7 Docker Compose conventions
Each book is a `command: python services/<name>_loop.py` service,
`depends_on: [postgres, <its poller>]`, `env_file: .env`, env-overridable
`*_POLL_INTERVAL`/`*_START_EQUITY_USD`/`*_MARGIN_PCT`, `restart: unless-stopped`
(auto-starts with the stack — these are paper books, inert-safe by design).
A **separate** `*_trader` service (same image, same script, `TRADING_MODE=
live`, a **separate** Postgres DB so live data never mixes with the paper
shadow, `profiles: [trader]` so it does NOT auto-start) is the live
counterpart — `docker compose --profile trader up -d <name>_trader`, still
armed-OFF until `LIVE_ENABLED=true` is set per-trader.

---

## 1. Book 1 — ETH V3 Hybrid signal bot (`paper_loop.py`)

**Tables:** `paper_positions`, `paper_equity_snapshots`, `paper_state`.
**Service:** `paper` (paper), `trader` (live, profile `trader`, DB
`options_trader`). **Deposit:** `$800` (env `PAPER_START_EQUITY_USD`).
**Files:** `services/paper_loop.py`, `services/paper_strategy.py`,
`services/strategy_config.py`, `services/strategy_registry.py`
(`gen_sell_premium_iv_high`).

### 1.1 What it is
A **signal-driven** (not clock-driven) short-premium bot. Every 5 minutes it
asks "is there a directional vol-selling setup right now?" and if yes, sells
ONE ATM option (Call or Put, never both at once). This is fundamentally
different from books 2/3, which sell both legs unconditionally on a fixed
clock — this book is selective and waits for a regime/timing edge.

### 1.2 Side selection — V2 trend-following hybrid
Compute `ret_7d` = 7-day % return of ETHUSDT on 5m closes (`BARS_7D = 2016`
bars = 7×24×12):
```
ret_7d > +0.5%   → only Put allowed   (uptrend — Put premium decays)
ret_7d < -0.5%   → only Call allowed  (downtrend — Call premium decays)
|ret_7d| < 0.5%  → both allowed, side-specific filters below decide
```
`RET_7D_THRESHOLD = 0.5`. This boundary was the winner of a `{0.5,1,1.5,2,3}`
sweep. Rationale documented in `strategy_config.py`'s module docstring: pure
Put-only and pure Call-only configs both have multiple deep losing months;
the hybrid only has 2 (vs 4-6), because it stops selling Puts right when a
real downtrend starts crushing them (and vice versa for Calls).

### 1.3 Per-side entry filter — `gen_sell_premium_iv_high`
(`strategy_registry.py`). Core idea: sell premium only when realized vol is
rich AND the regime/MTF context favors that side. Logic per 5m bar:
1. Roll a 1h realized-vol series (`realized_vol(closes, lookback=24)`,
   `vol_lookback_h=168` window). Compute the percentile of the CURRENT vol
   reading vs that rolling history.
2. Require `current_vol >= percentile_threshold` (side-specific
   `vol_threshold`, see table below) — i.e. only sell when vol is in the top
   `(1-vol_threshold)` of the trailing week.
3. Require the 1h regime (`detect_regime`) to be in the side's
   `regime_filter` list (`"range"`/`"transition"`/`"trend"`).
4. Require MTF consensus (`consensus(analyze_tf(5m), analyze_tf(15m),
   analyze_tf(1h))`) to match `mtf_direction_filter` with ≥2/3 timeframes
   aligned.
5. Put side only: bull-market kill-switch — skip if `EMA50_1h/EMA200_1h >
   bull_market_ratio_max` (don't sell calls/in this case the check is wired
   for the Call side's symmetric guard too, see `CALL_GEN_KWARGS`).
6. Cooldown: `cooldown_bars=6` (30 min) between any two signals of the same
   side.

| param | PUT_GEN_KWARGS | CALL_GEN_KWARGS |
|---|---|---|
| `vol_threshold` | 0.50 | 0.60 |
| `regime_filter` | `["range"]` | `["range","transition"]` |
| `mtf_direction_filter` | `"up"` (≥2/3 aligned) | `"down"` (≥2/3 aligned) |
| `bull_market_ratio_max` | `None` | `1.05` |
| `cooldown_bars` | 6 | 6 |

`check_new_signal` (`paper_loop.py`) tries `allowed_sides(ret_7d)` in order;
first side whose generator fires on the LAST bar wins (only one position
opens per tick, ever).

### 1.4 Entry execution & debounce
Conditions are re-evaluated **every minute**; the open is only committed near
the 5m candle close (`ENTRY_FIRE_SECOND=50` of the window's last minute) and
**only if every per-minute check inside that 5-minute window passed**
(persistence/debounce rule — a flicker mid-window disqualifies the whole
window). This avoids opening on a single noisy tick.

### 1.5 Instrument pick & pricing
`pick_bybit_atm_option(chain, spot, target_expiry_h, side)`: from the live
Bybit chain, filter to the requested side with `expiry_ms > now + 6h` and a
positive bid, pick the contract whose expiry is closest to
`target_expiry_h`, then among same-expiry contracts pick the strike closest
to `round(spot/25)*25` (Bybit's $25 ETH strike grid). If no live contract
qualifies, fall back to Black-Scholes pricing (`DEFAULT_SIGMA=0.6`) at a
synthetic strike/expiry — but in **live mode this fallback is refused**
(`entry_source != "bybit"` → skip; you never place a real order against a
modeled price).

**Per-side target expiry is asymmetric** (the single biggest non-obvious
tuning result in this book):
```
CALL_TARGET_EXPIRY_H = 24    # short-dated: lifts holdout/trade +7.37%→+14.79%
PUT_TARGET_EXPIRY_H  = 168   # long-dated: short puts have no edge at 24h
```
Why: per-day theta edge for Calls is far higher short (24h ≈ +13%/day vs
168h ≈ +2.8%/day); Puts have steep downside skew and get run over fast if
short-dated, so they need the full week to let mean-reversion play out.

### 1.6 Exit params (per side)
```python
PUT_EXIT  = {"tp1_pct": 0.50, "tp2_pct": 0.70, "sl_pct": 2.00, "hold_h": 96}
CALL_EXIT = {"tp1_pct": 0.40, "tp2_pct": 0.80, "sl_pct": 0.75, "hold_h": 24}
```
- `tp1_pct`/`tp2_pct` = fraction of entry credit decayed (e.g. Put TP2 fires
  when current mark ≤ `entry_credit * (1 - 0.70)`).
- `sl_pct` for **Put** = fraction of entry credit ABOVE entry the position can
  lose before stopping (`sl_threshold = entry_credit*(1+sl_pct)`), i.e. a
  static %-of-premium stop. Put SL was widened **1.50→2.00** in a full-year
  sweep: account $612→$712 (+78% vs +53%), maxDD flat, HOLDOUT per-trade avg
  +6.08%→+15.20%. Puts have 168h to recover, so a tight stop just whipsaws.
- `sl_pct` for **Call** is NOT this static value in the live loop — see §1.7,
  it's overridden by a dollar-margin SL. `CALL_EXIT["sl_pct"]=0.75` is kept
  only as the legacy/backtest-comparison constant; widening it was tested and
  HURTS (clogs the 4 margin slots with hung losers).
- TP1 is informational only — `paper_repo.mark_half_closed()` sets a status
  marker, contracts are NOT actually reduced (to match the backtest, which
  never modeled partial closes). Full PnL is recorded at TP2/SL/time-stop.
- Time-stop ALWAYS runs regardless of live-price availability (`age_h >=
  hold_h` → close at current mark, with BS fallback for pricing if the live
  chain is unavailable — but BS is NEVER used for TP/SL decisions, only
  time-stop, because BS σ=0.6 can diverge 30-50% from real Bybit IV and would
  cause false SL/TP triggers during a brief chain outage).

### 1.7 Call dollar-margin stop-loss (overrides §1.6's static SL for Calls)
```python
CALL_SL_DOLLAR_FRAC = 0.10   # env CALL_SL_DOLLAR_FRAC
def call_dollar_sl_pct(strike, entry_credit, sl_dollar_frac=0.10, im_rate=0.10):
    margin = im_rate*strike + entry_credit       # per 1 ETH
    return sl_dollar_frac * margin / entry_credit  # expressed as %-of-credit so it
                                                    # plugs into the existing
                                                    # sl_threshold = credit*(1+sl_pct) check
```
At position-open time, if `active_side == "C"`, `sl_pct` is computed from
this formula (not from `CALL_EXIT["sl_pct"]`) and stored on the position.
Rationale: a %-of-premium stop is meaningless near expiry (premium has
decayed near zero but intrinsic value hasn't — a 2% underlying move can move
the option 30-40x). Margin (dominated by the strike term) stays meaningful
through the option's whole life. Validated: frac=0.10 strictly dominates the
old static 0.75 SL on the real $-account engine (FINAL $2933 vs $2726, same
maxDD 20.8%). Put side has NO viable dollar-SL operating point — stays
%-of-premium.

### 1.8 Sizing
```python
START_EQUITY_USD       = 800     # env PAPER_START_EQUITY_USD (granularity knee)
MARGIN_PCT_PER_TRADE   = 0.15    # env PAPER_MARGIN_PCT_PER_TRADE — % of equity per trade
LOT_MIN_ETH            = 0.1     # Bybit min ETH-option lot
IM_RATE                = 0.10
MAX_PORTFOLIO_MARGIN_PCT = 0.80  # cap on total locked margin across ALL open positions
MAX_OPEN_POSITIONS     = 4       # (execution_config.py) hard concentration cap
```
`realistic_size_lots`: `margin_per_lot = (IM_RATE*strike + premium)*0.1`;
`trade_budget = equity * 0.15 * dyn_size_factor`; lots = `floor(min(
trade_budget, free_margin) / margin_per_lot)`. Skip the signal entirely if
lots < 1 (logs + Telegram-alerts `notify_skipped_margin`).
`dyn_size_factor`: halves size when the last-10-trade win rate < 40%.
`free_margin = equity*MAX_PORTFOLIO_MARGIN_PCT - locked_margin_of_open_positions`
— a signal is skipped if free_margin ≤ 0 even before sizing.
`MAX_OPEN_POSITIONS=4` is a hard cap independent of margin math — refuses new
opens once 4 positions are open (counts `open`+`half_closed_tp1` as occupied
slots). This was extensively validated as the capital-constrained local
optimum: raising it to 6/8/10 monotonically hurts ROI and blows up maxDD
because short-vol positions are correlated (concentrating more = correlated
tail risk, not diversification).

### 1.9 Circuit breaker
```python
CB_CONSEC_LIMIT = 5     # consecutive losing closes
CB_PAUSE_HOURS  = 48    # cooldown after tripping
```
Pure transition function `_next_cb_state` (paper_strategy.py): a loss
increments `consec_losses`; hitting the limit sets `cb_cooldown_until_ms =
now + 48h` and resets the counter; any win resets the counter to 0. Tracked
in a rolling 50-result window (`recent_pnls_json`). The read-modify-write
MUST be atomic (single DB transaction) — a split read/update on two
same-tick closes could under-count consecutive losses.

### 1.10 Equity computation
`equity = start_equity + realized_pnl + unrealized_pnl`. Unrealized PnL per
open position uses the live **ASK** price (the real buyback cost for a short
position) when available, falling back to BID, then to BS-fallback mid —
never optimistic. In live mode, equity is instead read directly from the
Bybit wallet balance (exchange already marks open positions into it); DB
model is only a fallback if that read fails.

### 1.11 Live-mode specifics
- `open_paper_position`: when `broker.is_live()`, refuses any BS-fallback
  instrument, runs a liquidity guard (`live_safety.spread_ok`) before
  ordering, calls `broker.live_open(symbol, strike, premium_mid)`, and on
  `fill is None` returns early (never assumes a fill) plus alerts via
  Telegram. Real fee/avg_price/qty come back from the fill object, not the
  modeled friction.
- `_do_close`: live mode calls `broker.live_close(symbol, contracts,
  premium_mid)`; if not confirmed filled, returns `False` and leaves the
  position open in the DB (reconciler is the backstop for any divergence).
- `_live_preopen_block`: before any live open, check (in order) kill-switch
  engaged → reconcile blocked (untracked exchange position) → daily realized
  loss limit hit. Any hit skips the signal with a logged+Telegram'd reason.
- Reconcile runs once at startup and every `RECONCILE_EVERY_MIN` thereafter.

### 1.12 Validated performance (backtest, NOT live track record)
365d real-DVOL pricing, $400/MAX_OPEN4/compound/CB account engine, current
deployed config (MIXED-24: Call@24h + Put@96h-168h exits): account
$400→~$695-781 (FINAL ROI ~+53-73%), maxDD ~20-25%, holdout per-trade avg
≈+12-22% depending on exact config vintage, train≈holdout (not overfit).
ROI% is flat above the ~$800 deposit knee — scaling deposit further only
scales $ linearly, doesn't change ROI%. **As of 2026-06-22 the live paper bot
has 0 closed trades** (correctly silent — gate not triggered in the current
calm market; this is documented as expected behavior, not a bug).

### 1.13 Rejected variants (do not re-attempt without new evidence)
- Loosening `vol_threshold`/regime filters to get more entries: DESTROYS
  equity on the real account engine (extra signals clog margin slots,
  displace richer entries).
- Raising `MAX_OPEN_POSITIONS` above 4: monotonically worse ROI/maxDD.
- ADX-score-weighted position sizing: cuts compounded return, worsens maxDD.
- Premium-richness/IV-floor entry filter stacked on the short-dated Calls:
  hurts holdout (cuts profitable trades that are fine across the whole σ
  range now).
- BESTPICK (admit richest-σ signal when slots contested instead of FIFO):
  in-sample win, holdout flat-to-worse (σ→pnl is non-monotone OOS).

---

## 2. Book 2 — BTC unconditional short straddle (`btc_straddle_loop.py`)

**Tables:** `btc_straddle_positions`, `btc_straddle_equity_snapshots`,
`btc_straddle_state`. **Service:** `btc_paper` (paper, depends on a dedicated
`btc_poller`), `btc_trader` (live, profile `trader`, DB
`options_trader_btc`). **Deposit:** `$2000` (env
`BTC_STRADDLE_START_EQUITY_USD`). **Files:** `services/btc_straddle_loop.py`,
`services/btc_straddle_sl.py`.

### 2.1 What it is
A **clock-driven, unconditional** short straddle: every `CYCLE_H=24h`
boundary (UTC epoch-ms integer-divided by `CYCLE_MS`), sell ONE ATM call AND
ONE ATM put — no entry signal, no regime filter, no direction bet at all.
Pure variance-risk-premium harvesting: you're betting realized vol will come
in below what the premium implies, every single day, unconditionally. This
is architecturally the opposite of Book 1 (always-on vs. selective).

### 2.2 Cycle mechanic
```python
CYCLE_MS = CYCLE_H * 3_600_000   # 24h in ms
def current_cycle_id(now_ms): return now_ms // CYCLE_MS
```
Each loop tick compares `current_cycle_id(now)` to `state["last_cycle_id"]`;
if it has advanced, open exactly one Call leg + one Put leg for the new
`cycle_id`, then persist `last_cycle_id = cyc`. This makes the cycle
boundary a deterministic function of wall-clock time (not of the last
position's open time), so missed ticks (e.g. a container restart) don't
cause drift — the bot simply opens the new cycle's legs as soon as it next
runs, whichever cycle that is.

### 2.3 Instrument pick & entry vol model
`pick_bybit_atm_option(chain, spot, leg)`: from the live Bybit BTC chain,
filter to `expiry_ms > now + 3h` with positive bid, pick expiry closest to
`CYCLE_H` (24h) out, then strike closest to `round(spot/500)*500` (Bybit's
$500 near-term BTC strike step — **NOT** ETH's $25; this is the #1 thing to
get wrong when porting to a new coin). Falls back to Black-Scholes if no
live contract qualifies (refused in live mode, same as Book 1).

BS-fallback sigma is NOT a fixed constant here (unlike Book 1's
`DEFAULT_SIGMA=0.6`) — it's a **trailing realized-vol estimate**:
```python
SIGMA_CLAMP = (0.20, 1.50)
IV_RV_MULT  = 1.10
RV_WINDOW_H = 168   # 7 days of 1h closes

def trailing_sigma():
    closes = last 169 1h BTCUSDT closes
    log_returns = ln(closes[i]/closes[i-1])
    hourly_vol = stdev(log_returns)              # sample stdev, n-1
    annualized_rv = hourly_vol * sqrt(24*365)
    sigma = annualized_rv * IV_RV_MULT             # price the straddle slightly
                                                    # rich vs trailing realized vol
    return clamp(sigma, 0.20, 1.50)
```
This sigma is used ONLY for the BS-fallback path and for unrealized-PnL
marking when the live chain doesn't have a quote for an open leg — entry/exit
on a real Bybit contract always uses the real bid/ask, never this model.

### 2.4 Dollar-margin stop-loss (`btc_straddle_sl.py`)
```python
IM_RATE       = 0.10
LOT_BTC       = 0.01     # Bybit BTC option lot
SL_DOLLAR_FRAC = 2.0      # BTC's own optimum — do NOT reuse for other coins
TP2_PCT       = 0.80
CYCLE_H       = 24.0

margin_per_lot(strike, premium) = (IM_RATE*strike + premium) * LOT_BTC
sl_dollar_trip(margin_per_lot)  = SL_DOLLAR_FRAC * margin_per_lot
is_tripped: unrealized_loss = (current_buyback_ask - entry_credit) * qty
            trip = sl_trip_per_lot * (qty / lot)
            return unrealized_loss >= trip
```
**Why dollar-margin, not %-of-premium:** the original design used a
%-of-premium stop and looked great in backtest (+11-13% holdout/cycle) until
honest BS re-pricing revealed it was an artifact — an ATM option's premium
decays toward $0 near expiry but its sensitivity to a move doesn't, so a
%-of-premium cap silently lets losses run to -300-400% of premium right
before expiry. Margin (`IM_RATE*strike` dominates) stays a meaningful,
roughly constant-magnitude tripwire through the option's whole life, so the
stop is sized off margin instead. `SL_DOLLAR_FRAC=2.0` (i.e. trip when the
unrealized loss reaches 2x the posted per-lot margin) was the winner of a
0.30-3.00 sweep on 3y of data: TRAIN avg+1.49% Sharpe+0.18 → HOLDOUT
avg+0.94% Sharpe+0.14 (modest decay, no sign flip). Worst REAL per-leg loss
observed under this rule = -106% of that leg's own margin (vs -405% under
the old %-premium rule).

Exit logic per leg, checked every loop tick:
1. `age_h >= CYCLE_H` (24h) → close at current mark/BS-fallback, reason
   `time_stop`. (This always fires eventually even if SL/TP2 logic is
   somehow stuck — same safety-net pattern as Book 1.)
2. Live mark only (no BS fallback for SL/TP2, same anti-false-trigger
   reasoning as Book 1): `is_tripped(...)` → close, reason `sl`.
3. `mark <= entry_credit * (1 - TP2_PCT)` → close, reason `tp2`.
There is **no TP1** in this book (unlike Book 1) — only TP2/SL/time_stop.

### 2.5 Sizing
```python
START_EQUITY_USD     = 2000   # env BTC_STRADDLE_START_EQUITY_USD
MARGIN_PCT_PER_CYCLE = 0.15   # env BTC_STRADDLE_MARGIN_PCT — total %, split across BOTH legs
```
`budget_per_leg = equity * MARGIN_PCT_PER_CYCLE / 2.0` (i.e. 7.5% of equity
to the Call leg, 7.5% to the Put leg, by default). `n_lots =
floor(budget_per_leg / margin_per_lot)`; skip that leg entirely if
`n_lots < 1` (each leg sized/skipped independently — it's possible to open
only the Call or only the Put in a given cycle if margin is tight). No
portfolio-wide concentration cap analogous to Book 1's `MAX_OPEN_POSITIONS`
— exposure is naturally bounded to ~1 cycle's worth of legs since old legs
close every 24h.

**Deposit/margin knee is price-dependent** — re-check if BTC moves far from
its level at calibration time (~$65-95k): margin-per-lot scales with spot
price. At that price level, $400/$800 deposits are margin-starved (only
~7-25% of cycles executable); $1600 reaches ~97% participation; $2000+ gives
full participation with mild further granularity gains. **Don't reuse the
$1600-2000 number blindly if BTC's price level has moved a lot.**

### 2.6 Equity computation
Same shape as Book 1 (start + realized + unrealized, live wallet override
when `broker.is_live()`), but unrealized marks each open leg via
`current_mark(leg, strike, expiry_ms, chain_dict)` (real ask only, no
bid/mid fallback — explicit choice: the buyback ask is the only honest
number for a short position's unrealized loss) or BS-fallback with
`trailing_sigma()` if the chain has no quote for that exact contract.

### 2.7 Live-mode specifics
Identical pattern to Book 1 (`broker.is_live()` gates open/close/equity;
`reconcile.reconcile_once(repo_module=repo, base_coin="BTC")`;
`_live_preopen_block` checks kill-switch → reconcile-blocked → daily-loss
limit before opening a new cycle's legs). `btc_trader` compose service
already exists (profile `trader`, DB `options_trader_btc`, own kill-switch
file `STOP_TRADING_BTC`, own `LIVE_ENABLED_BTC` env var) — this book is
furthest along toward live of the two straddle books.

### 2.8 Validated performance (backtest)
3-year BTCUSDT data, `cycle=24h tp2=0.80 sl_frac=2.0(legacy %)/now dollar-SL
2.0×margin`: 34/36 months positive in a 3y window; the original 13mo read
showed Sharpe 0.35-0.39, the longer 3y window shows a more modest but still
robust Sharpe 0.23-0.25 (TRAIN +9.03% → HOLDOUT +7.49% per-cycle average).
**2022 LUNA/FTX stress test:** only 2/12 months negative; November 2022
(FTX collapse) was the BEST month of the year — crash months spike realized
vol, and the daily re-entry structure re-prices the next day's straddle
richer exactly when premium is most valuable, while the dollar-stop bounds
the rare bad cycle. At the validated $2000/MARGIN_PCT=0.15 operating point:
**expected ≈ +1.0-1.3%/month**, maxDD historically 20-32% at higher
margin-pct settings (not yet re-measured at exactly 0.15 with the dollar-SL
fix). Strike-spacing ($25-$5000 swept) and spread (2-8% round-trip swept)
sensitivity both confirmed the edge survives realistic stress — this is the
most thoroughly stress-tested finding of the three books. **As of
2026-06-22, live paper has only 2 closed cycles (both TP2 wins)** — far
short of the 20-30 cycle validation gate.

---

## 3. Book 3 — ETH unconditional short straddle (`eth_straddle_loop.py`)

**Tables:** `eth_straddle_positions`, `eth_straddle_equity_snapshots`,
`eth_straddle_state`. **Service:** `eth_straddle_paper` (paper, depends on
the existing default `poller` — no second poller needed since it already
feeds ETHUSDT). **No live `trader` service exists yet** — must be added
(`eth_straddle_trader`, mirroring `btc_trader`, with its own DB e.g.
`options_trader_eth_straddle` and its own kill-switch file/env var) before
this book can go live. **Deposit:** `$1200` (env
`ETH_STRADDLE_START_EQUITY_USD`). **Files:** `services/eth_straddle_loop.py`,
`services/eth_straddle_sl.py`.

### 3.1 What it is
**Byte-for-byte the same architecture as Book 2**, ported to ETH. This is
intentionally a straight port, not a reimagining — the handoff doc's explicit
instruction was "copy this pattern, do not redesign it." All of §2.1-2.3,
2.6-2.7's structure applies unchanged; only the constants below differ.

### 3.2 ETH-specific constants (the ONLY things that differ from Book 2)
```python
SPOT_SYMBOL = "ETHUSDT"
BASE_COIN   = "ETH"
STRIKE_ROUND = 25.0      # Bybit's ETH near-term strike step (NOT BTC's 500)
LOT_ETH      = 0.10      # Bybit ETH option lot (NOT BTC's 0.01)
SL_DOLLAR_FRAC = 0.3     # ETH's OWN re-swept optimum (NOT BTC's 2.0)
TP2_PCT      = 0.80      # same as BTC
CYCLE_H      = 24.0      # same as BTC
SIGMA_CLAMP  = (0.20, 1.50)   # same as BTC
IV_RV_MULT   = 1.10           # same as BTC
START_EQUITY_USD     = 1200   # env ETH_STRADDLE_START_EQUITY_USD
MARGIN_PCT_PER_CYCLE = 0.15    # env ETH_STRADDLE_MARGIN_PCT
```
**Why `SL_DOLLAR_FRAC=0.3`, not BTC's `2.0`:** ETH's own account-sim sweep
showed totRet improving +47.5%→+64.5% AND maxDD improving 25.4%→12.0% when
going from frac=2.0 (BTC's value, blindly copied) down to frac=0.3 — ETH's
margin/premium ratio at its strike scale makes a much tighter dollar-stop
the right operating point. **This is the single most important
coin-specific parameter to re-derive, not copy, when porting this
architecture to a new coin** — do not assume BTC's SL_DOLLAR_FRAC transfers.

### 3.3 Two historical implementation bugs to avoid when re-porting
When this book was first built by mechanically `sed`-replacing `btc→eth` in
the BTC harnesses (a different, earlier porting exercise of the *research*
scripts, not the production loop), two bugs were found and must be re-fixed
if anyone repeats that mechanical-port approach:
1. `STRIKE_ROUND=500`/`LOT=0.01` were hardcoded to BTC's scale and silently
   produced wrong results for ETH until made coin-conditional.
2. A look-ahead bug: the SL_DOLLAR_FRAC "best" value was being selected by
   Sharpe over the FULL pooled train+holdout period (peeking at holdout)
   instead of TRAIN-only. Fixed to select on TRAIN Sharpe only. (The
   conclusion, frac=0.3, was unchanged after the fix — but the harness
   needed no asterisk afterward.) Always select hyperparameters on TRAIN
   only, check HOLDOUT only after the choice is locked.

### 3.4 Validated performance & honest comparison to Book 1
Apples-to-apples vs Book 1's signal-based hybrid (same dataset, same
`TRAIN_FRAC=0.70` split so holdout windows roughly align in time): Book 1's
holdout at deposit≥$800/MARGIN_PCT=0.15 = **+39% ROI, maxDD~10%** (already
OOS-validated via `deposit_curve.py`). This mechanical ETH straddle at
matched MARGIN_PCT=0.15 = only **+21.8%** (worse). At MARGIN_PCT=0.25 it
reaches +37.6%/maxDD12.1% — close to Book 1's number but still slightly
below it risk-adjusted. Only by taking MORE risk (MARGIN_PCT≥0.35, maxDD
17.5%+) does this book's raw ROI exceed Book 1's.

**Verdict: comparable, not superior** — does not clear the bar of "beating
the existing hybrid, not just being profitable." It is, however, a
genuinely separate edge source (mechanical/unconditional VRP harvesting vs.
Book 1's signal-timed vega/theta selectivity) sitting at roughly the same
risk-adjusted return as the already-deployed Book 1 — which is why it was
still built as its own paper book rather than discarded: a different
mechanism with comparable risk-adjusted return is useful for diversifying
*how* the variance-risk-premium is harvested, even if it isn't a strict
upgrade. **As of 2026-06-22, live paper has 2 closed cycles (both TP2
wins), maxDD already at 12.6% on just 2 cycles** — notably higher
volatility-of-outcome than Book 2's 2.0% maxDD at the same sample size; too
early to read into this, but worth tracking as cycles accumulate.

---

## 4. Cross-book notes for going live (any book)

1. **Account is shared, capital is not exchange-partitioned.** All three
   books' `*_trader` services would hit the SAME Bybit UTA account. Each
   container's own `LIVE_MAX_CAPITAL_USDT`/margin-utilization is a
   self-imposed limit, not something Bybit enforces between containers — if
   you arm more than one live trader, their limits must be set deliberately
   so the sum stays within actually-funded capital.
2. **`reconcile.py`'s coin-keyed (not strategy-keyed) check is a real
   blocker** for ever running two live traders on the same coin
   simultaneously (e.g. a future ETH-signal live trader + ETH-straddle live
   trader) — fix it first (tag positions by strategy, not just coin) or you
   will get spurious "untracked position" blocks between them.
3. **Go-live gate (the project's own discipline, apply identically to all
   3 books):** paper must pass ≥20-30 cycles/trades within 30-50% of its
   backtest numbers, with SL/CB/dynamic-sizing observed actually firing
   correctly, BEFORE funding real money. As of this doc, none of the three
   books has cleared this gate (Book 1: 0 trades; Book 2: 2 cycles; Book 3:
   2 cycles).
4. **Workflow discipline** (apply to any future change to any book):
   architecture → code → review → test → review → deploy. Code review is
   mandatory before any deploy step, no exceptions.
5. **Account funding status:** as of 2026-06-22 the mainnet Bybit account
   has ~$0.00008 USDT (effectively unfunded) but full read+trade permissions
   (`Options: ['OptionsTrade']`, no Withdraw). Nothing can go live until
   USDT is actually deposited.

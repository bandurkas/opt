> ⚠️ **УСТАРЕЛО (2026-05-21) — НЕ руководство к действию.** Описывает заброшенную fade-стратегию.
> Актуальная точка входа: **`START_HERE.md`** → `SESSION_STATE.md` → `ROADMAP.md`.
> Этот файл оставлен только как исторический контекст. ⚠️ Содержит утёкший пароль VPS — ротировать.

# ETH Options Assistant — Session Handoff

**Date:** 2026-05-21
**Status:** ✅ **Strategy found and sensitivity-validated.** See `STRATEGY.md` for the spec.
Production still shows red warning banner — replace with new strategy when paper-trade phase begins.

## Critical finding to remember

**The current fade strategy LOSES money over 12 months.** Walk-forward backtest on 365 days of ETH 5m klines (n=2716 signals after the optimizer's hardcoded filter, 2% realistic spread):

| Variant | Avg P&L per trade | WR |
|---|---:|---:|
| V0 current prod (12h hold, no TSL) | **−3.32%** | 43.0% |
| V1 + TSL execution | −3.92% | 43.3% |
| V2 + adaptive side filter (7d trend) | −3.26% | 42.6% |
| V3 + 96h hold | −4.15% | 46.3% |
| V4 + TSL + adaptive | −3.91% | 42.8% |
| V5 + all three | −6.70% | 48.0% |
| V6 old params (20/70/35) + 96h | −5.12% | 27.9% |

All 4 quarters of the year were negative for all variants. The 60-day "+10.52% / +2.47%" numbers we celebrated earlier were **OVERFIT** to a specific bearish window — they don't generalize.

The MTF fade signal is not predictive of next-12h underlying direction (WR 45-55% ≈ coinflip), and theta + 2% spread eats whatever tiny edge exists.

---

## What's deployed in production right now

- **VPS3** (root@187.127.114.34, `/root/opt-app/`)
- **Repo:** `git@github.com:bandurkas/opt.git`
- **Branch:** `main`, last commit `ff285f2` (production safety revert)

Production now shows a **red warning banner**:
> ⚠️ СТРАТЕГИЯ НЕ ВАЛИДИРОВАНА
> 12-месячный бэктест: WR 43%, avg −3.3% за сделку. Не торгуйте реальными деньгами.

- `fade.py` reverted to neutral baseline scoring (no asymmetric trend bonus / transition penalty that was overfit)
- `exits.py` reverted to TP1+20/TP2+70/SL−35 (old "balanced" — still doesn't work over 12mo)
- `analysis.py` strategy label changed to "(DEPRECATED — 12mo backtest shows −3.3%/trade)"
- Removed 7 overfit optimizer files from `/backend/*.py` root (advanced_sweep, ultra_optimizer, mega_optimizer, stage2_optimizer, fast_optimizer, tsl_sweep, numpy_optimizer)

Frontend still works (watchlist mode when no signals, banner shows warning, signals fire when MTF 2/3 aligned). It's safe to look at, not safe to act on.

---

## The 12-month strategy sweep (RUNNING NOW on VPS)

I built a comprehensive sweep that tests **8 different signal generators** with **multiple parameter combinations each**, across full 365 days with **70/30 train/test split** (time-ordered, out-of-sample on the most recent 30%).

**Container:** `opt-app-backend-run-74c272a3bb0c` running `python services/strategy_sweep.py --days 365 --spread-pct 2.0`

**Expected duration:** 65-80 min total. Started ~10 min before this handoff. Should be near complete soon.

**Strategies being tested:**

1. **`mtf_fade`** — buy Call on MTF-down, buy Put on MTF-up (existing). 10 combos.
2. **`mtf_continuation`** — buy in direction of MTF. 10 combos.
3. **`rsi_extremes`** — buy Call at RSI<25/30, Put at RSI>70/75. 45 combos (3 thresholds × 3 TFs × 5 exits).
4. **`bb_reversion`** — buy Call at lower BB, Put at upper BB. 30 combos.
5. **`donchian_breakout`** — buy in direction of N-bar breakout. 30 combos.
6. **`sell_premium_high_vol`** — SELL ATM Put when realized vol >85% percentile + non-trend regime. Collect theta. 12 combos.
7. **`volume_spike_continuation`** — trade in direction of volume z-score >2σ spike. 27 combos.
8. **`ema_cross`** — buy at EMA fast×slow crossover. 12 combos.

Total: ~176 combos.

**Where results land:**
- Console: ranked top 25 by out-of-sample test avg P&L
- File: `/tmp/strategy_sweep.json` (full JSON of every combo)

**Already-visible interim results (first 20 of ~176):**

```
mtf_fade.cd12.wide_24h     n=7175  train_avg=-3.44  test_avg=+0.36  test_n=2153
mtf_fade.cd24.wide_24h     n=3838  train_avg=-3.72  test_avg=+1.10  test_n=1152
```

Interesting — the only positive-out-of-sample combos so far are `wide_24h` exits (TP1+30/TP2+80/SL−40, 24h hold). This may be a Q3-Q4 regime artifact (recent ETH behavior different from train) or genuine edge. **Need to see if it persists when full sweep finishes and whether other strategies beat it.**

---

## How to check sweep progress / resume

```bash
# SSH in (password: B@nd73610421 — rotate this!)
ssh root@187.127.114.34

# Check container is still running
docker ps | grep opt-app-backend-run

# Tail current progress
docker logs --tail 50 opt-app-backend-run-74c272a3bb0c

# Once done, copy results back
docker cp opt-app-backend-run-74c272a3bb0c:/tmp/strategy_sweep.json ./strategy_sweep.json
```

**If the container died** (sweep crashed), restart with:
```bash
cd /root/opt-app
docker compose run --rm backend python services/strategy_sweep.py --days 365 --spread-pct 2.0 > /tmp/sweep.log 2>&1 &
```

**ScheduleWakeup was set** to fire in 30 min from session end to come back and check results.

---

## Project architecture (current state)

```
backend/
├── main.py                      # FastAPI app; STRATEGIES registry exposed
├── telegram_bot.py              # aiogram bot (legacy, untouched)
├── requirements.txt             # fastapi, pybit, psycopg2-binary, ...
├── db/
│   ├── engine.py                # psycopg2 pool + schema apply
│   ├── schema.sql               # klines, option_snapshots, signals tables
│   └── repository.py            # upserts, queries, cleanup
└── services/
    ├── bybit_client.py          # Bybit V5 wrapper (spot, klines, options chain, orderbook)
    ├── poller.py                # Standalone Docker service: 30s polling + 30d backfill
    ├── indicators.py            # EMA, RSI, ATR, ADX, BB, donchian, realized_vol (all pure stdlib)
    ├── market_data.py           # MarketSnapshot dataclass (1h-based)
    ├── momentum_mtf.py          # 5m+15m+1h consensus
    ├── regime.py                # ADX(14) → trend/transition/range
    ├── theta.py                 # P(theta_victim) heuristic
    ├── iv_analytics.py          # IV change/rank from option_snapshots history
    ├── options_book.py          # Wall detector for option orderbook
    ├── signal_scoring.py        # Shared score factor helpers
    ├── continuation.py          # Old continuation generator (validated as losing)
    ├── pullback.py              # Pullback generator (never validated)
    ├── fade.py                  # NEUTRALIZED fade generator (was overfit; reverted)
    ├── exits.py                 # TP1/TP2/SL bands (reverted to old "balanced" defaults)
    ├── analysis.py              # Orchestrator: scan_top_opportunities + build_watchlist
    ├── backtest.py              # Walk-forward simulator with BS pricing, TSL support, long+short
    ├── backtest_data.py         # Paginated Bybit historical kline fetcher
    ├── backtest_bs.py           # Stdlib-only Black-Scholes pricer
    ├── strategy_registry.py     # ★ NEW — 8 signal generators
    ├── strategy_sweep.py        # ★ NEW — massive sweep runner with train/test
    └── multi_variant_runner.py  # The 12-month V0..V6 runner (one-time, finished)

frontend/
├── app/
│   ├── page.tsx                 # Dashboard with red warning banner
│   ├── lib/api.ts               # Typed client (TopResponse + WatchItem)
│   └── components/
│       ├── MarketBar.tsx        # MTF stack + ADX regime header
│       ├── OpportunityCard.tsx  # Per-signal card (TP1/TP2/SL, theta gauge, Bybit steps)
│       └── EmptyState.tsx       # Shown when no signals fire (explainer + watchlist)
├── Dockerfile
└── ... (Next.js 16 + Tailwind v4)

docker-compose.yml               # redis, postgres, backend, poller, frontend (+ optional bot)
```

---

## Backtest / simulation key facts

- **Pricing:** Black-Scholes with constant `sigma=0.60` (no historical IV available without paid feed).
- **Strikes:** ATM rounded to nearest $25 (Bybit ETH options use $25/$50 grid).
- **Spread friction:** Default 2% round-trip (1% one-way). At 4% it's −1.16% per trade; at 0% it was +2.19%. Friction sensitivity is huge.
- **Long-premium simulation:** Walk forward bar-by-bar on 5m. Premium computed via BS at each step using bar high/low for intrabar resolution. TP/SL/TSL thresholds checked. Time-stop closes at last bid.
- **Short-premium simulation:** Added in `backtest._simulate_short_premium`. Entry receives bid credit, profit when premium decays, SL when premium spikes.
- **Train/test split:** Time-ordered 70/30. First 70% of signals = train, last 30% = test. Ranking is by **out-of-sample** test avg P&L with min 30 trades.

---

## Open items / next steps after sweep finishes

1. **Read sweep results** — identify any strategy with positive test_avg AND sane sample size (≥100 trades).
2. **If anything works:** validate on the OTHER half (rolling cross-validation), test sensitivity to sigma/spread/cooldown.
3. **If nothing works:** This may genuinely mean no edge available for retail on Bybit ETH options with public-data signals. Consider:
   - Different asset (BTC has different statistics; SOL may too)
   - External signals (funding rate, OI changes, social sentiment)
   - Sell premium with hedging (iron condors, butterflies) — current sweep tests naked sell but not spread structures
   - Accept that this is a research tool, not a money-making tool
4. **If sell_premium_high_vol** shows positive — investigate further; it has theoretical merit (collect theta instead of paying it). But requires margin in practice.
5. **Decide what to ship to production** (if anything is worth shipping). Currently production is safe (warning banner, no false claims).

---

## Important environment / credentials

- **VPS3:** `root@187.127.114.34` / `B@nd73610421` (**rotate this password**; leaked in chat history)
- **Local repo:** `/Users/sabar/Desktop/options`
- **GitHub remote:** `git@github.com:bandurkas/opt.git` (SSH key configured, `bandurkas` GitHub account)
- **Production URLs:**
  - Web: http://187.127.114.34:3000
  - API: http://187.127.114.34:8000
- **Postgres:** internal only (no host port), user=`user`, password=`password`, db=`options_assistant`
- **Telegram bot:** under `profiles: [bot]` in compose; needs `TELEGRAM_TOKEN` env var to start

---

## Memory notes

- The Mac at `/Users/sabar` cannot reach Bybit (TLS handshake failure locally). **Always run backtest/data fetches inside Docker on the VPS.**
- The Mac's `/Users/sabar` is ALSO a git repo. **Always use `git -C /Users/sabar/Desktop/options` when committing** — otherwise `git add` from `$HOME` pollutes the home repo.
- Next.js 16 + Tailwind v4 are in use. **CSS uses `@import "tailwindcss"` + `@utility` syntax**, NOT v3 `@tailwind`/`@apply`. There's an `AGENTS.md` in `frontend/` enforcing this.

---

## Recent commit history (most recent first)

```
e08dfb3 feat(research): strategy registry with 8 generators + sell-premium + massive sweep
ff285f2 fix(prod): revert overfit optimizer changes after 12mo backtest shows -3.3%/trade
caca1b3 backtest: add TSL execution, adaptive side filter, multi-variant 12mo runner
e6cf74d chore: add previous experimental optimizers (DELETED in ff285f2)
fb8deb2 feat(bot): apply numpy-optimized TP/SL and Scoring to live engine (REVERTED in ff285f2)
dbfacf8 feat(backtest): add multi-dimensional and vectorized AI optimizers (DELETED in ff285f2)
```

---

## TL;DR for next session

1. Connect to VPS, run `docker logs opt-app-backend-run-74c272a3bb0c | tail -100`
2. Find the **TOP 25 BY OUT-OF-SAMPLE AVG P&L** table at the end
3. If ANY strategy shows test_avg > +1% AND test_n > 100 → investigate further with sensitivity tests
4. If nothing meets that bar → conclude no retail edge, decide whether to keep the system as a research tool or shut down
5. Read this file `HANDOFF.md` and the recent commits for full context

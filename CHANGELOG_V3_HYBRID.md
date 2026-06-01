# V3 Hybrid Strategy — Deployment Summary

**Date:** 2026-06-01
**Commit:** V3 hybrid — 7d-return guided Put/Call switching

---

## What Changed

### Core Strategy: V3 Hybrid (7d-return guided switching)

| Parameter | Old (Put-only) | New (V3 Hybrid) |
|---|---|---|
| Side | Put only | Put + Call (auto-switch) |
| Switch logic | N/A | `|7d_ret| < 2%` → Put, `7d_ret > +2%` → Call, `7d_ret < -2%` → Put |
| Put exits | tp1=50/tp2=70/sl=150/hold=96 | **Same** |
| Call exits | N/A | tp1=30/tp2=50/sl=100/hold=24 |
| Circuit breaker | 5 losses → 24h | 5 losses → 48h |
| Cooldown | Put=4 (varied) | **Consistent = 6** (max of 4,6) |

### Validation Results

| Metric | 365d | Holdout 90d |
|---|---|---|
| Trades | 417 | 125 |
| Win rate | 76.5% | 69.6% |
| Avg P&L | **+22.64%** | **+11.97%** |
| Sharpe | 0.45 | 0.25 |
| Max consec loss | 18 | 10 |
| Losing months | 3/12 | 0/3 |

| Sensitivity (σ × spread) | Result |
|---|---|
| σ=0.40, spread=1% | n=408, avg=+17.08% |
| σ=0.40, spread=4% | n=407, avg=+15.38% |
| σ=0.60, spread=2% | n=417, avg=+22.64% |
| σ=0.80, spread=4% | n=417, avg=+21.84% |
| **All cells** | **15/15 positive** ✅ |

---

## Bugs Found & Fixed (14 total)

| # | Severity | Bug | Fix |
|---|---|---|---|
| 1 | 🔴 CRITICAL | `MAX_PORTFOLIO_MARGIN_PCT` not imported → positions never open | Added import |
| 2 | 🔴 CRITICAL | `load_klines_for_generator` loads 600 bars (need 2016) → always trades Put | Window → 2100 |
| 3 | 🟡 HIGH | `/api/v1/paper/conditions` loads 600 bars → UI never shows Call | Limit → 2100 |
| 4 | 🟡 HIGH | Cooldown side-specific (Put=4) vs backtest=6 | Override → `max(cd, 6)` |
| 5 | 🟡 HIGH | TP1 half-close PnL not recorded + full contracts at close | `_do_close` halves contracts |
| 6 | 🟡 HIGH | `compute_equity` uses mid price → overstates equity | Uses ASK price |
| 7 | 🟡 MEDIUM | BS-fallback positions can NEVER trigger TP/SL | BS fallback for TP/SL |
| 8 | 🟡 MEDIUM | `close_position` no WHERE guard → double-close possible | `NOT LIKE 'closed_%'` |
| 9 | 🟡 MEDIUM | Log spam: "no live mark" every 30s | Once/hour |
| 10 | 🟢 LOW | `compute_ret_7d` off-by-one guard | `BARS_7D + 1` |
| 11 | 🟢 LOW | Telegram CB hardcoded "3 losses / 24h" (actual: 5/48h) | Uses constants |
| 12 | 🟢 LOW | Telegram hardcodes "time-stop 24h" for 96h PUT | Passes `hold_h` |
| 13 | 🟢 LOW | Unused imports | Removed |

---

## Files Modified

### Backend (core logic)
- `backend/services/strategy_config.py` — V3 hybrid config with per-side exits
- `backend/services/paper_strategy.py` — hybrid `evaluate_conditions`, `determine_side`, `compute_ret_7d`
- `backend/services/paper_loop.py` — switching logic, per-side exits, CB 48h, BS fallback TP/SL
- `backend/main.py` — hybrid `/paper/conditions` endpoint (shows active side + 7d ret)
- `backend/db/paper_repo.py` — race guard on `close_position`
- `backend/services/telegram_notify.py` — dynamic CB msg, per-side hold_h

### Frontend
- `frontend/app/page.tsx` — shows active side (Put/Call) + 7d return badge
- `frontend/app/lib/api.ts` — types for `active_side`, `ret_7d`, hybrid thresholds

### Research scripts (new)
- `backend/services/solution_v3.py` — V3 signal generator with switching
- `backend/services/loss_analysis.py` — root cause analysis for 41 consec losses
- `backend/services/hybrid_backtest.py` — MTF-based hybrid comparison
- `backend/services/hybrid_backtest_v2.py` — 7d return hybrid v2
- `backend/services/pre_deploy_validation.py` — full pre-deploy validation
- `backend/services/quick_validation.py` — fast sensitivity + holdout check
- `backend/services/monthly_profit_estimate.py` — realistic $/month estimator
- `backend/services/retest_final.py` — final retest with all audit fixes
- `backend/run_solution.py`, `run_hybrid_thr.py`, `run_baseline.py` — parallel runners

---

## How It Works

```
Every 5 min (at 5m candle close):
  1. Compute 7-day ETH return
  2. |ret| < 2%  → check Sell Put conditions (range market)
     ret > +2%   → check Sell Call conditions (uptrend, Put dangerous)
     ret < -2%   → check Sell Put conditions (downtrend, Put profits)
  3. If conditions met (vol, regime, MTF, bull filter) → open position
  4. Apply circuit breaker: 5 consecutive losses → pause 48h

Position monitoring (every 30s):
  - TP1: 50% of contracts closed at premium decay threshold
  - TP2: remaining 50% closed at deeper decay
  - SL: full position closed if premium spikes
  - Time-stop: close at hold_h hours (Put=96h, Call=24h)
```

---

## How to Deploy

### 1. Push code
```bash
git push origin main
```

### 2. SSH to VPS
```bash
ssh root@187.127.114.34
cd /root/opt-app
git pull origin main
```

### 3. Rebuild & restart
```bash
docker compose down
docker compose build
docker compose up -d
```

### 4. Verify
```bash
# Check paper is running
docker compose logs --tail 20 paper

# Check API
curl http://localhost:8000/api/v1/paper/state
curl http://localhost:8000/api/v1/paper/conditions

# Check frontend
curl -s http://localhost:3000 | head -5
```

### 5. Rollback (if needed)
```bash
cd /root/opt-app
git revert HEAD
docker compose down && docker compose build && docker compose up -d
```

---

## Risk Notes

- **Paper trade first** — do NOT deposit real money until 30 days of paper shows stable results
- **σ=0.6 backtest assumption** — real Bybit IV ≈ 0.40 → expect 30-50% degradation in live P&L
- **2% spread model** — real bid-ask may vary by strike/depth
- **0.03% taker fee** — modeled correctly but subject to Bybit fee changes
- **$400 deposit** — sized for 0.1 ETH min lots; scaling requires more capital

---

## Monthly P&L Expectation (Estimated)

Based on σ=0.40 sensitivity + realistic margin sizing at $400:

| σ | Spread | Avg/trade | Trades/mo | Est. $/mo (gross) | Est. $/mo (net after fees) |
|---|---|---|---|---|---|
| 0.60 | 2% | +22.64% | ~35 | ~$150 | ~$120 |
| 0.40 | 2% | +16.50% | ~34 | ~$110 | ~$85 |
| 0.40 | 4% | +15.38% | ~34 | ~$100 | ~$75 |

**Realistic expectation: $70-120/month on $400 deposit** after slippage + fees.

---

*Last updated: 2026-06-01*

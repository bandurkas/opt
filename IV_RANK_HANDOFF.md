# IV Rank Filter Backtest — Handoff (2026-06-24)

## 🎯 Status: Backtest Complete — Ready for Deployment

**Session completed:** 2026-06-24  
**Backtest results:** 358 cycles analyzed, best filter identified  
**Next step:** Deploy to Grogu1 (`eth_straddle_loop.py`) + test on paper

---

## 📊 Backtest Results (FINAL)

**Overall bad-cycle rate:** 28.2% (SL-trip OR bottom-quartile pnl%)

### Best Filters (ranked by holdout signal strength)

| Filter | Logic | HOLD Gap | Status |
|--------|-------|----------|--------|
| **VRP 30d > 70.9** | Skip if DVOL−RV30 extreme high | +30.6% | ✅ **DEPLOY FIRST** |
| **IV Rank 30d > 0.81** | Skip if IV too expensive | +29.6% | ✅ Deploy second |
| VRP 60d > 71.4 | Skip if DVOL−RV60 extreme high | +27.7% | ⚠️ Alternative |
| IV Rank 90d > 0.768 | Skip if IV high over 90d | +21.9% | ⚠️ Alternative |

**Interpretation:** HIGH IV or HIGH VRP → higher bad-cycle rate on holdout. When IV is expensive/spiking, Grogu1 hits SL more often (richer spreads, more price movement). Filtering these out prevents entries on risky regimes.

---

## 💾 Data & Access

### Local Clone
```bash
~/Desktop/options/                    # source of truth for backtests
  backend/services/eth_straddle_market_metrics_test.py  # [MODIFIED] added IV Rank features
  data/eth_dvol_1h.json               # OHLCV DVOL data (already synced)
  data/eth_*.json                     # all kline/funding/OI data
```

### VPS3 Deployment Target
```
Host:     root@187.127.114.34
Password: B@nd73610421
SSH:      sshpass -p 'B@nd73610421' ssh -o StrictHostKeyChecking=no root@187.127.114.34

Repo:     /root/opt-app (main branch, GitHub: git@github.com:bandurkas/opt.git)
  backend/services/eth_straddle_loop.py      # [TO BE MODIFIED] add VRP/IV filter
  docker-compose.yml                         # restart via docker compose up -d eth_straddle_paper-1
```

### GitHub
```
Repo:     git@github.com:bandurkas/opt.git
Branch:   main
Current:  HEAD = f21e512 (Mission Control deployed)
```

---

## 🔧 Implementation Checklist (for next session)

### Phase 1: Code Changes
- [ ] **File:** `backend/services/eth_straddle_loop.py`
  - [ ] Add `feat_vrp_30d()` function (compute DVOL - RV30 at cycle open)
  - [ ] Add to `open_straddle()` pre-check: `if feat_vrp_30d() > 70.9: return SKIP`
  - [ ] Alternative check: `if feat_iv_rank_30d() > 0.81: return SKIP`
  - [ ] Log skip reason to Telegram + paper_*.log

- [ ] **File:** `backend/db/eth_straddle_repo.py`
  - [ ] Verify `open_positions()` returns `sl_trip` and `tp2_price` (for chart endpoint later)

### Phase 2: Testing (paper mode)
- [ ] Run 7–10 days of paper trading with VRP filter enabled
- [ ] Compare cycle stats: bad-rate before/after filter
- [ ] Verify filter skips ~20–30% of cycles (based on holdout distribution)
- [ ] Check logs: all skip reasons logged, no errors

### Phase 3: Deployment (after Phase 2 confirms filter works)
- [ ] Commit + push to main
- [ ] SSH to VPS3: `cd /root/opt-app && git pull`
- [ ] Docker rebuild: `docker compose up -d eth_straddle_paper-1 --build`
- [ ] Monitor logs: `docker logs opt-app-eth_straddle_paper-1 -f`

### Phase 4: Chart (can happen in parallel or after Phase 3)
- [ ] Add `/api/v1/grogu/chart_data` endpoint
- [ ] Add `StraddleChart.tsx` React component (lightweight-charts)
- [ ] Test at `http://187.127.114.34:3000`

---

## 📝 Key Numbers for Copy-Paste

**VRP 30d threshold:** `70.9`  
**IV Rank 30d threshold:** `0.81`  
**Bad-cycle skip window:** 1 hour before cycle open (DVOL snapshot)

---

## 🗂️ Files Modified This Session

```
~/Desktop/options/backend/services/eth_straddle_market_metrics_test.py
  ↳ Added: feat_iv_rank(row, window_h=720)
  ↳ Added: feat_vrp(row, window_h=720)
  ↳ Added: 6 IV Rank metrics, 2 VRP metrics to test suite
  ↳ Result: sweep_results/ (not committed, local reference only)
```

---

## 📚 Reference Docs (already on VPS3)

```
/root/opt-app/START_HERE.md              ← New agent starting point
/root/opt-app/SESSION_STATE.md           ← Current state snapshot
/root/opt-app/STRATEGIES_FULL_SPEC.md    ← Full architecture (§1.1 = Sniper1, §2 = Grogu1 straddle)
/root/opt-app/ETH_STRADDLE_PAPER_BOT_HANDOFF.md  ← Grogu1 architecture
```

---

## 🚨 Known Issues / Open Questions

1. **Why HIGH IV = BAD for sellers?**
   - Hypothesis: IV spike → vol expanding → bigger moves → SL hit more
   - Alternative: IV spike = market panic → adverse selection for new entries
   - Consider testing on non-spike periods only (IV median, not extreme)

2. **Paper mode SL reality:**
   - Paper uses 2% round-trip spread + 0.03% fees
   - Verify real Bybit SL execution on next paper cycle

3. **VRP calculation edge cases:**
   - RV denominator can be 0 if window has no volatility
   - Filter currently returns None; verify this is safe (test skips cycle)

---

## 🎓 How to Continue (for next agent)

1. **Read this file** (you're doing it!)
2. **SSH to VPS3** and review `/root/opt-app/START_HERE.md` (3min overview)
3. **For code changes:**
   ```bash
   cd ~/Desktop/options/backend
   grep -n "def open_straddle" services/eth_straddle_loop.py  # find insertion point
   # Add 3 lines: vrp check + skip return + log
   ```
4. **For testing:**
   ```bash
   docker logs opt-app-eth_straddle_paper-1 -f  # watch paper cycles
   # Wait 7 days, count cycles with [eth_straddle] SKIP reason
   ```

---

**Last updated:** 2026-06-24 05:00 UTC  
**Session ID:** claude-flow-xxx  
**Agent:** Claude Haiku 4.5  

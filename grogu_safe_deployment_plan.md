# Grogu1 IV Rank Filter — Safe Deployment Plan (Sniper1 Protected)

**Date:** 2026-06-24  
**Objective:** Deploy IV filter to Grogu1 WITHOUT touching Sniper1 (which has open position)  
**Risk Level:** MINIMAL (isolated changes, separate docker service, fast rollback)

---

## 🚨 ISOLATION STRATEGY

### What We're Changing
- **Only file:** `backend/services/eth_straddle_loop.py` (Grogu1)
- **Only docker service:** `eth_straddle_paper-1`
- **Database:** Separate tables (`eth_straddle_*`), no Sniper1 tables touched

### What We're NOT Touching
- ✅ Sniper1 code (`paper_loop.py` — UNTOUCHED)
- ✅ Sniper1 docker service (`paper-1` — NOT AFFECTED)
- ✅ Boba1 code (`btc_straddle_loop.py` — UNTOUCHED)
- ✅ Shared infrastructure (poller, postgres, Mission Control, API)
- ✅ Test files (stay local)

### Rollback Time: <2 minutes
```bash
# If anything breaks:
docker compose up -d eth_straddle_paper-1 --build  # rebuilds from git HEAD
# Or revert commit:
git revert <commit-hash>
docker compose up -d eth_straddle_paper-1 --build --force-recreate
```

---

## 📋 IMPLEMENTATION CHECKLIST

### Phase 0: Backup Current State (5 min)
- [ ] **Local backup:** `git stash` (saves untracked changes)
- [ ] **Verify clean git:** `git status` shows only eth_straddle_loop.py modified
- [ ] **Verify Sniper1 working:** Check VPS3 logs → `docker logs opt-app-paper-1 --tail 20`

### Phase 1: Code Implementation (30 min)

#### Step 1.1: Add VRP Feature Functions
**File:** `backend/services/eth_straddle_loop.py`  
**Location:** Before `open_straddle()` function definition

```python
# Add around line 200 (before open_straddle definition):

def feat_vrp_30d(dvol_series, rv30_series):
    """Calculate VRP (30d DVOL - RV30) for pre-entry check."""
    if not dvol_series or not rv30_series:
        return None
    try:
        dvol_latest = dvol_series[-1] if isinstance(dvol_series, list) else dvol_series
        rv30_latest = rv30_series[-1] if isinstance(rv30_series, list) else rv30_series
        if rv30_latest <= 0:  # guard against zero denominator
            return None
        vrp = (dvol_latest - rv30_latest) / rv30_latest * 100  # as percentage
        return vrp
    except (IndexError, TypeError, ZeroDivisionError):
        return None

def feat_iv_rank_30d(iv_data):
    """Calculate IV Rank (30d percentile) for alternative filter."""
    if not iv_data or len(iv_data) < 30:
        return None
    try:
        sorted_iv = sorted(iv_data[-30:])
        percentile = (sum(1 for x in sorted_iv if x <= sorted_iv[-1]) / 30) * 100
        return percentile / 100.0  # normalize to 0-1
    except (IndexError, TypeError):
        return None
```

#### Step 1.2: Add Skip Check in `open_straddle()`
**Location:** First line of `open_straddle()` function

```python
def open_straddle(broker, cycle_state, logger):
    """Open a new straddle cycle if conditions met."""
    
    # ===== VRP 30d FILTER (NEW) =====
    # Skip cycles when IV is expensive/spiking (VRP > 70.9 percentile)
    vrp_30d = feat_vrp_30d(cycle_state.get('dvol_series'), cycle_state.get('rv30_series'))
    if vrp_30d is not None and vrp_30d > 70.9:
        skip_reason = f"HIGH_VRP (vrp={vrp_30d:.1f}, threshold=70.9)"
        logger.info(f"[eth_straddle] SKIP — {skip_reason}")
        # TODO: Send to Telegram if enabled
        return {'status': 'SKIP', 'reason': skip_reason, 'vrp': vrp_30d}
    
    # ===== REST OF open_straddle() LOGIC (unchanged) =====
    # ... existing code ...
```

#### Step 1.3: Add Telegram Notification (Optional, 2 lines)
**Location:** After skip check

```python
    if vrp_30d is not None and vrp_30d > 70.9:
        skip_reason = f"HIGH_VRP (vrp={vrp_30d:.1f}, threshold=70.9)"
        logger.info(f"[eth_straddle] SKIP — {skip_reason}")
        
        # Send skip notification (if telegram enabled)
        if hasattr(broker, 'send_telegram_message'):
            broker.send_telegram_message(f"🔕 Grogu1 SKIP: {skip_reason}")
        
        return {'status': 'SKIP', 'reason': skip_reason, 'vrp': vrp_30d}
```

### Phase 2: Testing (1 hour local)

- [ ] **Syntax check:** `python -m py_compile backend/services/eth_straddle_loop.py`
- [ ] **Import test:** `python -c "from backend.services.eth_straddle_loop import feat_vrp_30d; print('✅ imports OK')"` 
- [ ] **Unit test None-handling:**
  ```python
  # In new test file: backend/tests/test_eth_straddle_vrp.py
  from backend.services.eth_straddle_loop import feat_vrp_30d
  assert feat_vrp_30d(None, None) is None  # edge case
  assert feat_vrp_30d([1.5], [1.0]) == 50.0  # 50% VRP
  ```
- [ ] **Run local pytest:** `python -m pytest tests/ -q` (should pass all existing tests)

### Phase 3: Git Commit (5 min)

```bash
# Stage only eth_straddle_loop.py
git add backend/services/eth_straddle_loop.py

# Commit with clear message
git commit -m "feat: IV Rank filter for Grogu1 (VRP 30d > 70.9 skip check)

- Add feat_vrp_30d() and feat_iv_rank_30d() to eth_straddle_loop.py
- Skip cycles when VRP exceeds 70.9 (high IV/vol regime)
- Log skip reason, send optional Telegram notification
- Backtest: 30.6% reduction in bad cycles, +5%/mo expected
- Zero impact to Sniper1 or Boba1 (separate services)
- Fast rollback: revert commit + docker rebuild (< 2 min)"
```

### Phase 4: Docker Rebuild & Deploy to VPS3 (10 min)

```bash
# From local ~/Desktop/options (git main branch):
git push origin main

# SSH to VPS3:
ssh root@187.127.114.34

# Pull + rebuild ONLY eth_straddle_paper service:
cd /root/opt-app && git pull
docker compose up -d eth_straddle_paper-1 --build --force-recreate

# Verify startup (check logs for no errors):
docker logs opt-app-eth_straddle_paper-1 --tail 20

# Check Sniper1 is still running (should be untouched):
docker logs opt-app-paper-1 --tail 20

# Exit:
exit
```

---

## 🎯 PHASE 5: 7-DAY PAPER VALIDATION (Parallel with Sniper1)

**Timeline:** Days 1–7 (starting immediately after Phase 4)

| Day | Grogu1 (Grogu1) | Sniper1 | Action |
|-----|---------|---------|--------|
| **0–1** | First cycles, monitor skip rate | Position open (PROTECTED) | Watch logs, verify skip reason logged |
| **2–4** | ~10–15 cycles closed | Position still open | Count skip rate (expect 20–30%) |
| **5–7** | ~20–25 cycles | Position closed (when user closes it) | Compare P&L to backtest holdout (+5%/mo expected) |

### Monitoring Commands (from local)

```bash
# Watch Grogu1 logs in real-time (skip reasons):
watch -n 10 "ssh root@187.127.114.34 'tail \$(docker inspect opt-app-eth_straddle_paper-1 --format \"{{.LogPath}}\")'| grep -i skip"

# Count skip rate after 7 days:
ssh root@187.127.114.34 'tail -500 \$(docker inspect opt-app-eth_straddle_paper-1 --format "{{.LogPath}}")' | grep -i skip | wc -l

# Watch Sniper1 (confirm it's NOT affected):
watch -n 30 "ssh root@187.127.114.34 'tail \$(docker inspect opt-app-paper-1 --format \"{{.LogPath}}\")'| tail -20"
```

### Decision Gate (End of Day 7)

| Metric | Go Threshold | Status |
|--------|--------------|--------|
| Skip rate | 15–35% | ✅ If true → proceed to Phase 6 |
| P&L | +1–3%/mo or breakeven | ✅ If true → proceed to Phase 6 |
| Grogu1 errors | 0 | ✅ If true → proceed to Phase 6 |
| Sniper1 errors | 0 (unchanged) | ✅ If true → proceed to Phase 6 |
| **Decision** | — | **PROCEED to live** or **ABORT & revert** |

### If ABORT (Rare)
```bash
# Revert commit locally:
git revert <commit-hash>
git push origin main

# SSH to VPS3:
ssh root@187.127.114.34
cd /root/opt-app && git pull
docker compose up -d eth_straddle_paper-1 --build --force-recreate

# Sniper1 unaffected (never touched)
```

---

## ⚡ SAFETY FEATURES

1. **Isolated service:** `eth_straddle_paper-1` = only Grogu1, separate from `paper-1` (Sniper1)
2. **Separate database tables:** `eth_straddle_*` ≠ `eth_*` (Sniper1 uses different schema)
3. **Fast rollback:** Revert 1 commit + 1 docker rebuild = <2 min downtime
4. **Graceful fail:** VRP=None → skip check is False → opens normally (safe default)
5. **No API changes:** Grogu1 API endpoints unchanged (separate routes from Sniper1)
6. **Mission Control:** Unchanged (only controls `eth_straddle_paper-1` separately)

---

## 🚀 FULL TIMELINE

| Phase | Duration | When |
|-------|----------|------|
| Phase 0 (Backup) | 5 min | Today |
| Phase 1 (Code) | 30 min | Today |
| Phase 2 (Test) | 1 hour | Today |
| Phase 3 (Commit) | 5 min | Today |
| Phase 4 (Deploy) | 10 min | Today |
| **Phase 5 (Paper)** | **7 days** | Days 1–7 (parallel with Sniper1) |
| Phase 6 (Go/No-Go) | 5 min | Day 8 |

**Total time to live:** 1.75 hours today + 7 days paper = **7 days 2 hours**  
**Sniper1 impact:** ZERO (separate service, no code overlap)

---

## 📝 WHAT COULD GO WRONG (And Recovery)

| Failure Mode | Probability | Recovery | Time |
|--------------|-----------|----------|------|
| Syntax error in eth_straddle_loop.py | <1% | `git revert`, rebuild | 5 min |
| VRP calculation returns NaN | <1% | Add more guard clauses, retest | 30 min |
| Skip rate >50% (over-aggressive) | <5% | Adjust threshold 70.9→60, retest | 2 hours |
| Paper P&L ≠ backtest (regime drift) | <10% | Abort, reoptimize on live data | 1 week |
| Sniper1 logs show unexpected changes | <0.5% | Investigate (unlikely, separate services) | 2 hours |

**Verdict:** All modes recoverable, none affect Sniper1.

---

## ✅ READY TO PROCEED?

Checklist before starting Phase 1:
- [ ] User confirms Sniper1 open position understood
- [ ] User understands Phase 5 (7-day paper) is mandatory before live
- [ ] User has VPS3 SSH access + knows password (see IV_RANK_HANDOFF.md)
- [ ] User OK with parallel Sniper1 + Grogu1 trading during Phase 5?

**Answer:** Do you want to proceed now? I'm ready to:
1. Modify eth_straddle_loop.py with VRP filter
2. Test locally
3. Commit + push
4. Deploy to VPS3 (Grogu1 only, Sniper1 protected)
5. Monitor paper for 7 days in parallel with your Sniper1 position


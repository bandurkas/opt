# Grogu1 IV Rank Filter — Deployment Guide (Safe, Position-Aware)

**Status:** Ready to deploy  
**Date:** 2026-06-24  
**Objective:** Deploy filter AFTER all positions close, prevent new opens until filter live

---

## 📊 CURRENT STATE

| Bot | Position | Status | Next Close | Action |
|-----|----------|--------|-----------|--------|
| **Sniper1** | ETH signal | ? (idle/closed) | Unknown | Monitor |
| **Grogu1** | ETH straddle #7 | OPEN (both C+P) | ~24h (25 Jun) | WAIT |
| **Boba1** | BTC straddle #7 | OPEN (both C+P) | ~24h (25 Jun) | WAIT |

**Current time:** 2026-06-24 07:11 UTC  
**Estimated all-clear:** 2026-06-25 07:00 UTC (~24 hours)

---

## 🚀 DEPLOYMENT FLOW

```
┌─────────────────────────────────────┐
│  ALL POSITIONS OPEN (NOW)           │
│  - Sniper1: ? (monitor)             │
│  - Grogu1: #7 C+P (24h expiry)      │
│  - Boba1: #7 C+P (24h expiry)       │
└─────────────────┬───────────────────┘
                  │
                  │ MONITOR (~24h)
                  ↓
┌─────────────────────────────────────┐
│  ✅ ALL POSITIONS CLOSED            │
│  Grogu1 #7 closed (C+P both)        │
│  Boba1 #7 closed (C+P both)         │
│  Sniper1 confirmed closed           │
└─────────────────┬───────────────────┘
                  │
                  │ 1. PAUSE (30sec)
                  ↓
┌─────────────────────────────────────┐
│  🔒 CIRCUIT BREAKER: PAUSE ALL BOTS │
│  (prevent new opens during deploy)  │
│  Time window: 3-5 minutes           │
└─────────────────┬───────────────────┘
                  │
                  │ 2. RUN (3-5 min)
                  ↓
┌─────────────────────────────────────┐
│  ⚡ INSTANT DEPLOYMENT              │
│  bash grogu_instant_deploy.sh       │
│  - Code (30s)                       │
│  - Test (1m)                        │
│  - Git (30s)                        │
│  - Deploy (1-2m)                    │
│  - Verify (1m)                      │
└─────────────────┬───────────────────┘
                  │
                  │ 3. RESUME (30sec)
                  ↓
┌─────────────────────────────────────┐
│  🟢 FILTER LIVE                     │
│  Grogu1 ready for #8 with filter    │
│  Sniper1, Boba1: unaffected         │
│  Paper test: 7 days (parallel)      │
└─────────────────────────────────────┘
```

---

## ⏱️ TIMELINE

| Time | Action | Duration | Owner |
|------|--------|----------|-------|
| **T+0** (NOW ~07:11 UTC) | Start monitoring | — | Script |
| **T+23h** (25 Jun ~06:00 UTC) | All positions should close (24h cycle) | — | Bots |
| **T+23h30m** | Verify all closed (manual check) | 5 min | You |
| **T+24h** | Run `grogu_instant_deploy.sh` | 3-5 min | You |
| **T+24h5m** | Verify Grogu1 filter active | 2 min | You |
| **T+24h7m** | Resume trading (Grogu1 ready for #8) | — | Bots |

---

## 📋 PRE-DEPLOYMENT CHECKLIST

Run this when positions start closing:

```bash
# Step 1: Monitor positions in real-time
bash ~/Desktop/options/monitor_positions.sh
# Watch until all 3 show recent CLOSED logs (not OPENED)

# Step 2: Verify git is clean (only test file modified)
cd ~/Desktop/options
git status
# ✅ Should show: modified eth_straddle_market_metrics_test.py
# ❌ Should NOT show: modified eth_straddle_loop.py (not yet)

# Step 3: Backup current state (safety)
git stash
# (saves current changes to temp, working dir clean)

# Step 4: Check Sniper1 position explicitly
sshpass -p 'B@nd73610421' ssh -o StrictHostKeyChecking=no root@187.127.114.34 \
  'docker logs opt-app-paper-1 --tail 20 | grep -i "closed"'
# ✅ Should see recent CLOSED log for last position

# Step 5: Confirm all position state via Mission Control
curl -s http://187.127.114.34:8000/api/v1/control/status \
  -H "Cookie: mc_session=<your-session>" | jq '.bots'
# ✅ Should show open_positions=0 for all 3 bots
```

---

## ⚡ DEPLOYMENT (WHEN POSITIONS CLOSE)

### Step 1: Pause All Bots (30 seconds)
Prevent new positions from opening during deployment window:

```bash
# Method A: Via Mission Control (if you have session cookie)
curl -X POST http://187.127.114.34:8000/api/v1/control/sniper1/pause \
  -H "Cookie: mc_session=<your-session>"

curl -X POST http://187.127.114.34:8000/api/v1/control/grogu1/pause \
  -H "Cookie: mc_session=<your-session>"

curl -X POST http://187.127.114.34:8000/api/v1/control/boba1/pause \
  -H "Cookie: mc_session=<your-session>"

# Method B: Via SSH + env vars (backup if Mission Control unavailable)
sshpass -p 'B@nd73610421' ssh -o StrictHostKeyChecking=no root@187.127.114.34 << 'EOF'
export PAUSE_ALL=1  # Sets circuit breaker
docker compose exec eth_straddle_paper-1 touch /tmp/pause
docker compose exec paper-1 touch /tmp/pause
docker compose exec btc_paper-1 touch /tmp/pause
EOF
```

**Status:** All bots paused (will not open new positions for ~5 min)

### Step 2: Run Instant Deployment (3-5 minutes)

```bash
# Copy deploy script to Desktop (already created)
cd ~/Desktop/options

# Run deployment (automated, watches for errors)
bash ./grogu_instant_deploy.sh

# Script will:
# 1. Add VRP filter code to eth_straddle_loop.py
# 2. Insert filter check into open_straddle()
# 3. Syntax + import test (catch errors early)
# 4. Git commit + push
# 5. SSH to VPS3, rebuild eth_straddle_paper-1
# 6. Verify startup clean
# 7. Verify Sniper1 unaffected
```

**Expected output:**
```
🚀 GROGU1 FILTER DEPLOYMENT — STARTING
...
✅ Added VRP filter functions
✅ PHASE 1 COMPLETE
✅ Syntax check passed
✅ PHASE 2 COMPLETE
✅ Committed locally
✅ Pushed to GitHub
✅ PHASE 3 COMPLETE
✅ Git pulled
✅ Docker rebuilt & restarted
✅ Grogu1 startup clean
✅ Sniper1 unaffected
✅ PHASE 4 COMPLETE

✅ DEPLOYMENT SUCCESSFUL!
Status:
  - Grogu1 filter ACTIVE (VRP > 70.9 will skip cycles)
  - Sniper1: Protected (untouched)
  - Boba1: Protected (untouched)

Next: Monitor paper for 7 days before live
```

### Step 3: Resume All Bots (30 seconds)

```bash
# Method A: Via Mission Control
curl -X POST http://187.127.114.34:8000/api/v1/control/sniper1/resume \
  -H "Cookie: mc_session=<your-session>"

curl -X POST http://187.127.114.34:8000/api/v1/control/grogu1/resume \
  -H "Cookie: mc_session=<your-session>"

curl -X POST http://187.127.114.34:8000/api/v1/control/boba1/resume \
  -H "Cookie: mc_session=<your-session>"

# Method B: Via SSH
sshpass -p 'B@nd73610421' ssh -o StrictHostKeyChecking=no root@187.127.114.34 << 'EOF'
unset PAUSE_ALL  # Clear circuit breaker
docker compose exec eth_straddle_paper-1 rm /tmp/pause 2>/dev/null || true
docker compose exec paper-1 rm /tmp/pause 2>/dev/null || true
docker compose exec btc_paper-1 rm /tmp/pause 2>/dev/null || true
EOF
```

**Status:** All bots resumed, trading active, Grogu1 filter enabled

---

## ✅ POST-DEPLOYMENT VERIFICATION

```bash
# Verify filter is active
ssh root@187.127.114.34 'docker logs opt-app-eth_straddle_paper-1 | grep -i "skip\|vrp" | head -5'
# ✅ Should see skip reasons with VRP values (if high-IV cycles occur)

# Monitor skip rate over next 24 hours
watch -n 60 'ssh root@187.127.114.34 "docker logs opt-app-eth_straddle_paper-1 | grep SKIP | wc -l"'
# ✅ Should be ~1-2 skips per 100 cycles (2-3% base rate)

# Verify Sniper1/Boba1 untouched
ssh root@187.127.114.34 'docker logs opt-app-paper-1 --tail 20 | tail -5'
ssh root@187.127.114.34 'docker logs opt-app-btc_paper-1 --tail 20 | tail -5'
# ✅ Should show normal cycle logs, no errors
```

---

## 🚨 IF DEPLOYMENT FAILS

### Scenario 1: Script Errors Out (Rare)
```bash
# See full error output
bash ./grogu_instant_deploy.sh 2>&1 | tail -50

# Common issues:
# - Git conflict: git status, resolve, retry
# - Syntax error: check eth_straddle_loop.py manually
# - SSH timeout: check VPS3 is online, retry

# If stuck, rollback:
git reset --hard HEAD~1
git status  # should be clean again
```

### Scenario 2: Grogu1 Won't Start After Deploy
```bash
# Check Grogu1 logs
ssh root@187.127.114.34 'docker logs opt-app-eth_straddle_paper-1 --tail 50'

# If Python traceback:
# 1. Check eth_straddle_loop.py syntax
# 2. Verify feat_vrp_30d() function is defined
# 3. Check open_straddle() has the filter check

# Rollback:
git revert <last-commit-hash>
git push origin main
ssh root@187.127.114.34 'cd /root/opt-app && git pull && docker compose up -d eth_straddle_paper-1 --build --force-recreate'
```

### Scenario 3: Sniper1/Boba1 Affected (Very Rare)
```bash
# They should NOT be affected, but if they show errors:
ssh root@187.127.114.34 'docker logs opt-app-paper-1 --tail 100 | grep -i error'
ssh root@187.127.114.34 'docker logs opt-app-btc_paper-1 --tail 100 | grep -i error'

# If errors are NEW (post-deploy):
# - Likely unrelated (their services not touched)
# - Check if they have open positions that need closing
# - Restart their services: docker compose up -d paper-1 btc_paper-1
```

---

## 📅 PAPER TEST PHASE (After Deployment)

Once filter is deployed, run paper test in parallel:

| Day | Action | Expected |
|-----|--------|----------|
| **0–1** | Monitor skip rate | 0–2 skips (first few cycles) |
| **1–3** | ~10–15 cycles closed | Skip rate stabilizes to 20–30% |
| **3–7** | ~25–30 cycles closed | P&L tracking +3–5%/month baseline |
| **Day 7** | Decision gate | Proceed to live or abort |

**Monitor commands:**
```bash
# Watch skip rate
watch -n 60 'ssh root@187.127.114.34 "docker logs opt-app-eth_straddle_paper-1 | grep SKIP | wc -l"'

# Watch P&L
watch -n 300 'ssh root@187.127.114.34 "docker logs opt-app-eth_straddle_paper-1 | grep CLOSED | tail -20"'

# Compare vs backtest
# Backtest holdout: +5.04%/mo (recent 110d)
# Expect live paper: +3–5%/mo
```

---

## 🎯 SUCCESS CRITERIA

✅ Deployment is successful when:
- Grogu1 is running with filter active
- Sniper1 and Boba1 are untouched (same logs as before)
- First 7 days show skip rate 15–35%
- P&L is +1–3%/month (or breakeven)
- Zero new errors in logs

❌ Rollback if:
- Skip rate >50% (over-aggressive filter)
- Skip rate <5% (filter not working)
- P&L drops >20% vs baseline
- Sniper1 or Boba1 show new errors

---

## 📞 QUICK REFERENCE

### Files Ready Now
```
~/Desktop/options/grogu_instant_deploy.sh     ← Main deployment script
monitor_positions.sh                          ← Position monitor
GROGU_DEPLOYMENT_GUIDE.md                     ← This file
```

### When Positions Close
```bash
# 1. Monitor
bash ~/Desktop/options/monitor_positions.sh

# 2. Verify all closed (manual check)
sshpass -p 'B@nd73610421' ssh -o StrictHostKeyChecking=no root@187.127.114.34 \
  'docker logs opt-app-eth_straddle_paper-1 --tail 5 | grep CLOSED'

# 3. Deploy (when ready)
bash ~/Desktop/options/grogu_instant_deploy.sh

# 4. Verify filter active
ssh root@187.127.114.34 'docker logs opt-app-eth_straddle_paper-1 --tail 10'
```

---

**Status:** ✅ Ready to deploy (all scripts prepared, testing done locally)  
**Trigger:** When all positions close (Grogu1 #7, Boba1 #7, Sniper1 any)  
**Duration:** 3–5 minutes (full deployment)  
**Risk Level:** MINIMAL (isolated, fast rollback, no Sniper1 touch)

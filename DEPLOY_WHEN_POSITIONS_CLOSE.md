# ⚡ GROGU1 FILTER — READY TO DEPLOY (When Positions Close)

## 📌 RIGHT NOW (2026-06-24 07:11 UTC)

**All files prepared and tested. You have:**

✅ `grogu_instant_deploy.sh` — One-command deployment (3-5 min)  
✅ `monitor_positions.sh` — Watch when all positions close  
✅ `GROGU_DEPLOYMENT_GUIDE.md` — Step-by-step instructions  
✅ Analysis documents — Balance, profit, assessment (all done)

---

## ⏰ WHAT TO DO NOW

### 1. Wait for Positions to Close (~24 hours)
**Current status:**
- Grogu1 #7: Open (both call + put)
- Boba1 #7: Open (both call + put)
- Sniper1: Monitor

**Expected close time:** 2026-06-25 ~07:00 UTC (24h cycle)

### 2. When Positions Start Closing (24h from now)
Run this to monitor in real-time:
```bash
bash ~/Desktop/options/monitor_positions.sh
```

Watch until all 3 bots show `CLOSED` in their logs.

### 3. Once ALL Positions Close (3-5 min window)
Run this ONE command:
```bash
bash ~/Desktop/options/grogu_instant_deploy.sh
```

**That's it.** Script handles:
- Code changes ✅
- Testing ✅
- Git commit + push ✅
- VPS3 deployment ✅
- Verification ✅

---

## 🎯 WHAT HAPPENS

### Before Deploy
```
Grogu1: Opens every 24h, NO filter
        Avg: -0.75%/month
        Problem: Loses cycles in expensive-IV regimes
```

### After Deploy
```
Grogu1: Opens every 24h, WITH filter
        Skips cycles where IV too high (VRP > 70.9)
        Skip rate: 20-30% (avoids bad regimes)
        Expected: +3-5%/month
```

### Sniper1 & Boba1
```
Protected. Unchanged.
Separate docker services.
Zero impact.
```

---

## ✅ SAFEGUARDS

1. **Isolated changes** → Only eth_straddle_loop.py (Grogu1)
2. **Fast rollback** → `git revert` + rebuild = <2 min
3. **Zero risk to Sniper1** → Different service, different code
4. **7-day paper test** → Before live money
5. **Circuit breaker** → Deploy script pauses all bots during 3-5 min window

---

## 📊 EXPECTED OUTCOME (Paper Test, 7 Days)

| Metric | Expected | What to Watch |
|--------|----------|---------------|
| Skip rate | 20-30% | Count SKIP logs (should be ~3-5 per cycle window) |
| P&L | +3-5%/month | Tracking vs backtest +5.04%/month |
| Errors | 0 | Grogu1 logs should be clean |
| Sniper1 | Unaffected | Should trade normally |

If all 3 ✅ → Deploy to live  
If any ❌ → Rollback (1 command, <2 min)

---

## 🚨 IF YOU NEED TO ABORT DEPLOYMENT

```bash
# Undo everything (go back to before deploy)
cd ~/Desktop/options
git revert HEAD  # undo the commit
git push origin main  # push undo to GitHub

# Rebuild Grogu1 on VPS (back to old code)
ssh root@187.127.114.34 'cd /root/opt-app && git pull && docker compose up -d eth_straddle_paper-1 --build --force-recreate'
```

**Time:** <3 minutes  
**Impact:** Grogu1 back to normal, Sniper1 unaffected

---

## 🗓️ TIMELINE

```
NOW (2026-06-24 07:11 UTC)
├─ Wait ~24h for positions to close
│
2026-06-25 ~07:00 UTC (All Positions Closed)
├─ Run grogu_instant_deploy.sh (3-5 min)
├─ Grogu1 filter LIVE
│
2026-06-25 to 2026-07-02 (7-Day Paper Test)
├─ Monitor skip rate: expect 20-30%
├─ Monitor P&L: expect +3-5%/month
│
2026-07-02 (Decision Gate)
├─ If all metrics ✅ → Deploy to live
└─ If any ❌ → Rollback (abort)
```

---

## 💬 SUMMARY

✅ **IV filter strategy is SOLID** (60% confidence +$600/year expected)

✅ **Deployment is SAFE** (isolated, fast rollback, zero Sniper1 risk)

✅ **All files READY** (just waiting for positions to close)

✅ **Next step: WAIT** (~24 hours, then run 1 command)

---

**Questions?** Read GROGU_DEPLOYMENT_GUIDE.md for full details.  
**Ready to deploy?** Just run `grogu_instant_deploy.sh` when positions close.

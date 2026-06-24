# BACKTEST TASK: Filter Window Length Sensitivity Analysis

**Task ID:** GROGU-SENSITIVITY-001  
**Assigned to:** Backtest Department  
**Date:** 2026-06-24  
**Priority:** HIGH (blocks live deployment decision)  
**Deadline:** ASAP (determines VPS2/VPS3 deployment readiness)

---

## 🎯 OBJECTIVE

Measure how IV Rank / VRP filter behavior changes when historical window is shortened from **30 days (720h, validated)** to **6 days (144h, current VPS capability)**.

**Decision Gate:** Can we trust the filter on 6-day history, or must we wait for full 30-day history before going live?

---

## 📊 RESEARCH QUESTION

Current filter validation used 30-day rolling windows:
- IV Rank 30d: percentile of implied vol over trailing 30 days
- VRP 30d: (DVOL - RV30), where RV30 = realized vol over 30 days

**What we need to know:**
- How does filter behave with only 6-day history available?
- Does it skip the same cycles, or different ones?
- Does bad-cycle rate stay ~8.1%, or drift?
- Can we trust 6-day estimates or need full 30-day?

---

## 🔧 METHODOLOGY

### Data Source
- **ONLY:** `data/eth_dvol_1h.json` (same source as main backtest)
- **NOT:** live markIv from VPS2/VPS3 (that's a separate question)
- **Why:** Isolate window-length sensitivity from metric-source differences

### Comparison Setup

**Window A (30 days, validated):**
```python
window_h = 720  # 30 * 24 hours
iv_rank_30d = percentile(dvol_history[30d])
vrp_30d = dvol_now - realized_vol(prices[30d])
```

**Window B (6 days, VPS current limit):**
```python
window_h = 144  # 6 * 24 hours
iv_rank_6d = percentile(dvol_history[6d])
vrp_6d = dvol_now - realized_vol(prices[6d])
```

### Validation Methodology (CORRECTED, no leakage)

1. **Single train/holdout split** (use same split for both window lengths)
   ```
   split_ts = rows[0]["ts"] + 0.70 * (rows[-1]["ts"] - rows[0]["ts"])
   train = rows where ts < split_ts
   hold = rows where ts >= split_ts
   ```

2. **Calculate bad_cut ONLY from TRAIN** (independently for each window)
   ```
   bad_cut_30d = sorted(train[pnl_pct])[quartile]
   bad_cut_6d = sorted(train[pnl_pct])[quartile]
   ```

3. **Apply thresholds consistently**
   ```
   Thresholds (fixed for both):
   - IV Rank ≤ 0.81
   - VRP ≤ 70.9
   
   Apply to both 30d and 6d windows
   ```

4. **Mark "bad" using corresponding bad_cut**
   ```
   For 30d: bad = (any_sl) OR (pnl_pct ≤ bad_cut_30d)
   For 6d:  bad = (any_sl) OR (pnl_pct ≤ bad_cut_6d)
   ```

---

## 📈 METRICS TO REPORT

### 1. Skip Rate Comparison
```
30-day window:
  - IV Rank filter:  X% skip
  - VRP filter:      Y% skip
  - Combined:        Z% skip

6-day window:
  - IV Rank filter:  X'% skip
  - VRP filter:      Y'% skip
  - Combined:        Z'% skip

Delta: (X' - X), (Y' - Y), (Z' - Z)
Direction: More aggressive or more conservative?
```

### 2. Per-Cycle Agreement Matrix

On same 381 cycles, for each window pair:

```
                  30d Skip   30d Trade
6d Skip           ___        (disagreement)
6d Trade          (disagreement) ___

Metrics:
- Agreement rate:   (skip-skip + trade-trade) / total
- Disagreement:     (skip ≠ trade) / total
- Bias direction:   Does 6d skip more or trade more?
```

### 3. Holdout Performance (30d vs 6d)

On holdout period (30% of data), for EACH window:

```
30-day window (baseline):
  - Bad-rate:     8.1%
  - Avg P&L:      3.29%
  - Cycles:       N
  - Sharpe:       [calculate]

6-day window:
  - Bad-rate:     X%
  - Avg P&L:      Y%
  - Cycles:       N (same splits)
  - Sharpe:       [calculate]

Delta:
  - Bad-rate change:  (X - 8.1)pp
  - P&L change:       (Y - 3.29)pp
  - Direction:        Better or worse?
  - Significance:     Statistically meaningful or noise?
```

### 4. Direction & Magnitude of Bias

```
For each filter (IV Rank, VRP, Combined):

6-day vs 30-day behavior:
- More aggressive (higher skip rate)?
- More conservative (lower skip rate)?
- Systematic or random?

Example:
"6-day window is 15pp MORE aggressive than 30d
 → skips more cycles (more conservative for deployment)"
```

---

## 📝 DELIVERABLES

### Report Contents

1. **Executive Summary (1 paragraph)**
   - Can we use 6-day window or must wait for 30-day?
   - Risk level: Low / Medium / High
   - Recommendation: Deploy with 6d / Wait for 30d / Consider hybrid

2. **Detailed Metrics Table**
   - Skip rates (30d vs 6d)
   - Agreement matrix
   - Holdout performance (both windows)
   - Statistical significance

3. **Per-Filter Analysis**
   - IV Rank alone: how sensitive?
   - VRP alone: how sensitive?
   - Combined: how does combining stabilize/destabilize?

4. **Visualizations** (optional but helpful)
   - Skip rate comparison (bar chart: 30d vs 6d)
   - Agreement heatmap (confusion matrix)
   - Holdout bad-rate scatter (30d vs 6d)

5. **Conclusion**
   - Which filter is more robust to shorter windows?
   - Deployment recommendation for VPS2/VPS3
   - If can't use 6d: when will 30d history be available?

---

## 🔍 QUALITY CHECKLIST

- [ ] Uses CORRECTED methodology (split→calculate→validate, no leakage)
- [ ] Same train/holdout split for both window lengths
- [ ] bad_cut calculated independently for each window from TRAIN only
- [ ] Applied same thresholds (IV Rank 0.81, VRP 70.9) to both
- [ ] Metrics clearly labeled (30d vs 6d)
- [ ] Direction of bias stated explicitly
- [ ] Statistical significance considered
- [ ] Recommendation is actionable (deploy / wait / hybrid)

---

## 📂 BASE CODE TO USE

Start from these corrected scripts:
- `backend/services/grogu_combined_filters.py` (best starting point, already has corrections)
- `backend/services/grogu_vrp_filter_backtest.py` (good for VRP-specific analysis)

Modify to test BOTH window_h=720 AND window_h=144 in single run.

---

## ⏱️ TIMELINE

- Estimated effort: 2-3 hours (write analysis script + run tests + report)
- Blocking: VPS2/VPS3 live deployment decision
- Urgency: This determines if we can go live with current 6-day history or must wait

---

## ❓ KEY QUESTIONS ANSWERED BY THIS TASK

1. **Can we deploy with 6-day history?**
   - If agreement > 90% AND bad-rate within 1pp of 8.1% → YES
   - If agreement < 80% OR bad-rate > 10% → NO, WAIT

2. **Which filter is more robust?**
   - IV Rank, VRP, or combined?

3. **What's the risk of deploying now vs waiting?**
   - Quantified in terms of bad-rate drift and skip-rate drift

---

## 🚨 IMPORTANT REMINDERS

- **NO data leakage:** Split first, calculate second
- **Same dataset:** Both windows analyze same 381 cycles
- **ONLY eth_dvol_1h.json:** Don't switch to live markIv
- **Honest methodology:** Follow corrected backtest standards
- **Actionable recommendation:** Report must answer "deploy or wait"

---

**Status:** READY FOR BACKTEST TEAM

Contact with questions on methodology.

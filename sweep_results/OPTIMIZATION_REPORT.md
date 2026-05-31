# Strategy optimization — final report (365d ETH, BS simulation)

**Completed:** 2026-05-31  
**Data:** `data/eth_*.json` (May 2025 – May 2026)  
**Rounds:** broad (144) → put_refine (108) → final_validation + 90d holdout

---

## Winner (deploy in `strategy_config.py`)

**Sell ATM Put · MTF up · high vol · range only**

| Parameter | Value |
|-----------|-------|
| side | P (sell Put) |
| vol_threshold | 0.50 |
| regime_filter | range |
| mtf_direction_filter | up (≥2/3 TF) |
| bull_market_ratio_max | **1.08** |
| cooldown_bars | **12** |
| tp1 / tp2 / sl | 50% / 70% / 150% |
| hold_h | 72h |

### Performance (full train + test + 90d holdout)

| Metric | Old Call baseline | **Final Put winner** |
|--------|-------------------|----------------------|
| Full-year avg/trade | +3.71% | **+16.19%** (train) |
| OOS test avg | +4.80% | **+17.78%** |
| OOS test n | 252 | 84 |
| 90d holdout avg | +3.66% | **+15.84%** |
| Composite score | 4.38 | **17.11** |

### Alternate (more trades)

`PAPER_VARIANT=alt` → `cooldown_bars=6`, same bull 1.08  
~152 OOS trades, +15.26% test avg, +13.78% holdout

---

## What we tested and rejected

| Variant | Why rejected |
|---------|----------------|
| Sell Call MTF-down (old live) | +3.7% full year; beaten 4× |
| bull_filter = None | Train >> test (overfit) |
| regime + transition on Put | **Negative** OOS (−6.75%) |
| vol 0.45 | Weaker test (+6.5%) |
| decay_48h exits | Lower composite vs 72h widest |
| bull 1.05 vs **1.08** | 1.08 wins iter3 (+17.78% vs +15.84% same cd12) |

---

## Iteration log

1. **local_opt_iter1** (144 combos, ~2.9h) — discovered Put >> Call  
2. **validation.json** — confirmed +14.8% full year Put  
3. **local_opt_iter3** (108 Put-only, ~2.8h) — bull 1.08, cd tuning  
4. **final_validation.json** — composite ranking with 90d holdout  

---

## Statistical caveats

- Black-Scholes only; not Bybit option marks  
- OOS n=84 → fragile; one bad month can hurt  
- sl_pct=1.50 → tail risk on stops  
- 252 combos tested → multiple-comparison bias; holdout helped  

---

## Re-run tools

```bash
cd backend
PYTHONPATH=. python3 services/local_optimizer.py --round put_refine --test-only --workers 4
PYTHONPATH=. python3 services/finalize_best.py
PYTHONPATH=. python3 services/local_backtest.py   # uses strategy_config
```

---

## Files

- `sweep_results/local_opt_iter1.json`
- `sweep_results/local_opt_iter3.json`
- `sweep_results/final_validation.json`
- `backend/services/strategy_config.py` — single source of truth

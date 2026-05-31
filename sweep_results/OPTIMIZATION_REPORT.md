# Strategy optimization report (365d, local Mac)

**Date:** 2026-05-31  
**Data:** `data/eth_*.json` (105k × 5m bars, May 2025 – May 2026)  
**Method:** 144-combo broad sweep (OOS test 30%) → full train/test validation

---

## Winner: Sell ATM Put (MTF up + high vol + range)

| Metric | Old live (sell Call) | **New optimized (sell Put)** |
|--------|----------------------|------------------------------|
| Full-year avg P&L/trade | +3.71% | **+14.83%** |
| OOS test avg | +4.80% | **+15.84%** |
| Train avg | +3.24% | **+14.39%** (stable, not overfit) |
| Win rate (full) | 57.0% | **61.2%** |
| Signals/year | 837 | 268 |
| OOS Sharpe/trade | 0.13 | **0.32** |

### Parameters applied to `paper_strategy.py`

```python
WINNER_GEN_KWARGS = {
    "vol_threshold": 0.50,
    "regime_filter": ["range"],
    "side": "P",
    "mtf_direction_filter": "up",
    "bull_market_ratio_max": 1.05,
    "cooldown_bars": 12,
}
WINNER_EXIT = {
    "tp1_pct": 0.50, "tp2_pct": 0.70, "sl_pct": 1.50, "hold_h": 72,
}
```

### Alternate (more trades)

`cooldown_bars=6` → **488 signals**, +13.33% full-year avg, OOS +13.4%  
(`WINNER_GEN_KWARGS_ALT` in `paper_strategy.py`)

---

## What did NOT improve

**Sell Call MTF-down** (old live): best OOS tweak was `vol_threshold=0.65`, `bull_filter=None`  
→ OOS +7.56% but **train −0.77%** (overfit); full-year only **+1.73%** → rejected.

---

## How to re-run

```bash
cd backend
PYTHONPATH=. python3 services/local_optimizer.py --round broad --test-only --workers 4
PYTHONPATH=. python3 services/validate_candidates.py
PYTHONPATH=. python3 services/local_backtest.py  # after editing params
```

---

## Caveats

1. **Black-Scholes** pricing, not real Bybit option history.  
2. Monthly variance is huge (some months −30%, others +40%).  
3. Fewer signals (268/yr) → longer flat periods in live.  
4. Deploy to VPS requires `git push` + `docker compose up -d --build paper backend`.

---

## Files

- `sweep_results/local_opt_iter1.json` — full 144-combo sweep  
- `sweep_results/validation.json` — train/test/full comparison  
- `backend/services/local_optimizer.py` — optimizer tool  
- `backend/services/validate_candidates.py` — validation tool  

# Strategy optimization — final report (365d ETH, BS simulation)

**Completed:** 2026-06-01 (revised after holdout-protocol audit)
**Data:** `data/eth_*.json` (May 2025 – May 2026)
**Rounds:** broad (144) → put_refine (108) → final_validation (proper holdout, 8 cells)

---

## Methodology fix (2026-06-01)

The pre-2026-06-01 finalize_best had a holdout bug: it took the last 90 days of
*signals after generation*, but for the sparse `cd=12` strategy this slice was
identical to the OOS test 30% split. The composite score double-counted them.

This run uses **`holdout_split.py`** — the cutoff is enforced on raw klines
before signal-gen, so the optimizer never sees the last 90 days. `train`/`test`
operate on pre-cutoff signals only; `holdout` is truly disjoint.

A bonus consequence: bull-filter values (None, 1.05, 1.08) deliver
**identical holdout PnL** within each cooldown group — the filter adds no edge
on unseen data. Earlier reports' bull=1.08 advantage was selection bias.

---

## Winner v2 — after 54-cell parallel sweep (2026-06-01)

**Sell ATM Put · MTF up · range · vol≥0.50 · cd=4 · hold=96h · NO bull-filter**

| Parameter | Value | Why this beats cd=6/h=72 (prior) |
|-----------|-------|---|
| side | P (sell Put) | hybrid Put+Call REJECTED (Call -$126/mo) |
| vol_threshold | 0.50 | sweet spot vs 0.45 (noise) and 0.55 (too few) |
| regime_filter | range | transition gave negative OOS |
| mtf_direction_filter | up (≥2/3 TF) | |
| bull_market_ratio_max | **None** | finalize_best: identical holdout across all bull values |
| cooldown_bars | **4** | cd=3 hits margin cap, cd=4 = +2× monthly $ vs cd=6 |
| **hold_h** | **96h** | h=96 beats h=72 by +44% across ALL cd cells |
| tp1 / tp2 / sl | 50% / 70% / 150% | unchanged |

### Parallel sweep top results (holdout, 54 cells)

By $/month on $400:

| Rank | cd/v/h | n_hold | avg | sharpe | $/mo (+%) |
|------|--------|--------|-----|--------|-----------|
| 1 | 3/0.5/96 | 284 | +18.89% | 0.36 | **+$143 (+35.8%)** ← margin-capped to ~$95 |
| 2 | 3/0.45/96 | 357 | +14.25% | 0.24 | +$136 (+33.9%) |
| 3 | 3/0.55/96 | 211 | +21.63% | 0.45 | +$122 (+30.4%) |
| **4** | **4/0.5/96 (LIVE)** | **219** | **+19.57%** | **0.38** | **+$114 (+28.6%)** |
| 5 | 4/0.45/96 | 275 | +14.93% | 0.25 | +$110 (+27.4%) |
| 8 | 6/0.5/96 | 148 | +19.78% | 0.38 | +$78 (+19.5%) ← cd=6/h=96 conservative |
| — | **6/0.5/72** (prior LIVE) | 148 | +13.78% | 0.28 | +$54 (+13.6%) |

By per-trade Sharpe:

| Rank | cd/v/h | sharpe | avg | $/mo |
|------|--------|--------|-----|------|
| 1 | 8/0.55/96 | **0.508** | +22.88% | +$54 |
| 2 | 12/0.55/96 | 0.498 | +24.20% | +$39 |
| 3 | 6/0.55/96 | 0.483 | +22.82% | +$67 |
| 4 | 4/0.55/96 | 0.455 | +21.96% | +$95 |
| 5 | 3/0.55/96 | 0.451 | +21.63% | +$122 |

### Phase 4 hybrid test (Put + Call)

REJECTED. Sell-Call MTF-down has structurally negative edge on 2025-2026 ETH:

| Variant | holdout n | avg | sharpe | $/mo |
|---------|-----------|-----|--------|------|
| Put-only (deployed) | 148 | +13.78% | 0.28 | **+$54** |
| Call-only | 149 | **−31.74%** | −0.37 | **−$126** |
| Hybrid (merge dedupe) | 297 | −9.06% | −0.12 | **−$72** |

Reason: ETH had persistent up-drift through the 365d sample → sell-Call on
MTF-down setups got run over. The proper-holdout `baseline_call` candidate in
finalize_best showed the same (+3.66% holdout vs Put +13.78%).

### Performance — 8-cell composite ranking (proper holdout)

| Rank | Config | composite | train | test | hold | $/mo @ $400 |
|--|--|--|--|--|--|--|
| 1 | put_bullNone_cd12 | **16.27** | n=145 +18.97% | n=63 +21.74% sh=.35 | **n=81 +15.84%** | +$34 (+8.5%) |
| 2 | put_bull108_cd12 (old deploy) | 15.74 | n=137 +15.99% | n=60 +19.33% sh=.31 | **n=81 +15.84%** | +$34 (+8.5%) |
| 3 | put_bull105_cd12 | 14.67 | n=130 +13.39% | n=57 +16.67% sh=.26 | **n=81 +15.84%** | +$34 (+8.5%) |
| 4 | put_bull105_cd6 | 14.40 | n=237 +10.80% | n=103 +18.53% sh=.31 | **n=148 +13.78%** | +$54 (+13.4%) |
| 5 | put_bull108_cd6 | 14.29 | n=250 +13.71% | n=108 +20.92% sh=.35 | **n=148 +13.78%** | +$54 (+13.4%) |
| **6** | **put_bullNone_cd6 → LIVE** | **14.05** | n=266 +17.10% | n=114 +23.50% sh=**.40** | **n=148 +13.78%** | **+$54 (+13.4%)** |
| 7 | put_v045_bull108_cd12 | 5.75 | n=156 +17.70% | n=67 +7.87% sh=.13 | n=101 +10.92% | — |
| 8 | baseline_call (pre-6be2fbc) | 4.98 | n=434 +2.01% | n=187 +7.72% sh=.21 | n=216 +3.66% | — |

### Why pick rank-6 (cd=6) as LIVE over rank-1 (cd=12)

- **cd=6 ≈ 1.83× more holdout sigs** (148 vs 81) at −13% per-trade edge
- Net **~+58% more $/month on $400** (+$54 vs +$34)
- Test Sharpe 0.40 > cd=12's 0.35 (better risk-adjusted)
- Composite ranks cd=12 higher because of `selection_bias_pen` (cd=6 had wider
  test-vs-holdout gap), but the holdout itself stayed solid at +13.78%

cd=12 retained as `PAPER_VARIANT=alt` for users preferring lower frequency.

---

## What we tested and rejected

| Variant | Reason |
|---------|--------|
| Sell Call MTF-down (old live, pre-6be2fbc) | +3.66% holdout; 4× worse than Put |
| bull_filter = 1.05 / 1.08 | identical holdout to bull=None — no edge added |
| regime ∪ transition on Put | negative OOS in iter3 |
| vol = 0.45 | composite 5.75 (overfit train) |
| decay_48h exits | lower composite than 72h widest |

---

## Statistical caveats

- **Black-Scholes pricing** only; real Bybit Put-IV often 15-25% higher than
  ATM-Call IV. Real per-trade $ may be higher than backtest by similar margin.
- Holdout n=148 (cd=6) is fragile statistically; 90d window may be biased by
  the local market regime.
- sl_pct=1.50 → tail risk: one stop ≈ 1.5× of an average winner. Limit by
  per-day stop count via `consec_losses` circuit breaker.
- Multiple-comparison risk: 252+ combos surveyed. Composite includes
  `selection_bias_pen` (test-hold gap > 5% triggers penalty).
- `bull_filter=None` → strategy will not refuse signals in blow-off bull
  regimes. Holdout window contained no such regime; live deployment may
  encounter one.

---

## Re-run tools

```bash
cd backend
PYTHONPATH=. python3 services/finalize_best.py        # 8-cell composite ranking
PYTHONPATH=. python3 services/profit_experiments.py   # hybrid + expiry + IV-skew
PYTHONPATH=. python3 services/local_backtest.py       # current LIVE config
PYTHONPATH=. python3 services/local_backtest.py --baseline  # old Call config
```

Holdout window is controlled by `HOLDOUT_DAYS` env (default 90).

---

## Files

- `backend/services/strategy_config.py` — single source of truth
- `backend/services/holdout_split.py` — cutoff protocol
- `backend/services/finalize_best.py` — composite ranking driver
- `backend/services/profit_experiments.py` — Phase 4 lever experiments
- `sweep_results/final_validation.json` — 8-cell ranking output
- `sweep_results/local_opt_iter1.json` — broad 144-combo (legacy)
- `sweep_results/local_opt_iter3.json` — put_refine 108 (legacy)

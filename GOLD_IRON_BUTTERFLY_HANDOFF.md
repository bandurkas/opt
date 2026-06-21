# Gold (GLD/XAUT) Iron Butterfly — REJECTED 2026-06-21 (spread kills it)

Status as of 2026-06-21: **REJECTED.** The entire edge below was a backtest artifact
of pricing all 4 legs at theoretical Black-Scholes MID with no bid/ask spread. Once
real CBOE GLD spreads (measured live via `gld_chain_probe.py`, no `dte<3` filter) are
applied as actual fills (sell legs at bid, buy legs at ask), the edge collapses:

- Real near-dated (1-5 DTE) GLD spreads are MUCH wider than the original 20-75 DTE
  probe suggested: calls ~9-11% round-trip, **puts ~9-22%** (worst at the very
  shortest, 1.2 DTE tenor — exactly what this strategy needs to trade).
- Re-ran the full wing x TP_frac sweep with spread-adjusted fills
  (`gold_ironfly_sweep_parallel.py`, 8-core parallel). Previous "best" (wing=$5,
  TP=1.0, hold-to-expiry): avg+17.15%/cycle, train+17.7%≈holdout+15.8% WITHOUT
  spread → with real spread: avg**-1.03%**, train+2.09% but **HOLDOUT-8.33%**
  (sign flips — no real edge, was always just an artifact of ignoring transaction
  cost).
- Best surviving combo with spread ON: wing=$10, TP=1.0 → avg**+0.93%**/cycle,
  train+1.26%/holdout+0.17% (same sign, but Sharpe ~0.00-0.03 — noise-level, not
  a tradeable edge). Every other wing/TP combo is negative with spread on.
- Mechanism: early profit-taking pays the spread TWICE (open + close); only
  hold-to-expiry (cash-settled, no bid/ask at settlement) avoids the second hit —
  which is why TP_frac=1.0 looked artificially best even before this fix, and why
  the apparent edge was entirely a spread-avoidance illusion, not real theta harvest.

**Conclusion: do not deploy. Do not revisit the $5-wing/24h iron butterfly as
previously described — it's dead on real transaction costs, matching the pattern of
every other "smooth small edge" idea tested in this project (ETH symmetric straddle,
entry-frequency levers, ADX sizing, rotation, perp overlays — all rejected on
realistic costs/OOS). See `finding_options_gold_ironfly_spread_rejected` memory.**

---

## Original (now superseded) narrative below — kept for the chain-of-reasoning record

Status as of 2026-06-20. Continue from here in a new session.

## TL;DR

A 24h-cadence ATM **iron butterfly** on gold (sell ATM call+put, buy $5-wide OTM wings)
shows a real, robust edge in backtest (TRAIN≈HOLDOUT, no crash-month blowup), AND now
fits a real $2000 IBKR account once margin is computed correctly. But the realistic
monthly return depends entirely on position sizing, and even at the sane sizing point
it comes with 45-95% drawdowns. **Not a "smooth income" result — don't deploy as-is.**

## How we got here (chain of reasoning, don't redo this)

1. Original ETH VRP-seller bot only trades crypto → wanted a decorrelated gold leg to
   smooth the equity curve during crypto-calm periods.
2. Crypto gold venues are all dead: Bybit XAUT options 95-114% spreads/~0 volume,
   Deribit has no gold options (PAXG is spot-only), Derive/Thalex/OKX/Binance — nothing.
   Real gold options only exist in the regulated world: CME/COMEX futures options (OG/OMG)
   or GLD/IAU ETF options. → IBKR is the only realistic broker (accepts Indonesia
   residents, $2k margin minimum, real API).
3. Pulled the REAL GLD options chain free via CBOE delayed quotes
   (`backend/services/gld_chain_probe.py`) — market IS liquid (4-6% spreads, deep OI),
   confirming GLD options are a real tradeable proxy for CME gold vol.
4. Original naked-put-sell-on-trend-signal gold strategy was REJECTED earlier
   (2026-06-17, see `project_options_gold_rejected` memory) — it was a hidden leveraged
   long that inverted to -25%/trade in gold corrections.
5. Direction-neutral redesign (sell ATM call+put together, no trend filter) on a WEEKLY
   cadence fixed the crash-survival problem but the edge itself sign-flipped
   train→holdout (too thin, see `gold_strangle_backtest.py` weekly results).
6. **24h cadence (not weekly) is the breakthrough**: same direction-neutral short
   straddle, just much shorter hold. TRAIN+14.65%/cycle → HOLDOUT+7.89%/cycle, same
   sign, ZERO negative months across 13mo including the 2026-03 gold crash
   (+2.54%/cycle that month, WR87%). This is genuine theta-harvest (SL fires 42% of
   individual legs but the paired leg usually catches TP2, netting positive).
7. User asked explicitly: model the AVERAGE MONTHLY RETURN at IBKR's $2000 minimum
   deposit. This forced confronting margin reality:
   - **Naked margin is impossible at $2000.** Real Reg-T naked straddle margin on GLD
     (S=$387, ATM K=$385, σ=22.8%) ≈ **$8,148/contract** — ~4x the whole account.
   - Fix = **iron butterfly**: buy OTM wings to cap risk. $5-wide wings (matching GLD's
     real strike spacing near $387) → margin caps to `wing*100 - credit*100`.
8. Built `backend/services/gold_iron_butterfly_backtest.py` to price all 4 legs directly
   via Black-Scholes (the shared engine only handles single naked legs).
9. **Caught a scale bug before trusting any number**: the backtest's underlying data is
   XAUT (a crypto token tracking raw spot gold $/oz, ~$3251-5598 over the dataset), NOT
   GLD-ETF-share scale (~$387/share, since 1 GLD share ≈ 1/10oz minus fees). Applying the
   real $5 GLD wing directly to XAUT's price made the wing only ~0.1% OTM instead of the
   intended ~1.3% OTM — silently collapsed the defined-risk structure back toward naked
   while still computing margin as if defined-risk (symptom: margin came out $22/contract,
   nonsensically low). **Fixed** via `GLD_RATIO = XAUT_close_today / real_GLD_spot_today`,
   dividing the underlying price (not σ — vol/log-returns are scale-invariant) before
   strike-rounding and BS pricing, at all 3 points the underlying price is used
   (entry, forward-walk, expiry settlement).

## Current validated numbers (after the GLD_RATIO fix)

Run: `python3 backend/services/gold_iron_butterfly_backtest.py 5` (wing=$5, 24h cycle)

- n=357 cycles (13mo), margin≈$199/contract, credit≈$301/contract
- Per-cycle: avg+9.64%, WR59%, Sharpe+0.12
- **TRAIN(<2026-03)** +9.98%/cycle ≈ **HOLDOUT(≥2026-03)** +8.86%/cycle — robust, same sign
- Worst month (2026-03 crash) only -4.17% — no blowup
- resolutions: 157 early-TP / 200 ran-to-expiry

### Account-sizing sweep (THE critical finding — don't skip this when continuing)

Max loss per cycle = -100% of deployed margin (defined risk, by construction of the
butterfly). Naive full reinvestment of capital every cycle hits that tail repeatedly =
classic Kelly-criterion over-betting, and blows the account up despite the good average:

| risk-per-cycle (% of capital) | total return (11.7mo) | compound %/month | maxDD |
|---|---|---|---|
| 80% (naive "go big") | -89% | -17.3% | 100% (ruined) |
| 40% | -63% | -8.1% | 100% (ruined) |
| 20% | +531% | +17.1% | 94.8% |
| **10%** | **+511%** | **+16.7%** | **72.7%** |
| 5% | +342% | +13.6% | 44.1% |

Empirical optimum (~10-20% capital at risk/cycle) matches a back-of-envelope Kelly
estimate (μ/σ² ≈ 9.64%/0.80² ≈ 15%).

**Honest answer if asked "how much per month": ~14-17%/month compound IS what this one
historical path shows at sane sizing — but with 45-95% drawdowns along the way.** This is
NOT a smooth income stream; it's a separate high-variance vol-selling bet that itself
needs careful risk control. It does NOT achieve the original goal ("smooth the ETH bot's
equity curve during crypto-calm periods") — it would add its own large swings.

## What's NOT yet validated — do this before considering any real deployment

1. **Underlying price proxy.** Backtest still uses XAUT (crypto token) as a stand-in for
   real GLD/COMEX price action. Should track tightly (it's a backed token) but never
   verified against real GLD daily closes. Get a real GLD/COMEX price history (Yahoo
   was 429-rate-limited, Stooq is JS-walled — try a different free source) and re-run.
2. **Synthetic σ.** Sigma is still a trailing-RV168h proxy (`× 1.05`), not a real
   historical gold-IV time series — no historical options-IV data source has been found
   yet. A real IV collector (like the ETH `iv_collector.py`) could be stood up against
   CBOE GLD chain (`gld_chain_probe.py` already pulls live snapshots) to start building
   one, but it takes time to accumulate.
3. **Exits weren't tuned for this structure.** TAKE_PROFIT_FRAC=0.50 (close at 50% of
   credit captured) was a reasonable guess, not swept. A proper exit-param sweep
   (analogous to `iv_short_exit_opt.py` for ETH) could change the risk/return picture.
4. **Only one wing width tested end-to-end** ($5, matching GLD's real strike spacing).
   $10/$15/$20 wings were spot-checked manually for margin only, not backtested — wider
   wings = more margin but probably smoother PnL (worth sweeping,
   `gold_iron_butterfly_backtest.py <wing_W>` already supports this via argv).
5. **Single historical path, one regime mix** (~13mo, one bull stretch + one correction).
   No Monte Carlo / no second independent gold history to confirm robustness.
6. **Sample is correlated**, not independent draws — 357 daily cycles on ONE underlying
   is much thinner evidence than ETH's larger/more diverse trade sample. Treat all the
   above %s as a single noisy point estimate, not a confirmed expectation.

## Files

- `backend/services/gold_iron_butterfly_backtest.py` — main harness (4-leg BS pricing,
  GLD_RATIO-corrected, account-sizing sweep + fixed-contract sim built in). Run with
  optional wing-width arg: `python3 ... [wing_W]` (default $5).
- `backend/services/gold_strangle_backtest.py` — the simpler single-leg-engine version
  (naked straddle, no margin/account modeling) used to find the 24h-cadence lead.
  Supports `CYCLE_H HOLD_H` argv for cadence sweeps.
- `backend/services/gld_chain_probe.py` — free real-time CBOE GLD options chain pull
  (spread/OI/IV), used for the original liquidity + IV-term-structure measurement.
- `backend/services/gold_oos_regime.py`, `iv_short_exit_opt.py` — older harnesses from
  the original (rejected) naked-put-on-trend-signal gold strategy; kept for reference.

## Memory pointer

Full narrative + all intermediate dead ends logged in
`project_options_gold_rejected` memory (the auto-memory system) — read it for the
complete history if more context is needed than this file provides.

## Suggested next steps (pick up here)

1. Try to source real GLD/COMEX daily price history (try Nasdaq Data Link, Alpha
   Vantage free tier, or IBKR's own historical-data API once registration completes) and
   re-run the iron-butterfly backtest on it instead of the XAUT proxy.
2. Sweep wing widths ($10/$15/$20) and the TP fraction for risk/return tradeoff.
3. If a real-IV collector is wanted, stand one up against `gld_chain_probe.py`'s CBOE
   feed now — it takes weeks to accumulate enough history to test IV-vs-RV properly.
4. Only after (1)-(3) check out cleanly: re-run the full train/holdout gate one more
   time before writing any IBKR live-order code.

# BTC Unconditional Short-Straddle — Paper Bot Implementation Handoff

**Date:** 2026-06-21
**Status:** Backtest-validated, redesigned exit rule, survived 2022 crash stress test. NOT yet implemented in any bot — paper implementation is tomorrow's task.
**Goal:** Build a paper-trading version (mirrors how the live ETH bot's `paper_loop.py` works) of a BTC unconditional short ATM straddle, separate from the existing ETH V3 book.

---

## 1. The strategy, in one paragraph

Sell ONE ATM call + ONE ATM put on BTC every 24 hours (matches Bybit's shortest listed BTC option tenor — daily expiries). No directional signal, no regime/MTF filter — this is pure variance-risk-premium harvesting, structurally different from the ETH bot's V3 directional generator. Entry IV is the trailing-168h realized-vol estimate (same calibration the ETH bot uses) × 1.10. Exit uses a **dollar-margin-based stop**, not a %-of-premium stop (see §3 — this was the key fix this session).

## 2. Research arc (why this is the final design, not the first idea)

1. User asked if BTC could be profitable selling vol, given the existing ETH-tuned V3 signal generator failed on BTC (`project_options_multicoin_research.md`, `btc_honest_iv_retest.py`). Confirmed: the ETH-style regime/MTF entry timing genuinely does not transfer to BTC (train/holdout sign-flips both sides — pure noise).
2. Pivoted to testing **unconditional** premium selling (no entry filter at all) — `btc_straddle_sweep.py`. Found a config (cycle=24h, tp1=0.50, tp2=0.80, sl=0.75-of-premium, iv_rv_mult=1.10) that looked very robust: train≈holdout, 34/36 months positive over 3 years, survived strike-spacing and spread stress tests (`btc_straddle_winner_detail.py`, `btc_straddle_sensitivity.py`).
3. User asked about deposit sizing → built `btc_straddle_account_sim.py` (same methodology as `deposit_sim.py`/`deposit_curve.py` used to find ETH's $800 knee). Found BTC's margin-knee ≈ $1600-2000 (vs ETH's $800) because Bybit's BTC option lot (0.01 BTC, confirmed live via Bybit's `/v5/market/instruments-info`) carries bigger dollar notional than ETH's (0.1 ETH).
4. User asked about reaching 3-5%/month via bigger position size (`MARGIN_PCT` sweep) → numbers looked great (Sharpe-positive at MARGIN_PCT up to 1.00) but this triggered the same red flag as the (rejected) PURR-leverage research: **thin margin buffers + unmodeled gap risk = backtest looks safe, reality might not be.**
5. Built `btc_straddle_gap_stress.py` to test this directly: the engine's intrabar high/low SL detection is fine, but it **caps the realized loss at exactly -75% of premium**, which is nonsensical near expiry (an ATM option's premium decays toward $0, but its *intrinsic* sensitivity to a move does NOT — confirmed via direct BS pricing: a 2% move 5 minutes from expiry can reprice an option 30-40x). Honest re-pricing collapsed Sharpe from +0.25 to +0.04 (noise), with real per-leg losses up to **-405% of premium**. **This killed the original finding.** Tried "close early to dodge the gamma window" — doesn't work (1h early = marginal help, more than that just bleeds theta for no safety gain).
6. **Fix:** redesigned the stop to trigger off **dollars lost relative to posted margin**, not % of premium (`btc_straddle_dollar_stop.py`). Margin (`IM_RATE×strike + premium`) doesn't shrink near expiry like premium does, so this stays a sane, constant-magnitude tripwire through the whole option life. Worst real per-leg loss with this fix: **-106% of that leg's margin** (vs -405% before) — bounded and survivable.
7. Re-ran the account-level deposit/leverage sweep on the fixed exit rule (`btc_straddle_dollar_account_sim.py`), **extended to include the 2022 LUNA/FTX crash** (1650 days back, vs the original 3y window) per user's explicit request. Result: **the strategy not only survived 2022, Nov 2022 (FTX) was the single best month of that year** — crash months spike realized vol, the dynamic-σ entry prices the straddle richer exactly when it should, and the dollar-stop bounds the rare bad cycle (only Jan/Feb 2022 were mildly negative).

## 3. Final validated parameters

```
CYCLE_H        = 24.0          # hours; sell + auto-close every cycle (matches Bybit's shortest BTC tenor)
TP2_PCT        = 0.80          # take-profit at 80% of premium decayed (no TP1 leg for short premium)
SL_DOLLAR_FRAC = 2.0           # stop when unrealized loss >= 2.0 × per-lot margin posted (NOT % of premium!)
IV_RV_MULT     = 1.10          # entry sigma = trailing-168h realized vol × this (matches Derive's real BTC IV/RV ratio)
SIGMA_CLAMP    = (0.20, 1.50)
SPREAD_PCT     = 2.0           # round-trip, matches ETH bot's convention; stress-tested up to 8% and still survives
STRIKE_ROUND   = 500.0         # $ — confirmed live via Bybit instruments-info, near-term BTC strikes step $500
IM_RATE        = 0.10          # margin formula: (IM_RATE × strike + premium) × qty  — same formula as ETH engine
LOT            = 0.01          # BTC — CONFIRMED LIVE (Bybit minOrderQty/qtyStep for BTC options = 0.01; ETH is 0.1)
MARGIN_PCT     = 0.15-0.25     # fraction of equity budgeted per cycle (split 50/50 between the call leg and put leg)
```

**Recommended starting point: MARGIN_PCT=0.15, deposit $2000.** Backtest result at these settings (1650d incl. 2022): **+3.49%/month CAGR, maxDD 20.0%**, 1635/1643 cycles executable (no margin starvation). MARGIN_PCT=0.25 pushes to +6.1%/mo but maxDD 32%. **Do not go above MARGIN_PCT=0.25** without a second independent crash episode in the data to validate against — only 2022 has been stress-tested so far.

**Deposit knee is current-BTC-price-dependent.** $1600-2000 is the right number for BTC at today's price (~$65-95k, computed on the recent 3y window). Don't reuse the lower "$800" knee figure from the 1650d window — that's an artifact of averaging in cheap BTC ($16-25k) from 2022, not relevant for sizing at today's price.

## 4. Stop-loss formula (the part that actually matters — implement exactly this)

```python
# at position open:
margin_per_lot = (IM_RATE * strike + entry_premium) * LOT
sl_dollar_trip = SL_DOLLAR_FRAC * margin_per_lot   # constant for the position's whole life

# on each price-check tick (live: every 30s like the ETH bot's monitor loop):
unrealized_loss_dollars = (current_buyback_ask - entry_credit) * qty
if unrealized_loss_dollars >= sl_dollar_trip * (qty / LOT):   # scale trip by actual qty
    close_position()  # this IS the honest exit — no further capping
```

Do **NOT** copy the ETH bot's `PUT_EXIT`/`CALL_EXIT` style (`sl_pct` as a fraction of entry premium) for this BTC straddle — that mechanism is what broke under the gap-risk stress test. This needs its own exit config shape (dollar-relative-to-margin), not the existing `strategy_config.py` pattern.

## 5. Where this plugs into the existing codebase

- **Live ETH bot reference:** `backend/services/paper_loop.py` — signal check every 5min, position monitor every 30s, uses `db/paper_repo.py` + tables `paper_positions`, `paper_state`, `paper_equity_snapshots` (see `paper_state` schema — started 2026-06-19, $800 equity, on VPS3 `/root/opt-app`).
- **This needs to be a SEPARATE paper account/loop, not mixed into the ETH one** — different underlying, different cycle cadence (24h fixed vs event-driven), different exit math. Cleanest: new tables (e.g. `paper_positions_btc` or add a `book` column to distinguish `eth_v3` vs `btc_straddle`), new service file `backend/services/btc_straddle_paper_loop.py`, new docker-compose service or reuse the existing `paper` container with a second async task.
- **Pricing fallback:** `services/backtest_bs.py` (`price`, `delta`, `theta_per_day`) — already used identically in this session's harnesses, reuse directly for the live BS-fallback when Bybit's real option chain isn't queryable.
- **Real Bybit option chain:** mirror `paper_loop.py`'s `pick_bybit_atm_option`-style logic (live bot picks the REAL nearest-expiry Bybit contract, not pure synthetic) — same approach should apply here, just for `BTC-<expiry>-<strike>-{C,P}-USDT` symbols instead of ETH's.
- **Margin/account engine reference:** `services/deposit_sim.py` and this session's `btc_straddle_dollar_account_sim.py` — the live position-sizing logic (n_lots = budget // margin_per_lot, MAX_OPEN=2 since one call+one put per non-overlapping 24h cycle, no portfolio-margin netting credit assumed) should be ported close to verbatim.

## 6. Harnesses created this session (all in `backend/services/`, all runnable standalone with plain `python3`, no docker needed for the BTC ones)

| File | Purpose |
|---|---|
| `btc_honest_iv_retest.py` | Re-confirmed ETH's V3 generator doesn't work on BTC (closes that question) |
| `btc_straddle_sweep.py` | 192-config brute-force grid (cycle/tp/sl/iv_rv_mult), 8-core fork-based parallel, train/holdout select |
| `btc_straddle_winner_detail.py` | Monthly breakdown + leg split for a chosen config |
| `btc_straddle_sensitivity.py` | Strike-spacing and spread stress tests |
| `btc_straddle_account_sim.py` | Deposit-knee + MARGIN_PCT sweep — **uses the OLD (broken) %-premium SL, kept for reference/comparison only** |
| `btc_straddle_gap_stress.py` | The critical test — exposes the %-premium SL's near-expiry blowup; also has the early-close mitigation sweep (doesn't help) |
| `btc_straddle_dollar_stop.py` | **The fix** — dollar-margin-relative SL, sweep `sl_dollar_frac`, train/holdout |
| `btc_straddle_dollar_account_sim.py` | **Use this one** — deposit-knee + MARGIN_PCT sweep on the FIXED exit rule, includes 2022 monthly detail |
| `session_open_*.py`, `cme_cboe_open_stats.py` | Earlier in this session: tested and REJECTED session-open/CME-CBOE-open volatility timing ideas — see `finding_options_session_open_rejected.md`. Not related to the BTC straddle; kept for provenance. |

Also modified: `backend/services/backtest.py` — added optional `strike_round_to` param (default 25.0, so every existing ETH/gold backtest is byte-for-byte unaffected) to `simulate_signal_set`/`_simulate_option_trade`, used for BTC's realistic $500 strike spacing tests.

Data: `data/btc_long_{5m,15m,1h}.json` — full BTCUSDT history back to 2020-04 (648k/216k/54k bars), fetched live via VPS3 (local Mac has no direct Bybit reachability — sandboxed network, SSL-blocked). `data/` is gitignored, these files are NOT in this commit — re-fetch via VPS3 if needed (see git history / ask Claude to re-run the fetch script through `ssh root@187.127.114.34`).

## 7. Open items for tomorrow

1. **Write the actual paper-trading loop** per §5 — new service, new DB schema (or column), reuse the dollar-margin SL formula from §4 exactly.
2. **Decide MARGIN_PCT for the paper run** — recommend starting conservative (0.15) since paper validation should mirror what you'd actually risk live, not the backtest-optimal.
3. **Wire up the deposit** — $2000 paper balance recommended (the validated knee).
4. **Real Bybit BTC option chain integration** — confirm the bot can actually fetch/parse `BTC-<DDMMMYY>-<strike>-{C,P}-USDT` symbols and their live quotes (same pattern as the ETH bot already does).
5. **Paper-validation gate** — same bar ETH cleared: accumulate 20-30+ cycles, compare paper performance to backtest (expect some decay, per every other finding in this project), watch the dollar-stop actually fire correctly on a real losing cycle before trusting it with more size.
6. Per user's explicit operating assumption: **this is meant to run with human monitoring**, not as a fully unsupervised bot — no algorithmic circuit breaker can foresee a LUNA/FTX-style event before it happens, only react after losses start. Keep that in the design (alerting, not just auto-pilot).

## 8. What's still NOT validated (be honest about this if asked)

- Only ONE crash episode (2022) has been stress-tested. A different/worse crash (sharper, longer, or with worse liquidity than 2022) could behave differently — survival in backtest is not a guarantee.
- Margin formula doesn't model real Bybit portfolio-margin netting between the call and put legs (conservative — real capital efficiency is probably somewhat better than modeled).
- Fee model is a proxy (reused from the ETH engine's formula), not independently verified against real Bybit BTC option fee schedules.
- No real intraday Bybit BTC IV data used anywhere — entry sigma is always the synthetic trailing-RV proxy, same as the rest of this project's methodology.

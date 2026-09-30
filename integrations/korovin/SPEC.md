# KOR-BTC-PAPER-001

Virtual only. No credentials, signed requests, or exchange order endpoints.
User authorized live paper trading and replacing dashboard panel 6 on 2026-09-28.
This is a forward experiment, NOT a verified reproduction of every discretionary
course rule, NOT a backtested edge. Existing bots and strategy-6 history remain intact.

Frozen defaults: 20,000 USDT virtual capital; buy 0.24 BTC of nearest-ATM USDT call
with 21–35 days remaining, expiry nearest 28 days. Initial BTCUSDT perpetual short
equals call delta × quantity, rounded to exchange step. Minimum required call
liquidity is full size at ask; spread <=10% of mid. Premium+entry fees <=2,000 USDT.
Base short must be >=0.08 BTC and <=0.16 BTC. No naked option sales or edges.

Fixed initial center = filled perpetual bid. Four 0.01 BTC parts in each direction.
Width = 0.45% of initial center, rounded UP to perpetual tick. Entry distances
1, 2, 5, 12 widths; exit one width towards center. Buy below center, sell above.
No discretionary relocation, volume growth, loss unfreezing, or roll in v1.
These percentage rules are OUR adaptation; RI absolute levels are not copied.
Exit entire experiment at equity <=16,000 or five days before option expiry;
do not reopen automatically. If a close cannot fill, keep position and retry;
never invent a fill. Equity threshold is not a guaranteed maximum loss.

Quotes polled every 10s, full option quote and instrument metadata at assembly.
Model taker fills only: buy when ask<=limit, sell when bid>=limit, enough top size.
Fill at limit for grid (conservative vs current quote), initial/close at ask/bid.
One leg per part per snapshot; missing intervals are NOT replayed from candle highs.
Simultaneous assembly is a simplifying assumption, explicitly shown in UI.
All prices/quantities validated against metadata. Mark equity and executable-side
liquidation equity shown separately, with estimated closing fees.

VIP0 published assumptions: perpetual taker 0.055% notional per fill; option taker
min(0.03% index, 7% premium) × BTC quantity per fill. No maker discount assumed.
Personal fee tier unknown. Fees sources accessed 2026-09-28:
https://www.bybit.com/en/help-center/article/Trading-Fee-Structure
https://www.bybit.com/en/help-center/article/Bybit-Option-Fees-Explained

Funding: finalized public funding history, position immediately BEFORE timestamp,
signed cashflow = -quantity × settlement mark estimate × finalized rate.
Settlement mark estimate = OPEN of Bybit 1-minute mark candle at timestamp;
this is an explicit precision limitation, not an account funding statement.
Funding applied once per timestamp and caught up after restart. Missing rate or
mark candle remains pending, never zeroed. Funding applies to perpetual only.
Actual prospective funding quote is not booked before settlement.

Independent SQLite state + events + quote samples. Restart preserves position and
rules hash; changed config refuses old state. Read-only authenticated dashboard.
Margin shown as conservative research reserve (full perpetual notional + paid
premium), NOT Bybit liquidation or portfolio-margin calculation. No leverage.
Full account liquidation model and historical option backtest are NOT implemented.
Any new rule version starts a separate ledger and must preserve this experiment.


## Deterministic paper agent v002 (2026-09-30)
Runs inside the existing Engine worker, not a second writer or LLM. Frozen CONFIG,
RULE_HASH, positions and ledger are retained. agent_version events identify the
execution-policy change separately. Each successful/error tick stores decision,
reason, quote timestamp and heartbeat; dashboard shows them. Quotes must remain
within 30 seconds of local time at decision (including after funding lookups).
Non-increasing timestamps freeze execution. A >60 second sample gap skips the
whole grid/assembly for one snapshot, then resumes on the next fresh snapshot.
No missed fills are replayed; protective full close may still run on fresh quotes.
Funding pending blocks grid as before. No new alpha, discretionary levels,
unfreezing, volume growth, live orders or credentials are introduced.

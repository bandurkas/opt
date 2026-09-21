# Strategy 6 v2 A dashboard adapter

Read-only integration: independent SIM-001/A positions from Artur SQLite.
Bybit public linear tickers and 15m/30m candles are display-only. Existing
OKX-derived entries, stops, targets and PnL are never recomputed on Bybit.
The browser uses same-origin /api/strategy6; the server reaches the adapter
on Docker bridge gateway 172.18.0.1:8106. No public listener, credentials,
trading controls or database writes are added. Missing quotes are null.
SQLite connection is mode=ro. Stats are sums of independent setups, not
equity of one capital-constrained account. Funding remains provisional.

Review 1: data boundary checked: A only; allowlisted fields; no secret payload.
Review 2: execution boundary checked: GET only, fixed public Bybit endpoints,
no private exchange keys, no strategy edits, no trading container restart.
Review 3: failure checks: missing quotes stay unavailable; chart failures
isolated; stale simulation marked; API query cannot choose an arbitrary host.
Automated Python adapter tests and Next build required before deployment.

Deploy: commit -> push origin/main -> production pull --ff-only ->
scripts/deploy_on_vps.sh --frontend-only. This new guarded mode rebuilds and
recreates frontend ONLY using --no-deps; the original whole-stack path is
untouched. Never use DEPLOY_FORCE. Keep the previous frontend image for rollback.
Adapter service: artur-n6-dashboard.service. Data DB remains at
/home/artur/Артур бот/state.sqlite3. Gateway address is deployment-specific.

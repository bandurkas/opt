# KOR-PAPER-AGENT-002

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

State contract: agent={version, mode:"deterministic_paper", decision, reason, heartbeat_at, quote_at}; times are Unix milliseconds. Decisions: observe, assemble, wait_liquidity, gap_pause, funding_pause, grid_fills, wait_levels, closing, closed, error_pause. Old ledger needs no schema migration. Existing terminal closed phase remains closed; no automatic new experiment. Runtime single writer is unchanged.

Validation: 16 unittest tests passed including gap-before-fill/resume, stale rollback, protective close after a gap, and existing accounting/funding/reporting tests.

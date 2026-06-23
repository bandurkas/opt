-- OHLC time-series (5m / 15m / 1h). Retention ~30 days, idempotent upserts.
CREATE TABLE IF NOT EXISTS klines (
    symbol      TEXT          NOT NULL,
    interval    TEXT          NOT NULL,
    start_ms    BIGINT        NOT NULL,
    open        NUMERIC(18,4) NOT NULL,
    high        NUMERIC(18,4) NOT NULL,
    low         NUMERIC(18,4) NOT NULL,
    close       NUMERIC(18,4) NOT NULL,
    volume      NUMERIC(28,8) NOT NULL,
    PRIMARY KEY (symbol, interval, start_ms)
);
CREATE INDEX IF NOT EXISTS klines_lookup
    ON klines (symbol, interval, start_ms DESC);

-- Option chain snapshots (ATM ±N% window). Retention 7 days.
CREATE TABLE IF NOT EXISTS option_snapshots (
    symbol            TEXT          NOT NULL,
    ts_ms             BIGINT        NOT NULL,
    base_coin         TEXT          NOT NULL,
    side              CHAR(1)       NOT NULL,
    strike            NUMERIC(18,4) NOT NULL,
    expiry_ms         BIGINT        NOT NULL,
    bid               NUMERIC(18,6),
    ask               NUMERIC(18,6),
    mark_price        NUMERIC(18,6),
    mark_iv           NUMERIC(10,6),
    delta             NUMERIC(10,6),
    gamma             NUMERIC(14,10),
    vega              NUMERIC(10,6),
    theta             NUMERIC(10,6),
    open_interest     NUMERIC(18,4),
    volume_24h        NUMERIC(18,4),
    underlying_price  NUMERIC(18,4),
    PRIMARY KEY (symbol, ts_ms)
);
CREATE INDEX IF NOT EXISTS option_snapshots_recent
    ON option_snapshots (symbol, ts_ms DESC);
CREATE INDEX IF NOT EXISTS option_snapshots_ts
    ON option_snapshots (ts_ms DESC);

-- Generated signals — for later calibration. payload contains full breakdown + entry plan.
CREATE TABLE IF NOT EXISTS signals (
    id                  BIGSERIAL  PRIMARY KEY,
    generated_at_ms     BIGINT     NOT NULL,
    symbol              TEXT       NOT NULL,
    side                CHAR(1)    NOT NULL,
    strike              NUMERIC(18,4),
    expiry_ms           BIGINT,
    score               NUMERIC(4,1),
    signal_type         TEXT,
    payload             JSONB,
    outcome_realized_ms BIGINT,
    pnl_pct             NUMERIC(10,4)
);
CREATE INDEX IF NOT EXISTS signals_recent
    ON signals (generated_at_ms DESC);
CREATE INDEX IF NOT EXISTS signals_score
    ON signals (score DESC, generated_at_ms DESC);

-- Paper-trading positions (one row per paper trade taken by the live strategy).
CREATE TABLE IF NOT EXISTS paper_positions (
    id                  BIGSERIAL    PRIMARY KEY,
    opened_at_ms        BIGINT       NOT NULL,
    underlying_at_open  NUMERIC(18,4) NOT NULL,
    side                CHAR(1)      NOT NULL,                -- 'C' or 'P'
    strike              NUMERIC(18,4) NOT NULL,
    expiry_ms           BIGINT       NOT NULL,
    contracts           NUMERIC(18,8) NOT NULL,               -- # contracts (size_usd / entry_credit_usd)
    size_usd            NUMERIC(18,2) NOT NULL,               -- dollar size at entry
    entry_credit_usd    NUMERIC(18,4) NOT NULL,               -- per-contract premium received
    entry_credit_pct    NUMERIC(10,4) NOT NULL,               -- premium / underlying * 100
    entry_source        TEXT         NOT NULL,                -- 'bybit' | 'bs_fallback'
    status              TEXT         NOT NULL DEFAULT 'open', -- 'open' | 'closed_tp1' | 'closed_tp2' | 'closed_sl' | 'closed_time'
    tp1_pct             NUMERIC(10,4) NOT NULL,               -- decay-% for half-close
    tp2_pct             NUMERIC(10,4) NOT NULL,               -- decay-% for full close
    sl_pct              NUMERIC(10,4) NOT NULL,               -- growth-% for stop-loss
    hold_h              INT          NOT NULL,                -- max hold hours (time-stop)
    half_closed_at_ms   BIGINT,                               -- when TP1 fired
    closed_at_ms        BIGINT,
    exit_debit_usd      NUMERIC(18,4),                        -- per-contract premium paid back at close
    pnl_pct             NUMERIC(10,4),                        -- realized P&L %
    pnl_usd             NUMERIC(18,4),                        -- realized P&L $
    exit_reason         TEXT,                                  -- mirrors status's reason
    signal_payload      JSONB                                 -- entry conditions for audit
);
CREATE INDEX IF NOT EXISTS paper_positions_status
    ON paper_positions (status, opened_at_ms DESC);
CREATE INDEX IF NOT EXISTS paper_positions_recent
    ON paper_positions (opened_at_ms DESC);

-- Paper-trading equity snapshots (one row per minute or per equity change event).
CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
    ts_ms          BIGINT       PRIMARY KEY,
    equity_usd     NUMERIC(18,4) NOT NULL,
    realized_usd   NUMERIC(18,4) NOT NULL,                    -- $ realized from closed trades since start
    unrealized_usd NUMERIC(18,4) NOT NULL,                    -- mark-to-market of open positions
    n_open         INT          NOT NULL,
    n_closed       INT          NOT NULL,
    max_dd_pct     NUMERIC(10,4)                              -- running max drawdown
);
CREATE INDEX IF NOT EXISTS paper_equity_recent
    ON paper_equity_snapshots (ts_ms DESC);

-- Signal audit log — EVERY signal check is recorded for analysis.
-- Tracks: was signal generated? accepted? rejected why?
CREATE TABLE IF NOT EXISTS signal_audit (
    ts_ms           BIGINT    PRIMARY KEY,
    ret_7d          NUMERIC(10,4),                  -- 7-day return %
    active_side     CHAR(1),                        -- 'P', 'C', or NULL (dead zone)
    dead_zone       BOOLEAN   NOT NULL DEFAULT false,
    signal_generated BOOLEAN  NOT NULL DEFAULT false, -- did generator emit?
    accepted        BOOLEAN,                        -- was trade opened?
    reject_reason   TEXT,                           -- NULL, 'cb_active', 'no_signal',
                                                    -- 'insufficient_margin', 'no_option',
                                                    -- 'side_mismatch'
    spot            NUMERIC(18,4),
    signal_payload  JSONB                           -- full signal dict if generated
);
CREATE INDEX IF NOT EXISTS signal_audit_recent
    ON signal_audit (ts_ms DESC);
CREATE INDEX IF NOT EXISTS signal_audit_by_side
    ON signal_audit (active_side, accepted) WHERE active_side IS NOT NULL;

-- Paper-trading state singleton (CB cooldown, recent WR for dynamic sizing).
CREATE TABLE IF NOT EXISTS paper_state (
    id                       INT       PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    started_at_ms            BIGINT    NOT NULL,
    start_equity_usd         NUMERIC(18,4) NOT NULL,
    cb_cooldown_until_ms     BIGINT    NOT NULL DEFAULT 0,    -- 24h pause after 3 losses
    consec_losses            INT       NOT NULL DEFAULT 0,
    recent_pnls_json         JSONB     NOT NULL DEFAULT '[]'  -- last 10 pnls for dyn sizing
);

-- BTC unconditional short-straddle (24h cycle, dollar-margin SL). Structurally
-- separate from paper_positions: each cycle opens a CALL leg + a PUT leg sharing
-- a cycle_id, and the stop is sized off posted margin (not %-of-premium) — see
-- BTC_STRADDLE_HANDOFF.md and services/btc_straddle_sl.py.
CREATE TABLE IF NOT EXISTS btc_straddle_positions (
    id                  BIGSERIAL    PRIMARY KEY,
    cycle_id            BIGINT       NOT NULL,                -- pairs the C+P legs of one 24h cycle
    leg                 CHAR(1)      NOT NULL,                -- 'C' or 'P'
    opened_at_ms        BIGINT       NOT NULL,
    underlying_at_open  NUMERIC(18,4) NOT NULL,
    strike              NUMERIC(18,4) NOT NULL,
    expiry_ms           BIGINT       NOT NULL,
    contracts           NUMERIC(18,8) NOT NULL,               -- BTC qty (lot = 0.01)
    size_usd            NUMERIC(18,2) NOT NULL,
    entry_credit_usd    NUMERIC(18,4) NOT NULL,               -- per-contract premium received
    entry_credit_pct    NUMERIC(10,4) NOT NULL,
    entry_source        TEXT         NOT NULL,                -- 'bybit' | 'bs_fallback'
    status              TEXT         NOT NULL DEFAULT 'open', -- 'open' | 'closed_tp2' | 'closed_sl' | 'closed_time' | 'closed_reconciled'
    margin_per_lot_usd  NUMERIC(18,4) NOT NULL,                -- IM_RATE*strike+premium, per 0.01 BTC lot
    sl_dollar_trip_usd  NUMERIC(18,4) NOT NULL,                -- SL_DOLLAR_FRAC * margin_per_lot_usd, constant for the leg's life
    closed_at_ms        BIGINT,
    exit_debit_usd      NUMERIC(18,4),
    pnl_pct             NUMERIC(10,4),
    pnl_usd             NUMERIC(18,4),
    exit_reason         TEXT,
    signal_payload      JSONB
);
CREATE INDEX IF NOT EXISTS btc_straddle_positions_status
    ON btc_straddle_positions (status, opened_at_ms DESC);
CREATE INDEX IF NOT EXISTS btc_straddle_positions_cycle
    ON btc_straddle_positions (cycle_id);

CREATE TABLE IF NOT EXISTS btc_straddle_equity_snapshots (
    ts_ms          BIGINT       PRIMARY KEY,
    equity_usd     NUMERIC(18,4) NOT NULL,
    realized_usd   NUMERIC(18,4) NOT NULL,
    unrealized_usd NUMERIC(18,4) NOT NULL,
    n_open         INT          NOT NULL,
    n_closed       INT          NOT NULL,
    max_dd_pct     NUMERIC(10,4)
);
CREATE INDEX IF NOT EXISTS btc_straddle_equity_recent
    ON btc_straddle_equity_snapshots (ts_ms DESC);

CREATE TABLE IF NOT EXISTS btc_straddle_state (
    id                       INT       PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    started_at_ms            BIGINT    NOT NULL,
    start_equity_usd         NUMERIC(18,4) NOT NULL,
    last_cycle_id            BIGINT    NOT NULL DEFAULT 0,    -- last 24h cycle boundary opened
    cb_cooldown_until_ms     BIGINT    NOT NULL DEFAULT 0,
    consec_losses            INT       NOT NULL DEFAULT 0,
    recent_pnls_json         JSONB     NOT NULL DEFAULT '[]'
);

-- ETH unconditional short-straddle (24h cycle, dollar-margin SL) — separate
-- paper book from the ETH V3 signal bot (paper_*) and from the BTC straddle
-- above. Same shape as btc_straddle_* (ETH lot = 0.10, see eth_straddle_sl.py),
-- per ETH_STRADDLE_PAPER_BOT_HANDOFF.md.
CREATE TABLE IF NOT EXISTS eth_straddle_positions (
    id                  BIGSERIAL    PRIMARY KEY,
    cycle_id            BIGINT       NOT NULL,                -- pairs the C+P legs of one 24h cycle
    leg                 CHAR(1)      NOT NULL,                -- 'C' or 'P'
    opened_at_ms        BIGINT       NOT NULL,
    underlying_at_open  NUMERIC(18,4) NOT NULL,
    strike              NUMERIC(18,4) NOT NULL,
    expiry_ms           BIGINT       NOT NULL,
    contracts           NUMERIC(18,8) NOT NULL,               -- ETH qty (lot = 0.10)
    size_usd            NUMERIC(18,2) NOT NULL,
    entry_credit_usd    NUMERIC(18,4) NOT NULL,               -- per-contract premium received
    entry_credit_pct    NUMERIC(10,4) NOT NULL,
    entry_source        TEXT         NOT NULL,                -- 'bybit' | 'bs_fallback'
    status              TEXT         NOT NULL DEFAULT 'open', -- 'open' | 'closed_tp2' | 'closed_sl' | 'closed_time' | 'closed_reconciled'
    margin_per_lot_usd  NUMERIC(18,4) NOT NULL,                -- IM_RATE*strike+premium, per 0.10 ETH lot
    sl_dollar_trip_usd  NUMERIC(18,4) NOT NULL,                -- SL_DOLLAR_FRAC * margin_per_lot_usd, constant for the leg's life
    closed_at_ms        BIGINT,
    exit_debit_usd      NUMERIC(18,4),
    pnl_pct             NUMERIC(10,4),
    pnl_usd             NUMERIC(18,4),
    exit_reason         TEXT,
    signal_payload      JSONB
);
CREATE INDEX IF NOT EXISTS eth_straddle_positions_status
    ON eth_straddle_positions (status, opened_at_ms DESC);
CREATE INDEX IF NOT EXISTS eth_straddle_positions_cycle
    ON eth_straddle_positions (cycle_id);

CREATE TABLE IF NOT EXISTS eth_straddle_equity_snapshots (
    ts_ms          BIGINT       PRIMARY KEY,
    equity_usd     NUMERIC(18,4) NOT NULL,
    realized_usd   NUMERIC(18,4) NOT NULL,
    unrealized_usd NUMERIC(18,4) NOT NULL,
    n_open         INT          NOT NULL,
    n_closed       INT          NOT NULL,
    max_dd_pct     NUMERIC(10,4)
);
CREATE INDEX IF NOT EXISTS eth_straddle_equity_recent
    ON eth_straddle_equity_snapshots (ts_ms DESC);

CREATE TABLE IF NOT EXISTS eth_straddle_state (
    id                       INT       PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    started_at_ms            BIGINT    NOT NULL,
    start_equity_usd         NUMERIC(18,4) NOT NULL,
    last_cycle_id            BIGINT    NOT NULL DEFAULT 0,    -- last 24h cycle boundary opened
    cb_cooldown_until_ms     BIGINT    NOT NULL DEFAULT 0,
    consec_losses            INT       NOT NULL DEFAULT 0,
    recent_pnls_json         JSONB     NOT NULL DEFAULT '[]'
);

-- Mission Control: per-bot pause flag. Loops check `paused` each tick and skip
-- NEW entries only — monitoring/exits/heartbeat of already-open positions keep
-- running regardless (pausing must not abandon open risk).
CREATE TABLE IF NOT EXISTS bot_control (
    bot_name             TEXT    PRIMARY KEY,   -- 'eth_signal' | 'btc_straddle' | 'eth_straddle'
    paused               BOOLEAN NOT NULL DEFAULT false,
    close_all_requested  BOOLEAN NOT NULL DEFAULT false,  -- one-shot flag, loop clears it once done
    updated_at_ms        BIGINT  NOT NULL,
    updated_by           TEXT                              -- free-text actor label (e.g. 'dashboard')
);

-- Exchange accounts — one Bybit account PER BOT (own key, own wallet, no
-- shared capital): 'eth_signal', 'btc_straddle', 'eth_straddle'. Pre-seeded by
-- db.accounts_repo.ensure_all_bot_accounts(); see services/execution_config.py.
CREATE TABLE IF NOT EXISTS accounts (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT      NOT NULL UNIQUE,
    exchange    TEXT      NOT NULL DEFAULT 'bybit',
    is_active   BOOLEAN   NOT NULL DEFAULT true,
    created_at_ms BIGINT  NOT NULL
);

-- Encrypted exchange API credentials, keyed per account. Values are Fernet
-- ciphertext (services/credentials.py); the master key lives only in .env
-- (CREDENTIALS_MASTER_KEY), never in the DB.
CREATE TABLE IF NOT EXISTS exchange_credentials (
    account_id        BIGINT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    api_key_encrypted    TEXT NOT NULL,
    api_secret_encrypted TEXT NOT NULL,
    updated_at_ms     BIGINT NOT NULL
);

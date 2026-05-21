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

-- Paper-trading state singleton (CB cooldown, recent WR for dynamic sizing).
CREATE TABLE IF NOT EXISTS paper_state (
    id                       INT       PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    started_at_ms            BIGINT    NOT NULL,
    start_equity_usd         NUMERIC(18,4) NOT NULL,
    cb_cooldown_until_ms     BIGINT    NOT NULL DEFAULT 0,    -- 24h pause after 3 losses
    consec_losses            INT       NOT NULL DEFAULT 0,
    recent_pnls_json         JSONB     NOT NULL DEFAULT '[]'  -- last 10 pnls for dyn sizing
);

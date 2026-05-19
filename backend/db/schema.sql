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

CREATE TABLE daily_market_bars (
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL NOT NULL CHECK(close > 0),
    adjusted_close REAL NOT NULL CHECK(adjusted_close > 0),
    volume REAL CHECK(volume >= 0),
    currency TEXT NOT NULL,
    provider TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    PRIMARY KEY (symbol, session_date, provider)
);

CREATE TABLE corporate_actions (
    action_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    action_type TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    value REAL NOT NULL,
    provider TEXT NOT NULL,
    source_id TEXT REFERENCES source_documents(source_id),
    UNIQUE (symbol, action_type, effective_date, provider)
);

CREATE TABLE market_features (
    feature_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    as_of_session TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    value REAL,
    benchmark_symbol TEXT,
    calculation_version TEXT NOT NULL,
    input_start TEXT NOT NULL,
    input_end TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    UNIQUE (company_id, as_of_session, feature_name, calculation_version)
);

CREATE INDEX idx_market_bars_symbol_date
    ON daily_market_bars(symbol, session_date);
CREATE INDEX idx_market_features_company_date
    ON market_features(company_id, as_of_session);

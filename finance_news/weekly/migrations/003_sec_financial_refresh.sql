CREATE TABLE filings (
    accession_number TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    form TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    accepted_at TEXT,
    period_end TEXT,
    document_url TEXT NOT NULL,
    primary_document TEXT NOT NULL,
    source_id TEXT REFERENCES source_documents(source_id),
    processing_status TEXT NOT NULL
);

CREATE TABLE filing_sections (
    section_id TEXT PRIMARY KEY,
    accession_number TEXT NOT NULL REFERENCES filings(accession_number) ON DELETE CASCADE,
    section_type TEXT NOT NULL,
    title TEXT NOT NULL,
    text_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    extraction_version TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    UNIQUE (accession_number, section_type, extraction_version)
);

CREATE TABLE filing_refresh_state (
    company_id TEXT PRIMARY KEY REFERENCES companies(company_id),
    last_checked_at TEXT NOT NULL,
    latest_known_accession TEXT,
    latest_10k_accession TEXT,
    latest_10q_accession TEXT,
    latest_8k_accession TEXT,
    known_accessions_json TEXT NOT NULL,
    refresh_status TEXT NOT NULL,
    error_code TEXT
);

CREATE TABLE financial_observations (
    observation_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    period_type TEXT NOT NULL,
    fiscal_year INTEGER,
    fiscal_quarter INTEGER,
    period_start TEXT,
    period_end TEXT NOT NULL,
    filed_at TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    form TEXT NOT NULL,
    frame TEXT,
    derivation_type TEXT NOT NULL,
    source_observation_ids_json TEXT NOT NULL DEFAULT '[]',
    quality_status TEXT NOT NULL,
    UNIQUE (company_id, metric, period_end, accession_number, derivation_type)
);

CREATE TABLE derived_financial_metrics (
    metric_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    metric TEXT NOT NULL,
    value REAL,
    period_end TEXT NOT NULL,
    information_available_at TEXT NOT NULL,
    input_observation_ids_json TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    UNIQUE (company_id, metric, period_end, calculation_version)
);

CREATE TABLE equity_score_snapshots (
    score_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    score REAL CHECK(score BETWEEN 0 AND 100),
    label TEXT NOT NULL,
    available_components INTEGER NOT NULL CHECK(available_components BETWEEN 0 AND 4),
    eligible_coverage INTEGER NOT NULL CHECK(eligible_coverage IN (0, 1)),
    financial_period_end TEXT NOT NULL,
    information_available_at TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    component_json TEXT NOT NULL,
    source_accessions_json TEXT NOT NULL,
    source_ids_json TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    UNIQUE (company_id, financial_period_end, formula_version)
);

CREATE INDEX idx_filings_company_date ON filings(company_id, filing_date DESC);
CREATE INDEX idx_financial_observations_company_period
    ON financial_observations(company_id, period_end DESC);
CREATE INDEX idx_equity_scores_company_available
    ON equity_score_snapshots(company_id, information_available_at DESC);

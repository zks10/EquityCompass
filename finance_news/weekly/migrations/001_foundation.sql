CREATE TABLE companies (
    company_id TEXT PRIMARY KEY CHECK(length(company_id) = 10),
    current_ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE universe_snapshots (
    universe_id TEXT PRIMARY KEY,
    universe_name TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    source_path TEXT,
    source_hash TEXT,
    status TEXT NOT NULL
);

CREATE TABLE universe_members (
    universe_id TEXT NOT NULL REFERENCES universe_snapshots(universe_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    sector TEXT,
    industry TEXT,
    membership_status TEXT NOT NULL,
    identity_status TEXT NOT NULL,
    exclusion_reason TEXT,
    PRIMARY KEY (universe_id, company_id)
);

CREATE TABLE source_documents (
    source_id TEXT PRIMARY KEY,
    company_id TEXT REFERENCES companies(company_id),
    source_type TEXT NOT NULL,
    source_tier INTEGER CHECK(source_tier BETWEEN 1 AND 6),
    title TEXT NOT NULL,
    publisher TEXT,
    published_at TEXT,
    effective_at TEXT,
    collected_at TEXT NOT NULL,
    canonical_url TEXT,
    accession_number TEXT,
    local_artifact_path TEXT,
    content_hash TEXT,
    provider TEXT NOT NULL,
    provider_record_id TEXT,
    quality_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE weekly_runs (
    run_id TEXT PRIMARY KEY,
    week_ending TEXT NOT NULL,
    information_cutoff TEXT NOT NULL,
    market_data_through TEXT NOT NULL,
    universe_id TEXT NOT NULL REFERENCES universe_snapshots(universe_id),
    methodology_version TEXT NOT NULL,
    configuration_version TEXT NOT NULL,
    database_schema_version INTEGER NOT NULL,
    code_revision TEXT,
    status TEXT NOT NULL,
    current_stage TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    snapshot_id TEXT,
    snapshot_hash TEXT,
    warning_count INTEGER NOT NULL DEFAULT 0 CHECK(warning_count >= 0),
    error_summary TEXT
);

CREATE TABLE run_stages (
    run_id TEXT NOT NULL REFERENCES weekly_runs(run_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    input_count INTEGER NOT NULL DEFAULT 0 CHECK(input_count >= 0),
    success_count INTEGER NOT NULL DEFAULT 0 CHECK(success_count >= 0),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
    warning_count INTEGER NOT NULL DEFAULT 0 CHECK(warning_count >= 0),
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, stage_name)
);

CREATE TABLE run_company_status (
    run_id TEXT NOT NULL REFERENCES weekly_runs(run_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    refresh_status TEXT,
    eligibility_status TEXT,
    trigger_status TEXT,
    analysis_status TEXT,
    ranking_status TEXT,
    final_disposition TEXT,
    reason_codes_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (run_id, company_id)
);

CREATE TABLE detector_triggers (
    trigger_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES weekly_runs(run_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    detector TEXT NOT NULL,
    activated INTEGER NOT NULL CHECK(activated IN (0, 1)),
    evaluated_at TEXT NOT NULL,
    feature_values_json TEXT NOT NULL,
    thresholds_json TEXT NOT NULL,
    input_record_ids_json TEXT NOT NULL,
    quality_status TEXT NOT NULL,
    UNIQUE (run_id, company_id, detector)
);

CREATE TABLE detector_results (
    result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES weekly_runs(run_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    detector TEXT NOT NULL,
    applicable INTEGER NOT NULL CHECK(applicable IN (0, 1)),
    analysis_status TEXT NOT NULL,
    score REAL CHECK(score BETWEEN 0 AND 100),
    feature_scores_json TEXT NOT NULL DEFAULT '{}',
    positive_findings_json TEXT NOT NULL DEFAULT '[]',
    counter_findings_json TEXT NOT NULL DEFAULT '[]',
    unknowns_json TEXT NOT NULL DEFAULT '[]',
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    evidence_confidence REAL CHECK(evidence_confidence BETWEEN 0 AND 100),
    evaluated_at TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    model_version TEXT,
    CHECK(
        (applicable = 0 AND analysis_status = 'not_applicable' AND score IS NULL)
        OR (applicable = 1 AND analysis_status = 'completed' AND score IS NOT NULL)
        OR (applicable = 1 AND analysis_status IN ('failed', 'insufficient_evidence') AND score IS NULL)
    ),
    UNIQUE (run_id, company_id, detector)
);

CREATE TABLE eligibility_assessments (
    assessment_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES weekly_runs(run_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    data_gate_passed INTEGER NOT NULL CHECK(data_gate_passed IN (0, 1)),
    equity_score_gate_passed INTEGER NOT NULL CHECK(equity_score_gate_passed IN (0, 1)),
    critical_risk_gate_passed INTEGER NOT NULL CHECK(critical_risk_gate_passed IN (0, 1)),
    eligible INTEGER NOT NULL CHECK(eligible IN (0, 1)),
    equity_score REAL CHECK(equity_score BETWEEN 0 AND 100),
    minimum_equity_score REAL NOT NULL CHECK(minimum_equity_score BETWEEN 0 AND 100),
    freshness_json TEXT NOT NULL,
    failure_reasons_json TEXT NOT NULL,
    UNIQUE (run_id, company_id)
);

CREATE TABLE risk_assessments (
    assessment_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES weekly_runs(run_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    opportunity_risk_score REAL NOT NULL CHECK(opportunity_risk_score BETWEEN 0 AND 100),
    financial_risk REAL CHECK(financial_risk BETWEEN 0 AND 100),
    business_risk REAL CHECK(business_risk BETWEEN 0 AND 100),
    event_risk REAL CHECK(event_risk BETWEEN 0 AND 100),
    valuation_risk REAL CHECK(valuation_risk BETWEEN 0 AND 100),
    market_risk REAL CHECK(market_risk BETWEEN 0 AND 100),
    evidence_risk REAL CHECK(evidence_risk BETWEEN 0 AND 100),
    critical_flags_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    UNIQUE (run_id, company_id)
);

CREATE TABLE ranking_assessments (
    ranking_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES weekly_runs(run_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    primary_detector TEXT NOT NULL,
    primary_strength REAL NOT NULL CHECK(primary_strength BETWEEN 0 AND 100),
    supporting_detectors_json TEXT NOT NULL,
    supporting_families_json TEXT NOT NULL,
    signal_convergence_bonus REAL NOT NULL,
    evidence_confidence REAL NOT NULL CHECK(evidence_confidence BETWEEN 0 AND 100),
    freshness REAL NOT NULL CHECK(freshness BETWEEN 0 AND 100),
    opportunity_risk_score REAL NOT NULL CHECK(opportunity_risk_score BETWEEN 0 AND 100),
    thesis_momentum TEXT NOT NULL,
    momentum_adjustment REAL NOT NULL,
    weighted_base_score REAL NOT NULL,
    final_score REAL NOT NULL,
    pre_diversification_rank INTEGER CHECK(pre_diversification_rank > 0),
    minimum_threshold_passed INTEGER NOT NULL CHECK(minimum_threshold_passed IN (0, 1)),
    formula_version TEXT NOT NULL,
    calculation_inputs_json TEXT NOT NULL,
    UNIQUE (run_id, company_id)
);

CREATE TABLE final_selections (
    run_id TEXT NOT NULL REFERENCES weekly_runs(run_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    selected INTEGER NOT NULL CHECK(selected IN (0, 1)),
    final_rank INTEGER CHECK(final_rank > 0),
    pre_diversification_rank INTEGER NOT NULL CHECK(pre_diversification_rank > 0),
    sector TEXT,
    systemic_event_cluster_id TEXT,
    decision_reason TEXT NOT NULL,
    CHECK((selected = 1 AND final_rank IS NOT NULL) OR (selected = 0 AND final_rank IS NULL)),
    PRIMARY KEY (run_id, company_id),
    UNIQUE (run_id, final_rank)
);

CREATE INDEX idx_source_documents_company ON source_documents(company_id, published_at);
CREATE INDEX idx_detector_triggers_run ON detector_triggers(run_id, activated);
CREATE INDEX idx_detector_results_run ON detector_results(run_id, analysis_status);
CREATE INDEX idx_rankings_run_score ON ranking_assessments(run_id, final_score DESC);

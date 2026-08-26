CREATE TABLE evidence_items (
    evidence_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES source_documents(source_id),
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    evidence_type TEXT NOT NULL,
    claim TEXT NOT NULL,
    normalized_value_json TEXT NOT NULL DEFAULT '{}',
    direction TEXT NOT NULL,
    materiality REAL NOT NULL CHECK(materiality BETWEEN 0 AND 100),
    reliability REAL NOT NULL CHECK(reliability BETWEEN 0 AND 100),
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 100),
    effective_at TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    source_location TEXT NOT NULL,
    fact_interpretation_type TEXT NOT NULL CHECK(
        fact_interpretation_type IN ('reported_fact', 'calculated_fact', 'interpretation')
    ),
    extraction_method TEXT NOT NULL,
    extraction_version TEXT NOT NULL,
    model_version TEXT,
    contradiction_group_id TEXT,
    status TEXT NOT NULL
);

CREATE TABLE event_threads (
    event_id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(company_id),
    event_family TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    event_started_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    current_status TEXT NOT NULL CHECK(current_status IN (
        'detected', 'unresolved', 'partially_clarified', 'resolved_positive',
        'confirmed_negative', 'stale_irrelevant'
    )),
    materiality REAL NOT NULL CHECK(materiality BETWEEN 0 AND 100),
    initial_severity REAL NOT NULL CHECK(initial_severity BETWEEN 0 AND 100),
    current_severity REAL NOT NULL CHECK(current_severity BETWEEN 0 AND 100),
    fundamental_impact TEXT NOT NULL CHECK(fundamental_impact IN (
        'none_visible', 'limited', 'moderate', 'severe', 'unknown'
    )),
    evidence_confidence REAL NOT NULL CHECK(evidence_confidence BETWEEN 0 AND 100),
    primary_evidence_id TEXT REFERENCES evidence_items(evidence_id),
    systemic_event_cluster_id TEXT,
    methodology_version TEXT NOT NULL
);

CREATE TABLE event_evidence (
    event_id TEXT NOT NULL REFERENCES event_threads(event_id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence_items(evidence_id),
    relationship TEXT NOT NULL CHECK(relationship IN (
        'primary', 'supporting', 'counter', 'context'
    )),
    attached_at TEXT NOT NULL,
    match_confidence REAL NOT NULL CHECK(match_confidence BETWEEN 0 AND 100),
    PRIMARY KEY (event_id, evidence_id)
);

CREATE TABLE event_market_anchors (
    anchor_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES event_threads(event_id) ON DELETE CASCADE,
    anchor_type TEXT NOT NULL,
    anchor_session TEXT NOT NULL,
    anchor_price REAL NOT NULL CHECK(anchor_price > 0),
    first_reaction_session TEXT NOT NULL,
    first_reaction_price REAL NOT NULL CHECK(first_reaction_price > 0),
    initial_reaction REAL NOT NULL,
    maximum_drawdown REAL NOT NULL,
    benchmark_symbol TEXT NOT NULL,
    benchmark_initial_reaction REAL NOT NULL,
    benchmark_adjusted_initial_reaction REAL NOT NULL,
    calculation_version TEXT NOT NULL,
    UNIQUE (event_id, anchor_type, calculation_version)
);

CREATE INDEX idx_evidence_company_effective
    ON evidence_items(company_id, effective_at DESC);
CREATE INDEX idx_events_company_updated
    ON event_threads(company_id, last_updated_at DESC);
CREATE INDEX idx_event_evidence_evidence
    ON event_evidence(evidence_id);

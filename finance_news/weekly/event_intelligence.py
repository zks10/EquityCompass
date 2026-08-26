"""Minimal evidence, Event Thread, and market-anchor infrastructure."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence

from finance_news.weekly.market import DailyBar
from finance_news.weekly.models import FactInterpretationType
from finance_news.weekly.storage import transaction


EVENT_METHODOLOGY_VERSION = "event-intelligence-v1"
MARKET_ANCHOR_VERSION = "event-market-anchor-v1"


class EventIntelligenceError(ValueError):
    """Raised when event evidence cannot be represented safely."""


class EvidenceDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    CLARIFYING = "clarifying"
    CONTRADICTORY = "contradictory"
    UNKNOWN = "unknown"


class EventStatus(str, Enum):
    DETECTED = "detected"
    UNRESOLVED = "unresolved"
    PARTIALLY_CLARIFIED = "partially_clarified"
    RESOLVED_POSITIVE = "resolved_positive"
    CONFIRMED_NEGATIVE = "confirmed_negative"
    STALE_IRRELEVANT = "stale_irrelevant"


class FundamentalImpact(str, Enum):
    NONE_VISIBLE = "none_visible"
    LIMITED = "limited"
    MODERATE = "moderate"
    SEVERE = "severe"
    UNKNOWN = "unknown"


class EventFamily(str, Enum):
    EARNINGS_GUIDANCE = "earnings_guidance"
    OPERATIONS_SUPPLY = "operations_supply"
    CUSTOMER_CONTRACT = "customer_contract"
    LEGAL_LITIGATION = "legal_litigation"
    REGULATORY = "regulatory"
    ACCOUNTING_GOVERNANCE = "accounting_governance"
    FINANCING_LIQUIDITY = "financing_liquidity"
    PRODUCT_TECHNOLOGY = "product_technology"
    MANAGEMENT = "management"
    CAPITAL_ALLOCATION = "capital_allocation"
    MERGER_ACQUISITION = "merger_acquisition"
    MACRO_INDUSTRY = "macro_industry"


class EvidenceRelationship(str, Enum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    COUNTER = "counter"
    CONTEXT = "context"


@dataclass(frozen=True)
class SourceDocument:
    source_id: str
    company_id: str
    source_type: str
    source_tier: int
    title: str
    publisher: str
    published_at: datetime
    effective_at: datetime
    collected_at: datetime
    canonical_url: str
    provider: str
    quality_status: str = "complete"
    content_hash: str | None = None
    accession_number: str | None = None
    local_artifact_path: str | None = None
    provider_record_id: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.title.strip() or not self.provider.strip():
            raise EventIntelligenceError("Source ID, title, and provider are required.")
        if not 1 <= self.source_tier <= 6:
            raise EventIntelligenceError("Source tier must be between 1 and 6.")
        for value in (self.published_at, self.effective_at, self.collected_at):
            _require_aware(value, "Source timestamps")


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    source_id: str
    company_id: str
    evidence_type: str
    claim: str
    direction: EvidenceDirection
    materiality: float
    reliability: float
    confidence: float
    effective_at: datetime
    extracted_at: datetime
    source_location: str
    fact_type: FactInterpretationType
    extraction_method: str
    extraction_version: str
    normalized_value: dict[str, Any] | None = None
    model_version: str | None = None
    contradiction_group_id: str | None = None
    status: str = "active"

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.evidence_id, self.source_id, self.evidence_type, self.claim,
            self.source_location, self.extraction_method, self.extraction_version,
        )):
            raise EventIntelligenceError("Evidence identity, claim, location, and extraction provenance are required.")
        for value, label in (
            (self.materiality, "Evidence materiality"),
            (self.reliability, "Evidence reliability"),
            (self.confidence, "Evidence confidence"),
        ):
            _score(value, label)
        _require_aware(self.effective_at, "Evidence effective timestamp")
        _require_aware(self.extracted_at, "Evidence extraction timestamp")


@dataclass(frozen=True)
class EventThread:
    event_id: str
    company_id: str
    event_family: EventFamily
    title: str
    summary: str
    detected_at: datetime
    event_started_at: datetime
    status: EventStatus
    materiality: float
    initial_severity: float
    current_severity: float
    fundamental_impact: FundamentalImpact
    evidence_confidence: float
    primary_evidence_id: str | None = None
    systemic_event_cluster_id: str | None = None
    methodology_version: str = EVENT_METHODOLOGY_VERSION

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.event_id, self.company_id, self.title, self.summary)):
            raise EventIntelligenceError("Event identity, company, title, and summary are required.")
        _require_aware(self.detected_at, "Event detection timestamp")
        _require_aware(self.event_started_at, "Event start timestamp")
        for value, label in (
            (self.materiality, "Event materiality"),
            (self.initial_severity, "Initial severity"),
            (self.current_severity, "Current severity"),
            (self.evidence_confidence, "Event evidence confidence"),
        ):
            _score(value, label)


@dataclass(frozen=True)
class EventMarketAnchor:
    anchor_id: str
    event_id: str
    anchor_type: str
    anchor_session: str
    anchor_price: float
    first_reaction_session: str
    first_reaction_price: float
    initial_reaction: float
    maximum_drawdown: float
    benchmark_symbol: str
    benchmark_initial_reaction: float
    benchmark_adjusted_initial_reaction: float
    calculation_version: str = MARKET_ANCHOR_VERSION


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EventIntelligenceError(f"{label} must be timezone-aware.")


def _utc(value: datetime) -> str:
    _require_aware(value, "Timestamp")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _score(value: float, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        raise EventIntelligenceError(f"{label} must be between 0 and 100.")


def store_source_document(connection: sqlite3.Connection, source: SourceDocument) -> None:
    """Persist source metadata idempotently and reject source-ID drift."""
    metadata_json = json.dumps(source.metadata or {}, sort_keys=True)
    values = (
        source.company_id, source.source_type, source.source_tier, source.title,
        source.publisher, _utc(source.published_at), _utc(source.effective_at),
        _utc(source.collected_at), source.canonical_url, source.accession_number,
        source.local_artifact_path, source.content_hash, source.provider,
        source.provider_record_id, source.quality_status, metadata_json,
    )
    existing = connection.execute(
        "SELECT company_id, source_type, source_tier, title, publisher, published_at, effective_at, "
        "collected_at, canonical_url, accession_number, local_artifact_path, content_hash, provider, "
        "provider_record_id, quality_status, metadata_json FROM source_documents WHERE source_id = ?",
        (source.source_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != values:
            raise EventIntelligenceError(f"Source ID {source.source_id} already has different content.")
        return
    try:
        with transaction(connection):
            connection.execute(
                "INSERT INTO source_documents "
                "(source_id, company_id, source_type, source_tier, title, publisher, published_at, "
                "effective_at, collected_at, canonical_url, accession_number, local_artifact_path, "
                "content_hash, provider, provider_record_id, quality_status, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source.source_id, *values),
            )
    except sqlite3.Error as exc:
        raise EventIntelligenceError(f"Could not store source document: {exc}") from exc


def store_evidence_item(connection: sqlite3.Connection, evidence: EvidenceItem) -> None:
    """Persist one factual, calculated, or interpretive evidence claim."""
    values = (
        evidence.source_id, evidence.company_id, evidence.evidence_type, evidence.claim,
        json.dumps(evidence.normalized_value or {}, sort_keys=True), evidence.direction.value,
        evidence.materiality, evidence.reliability, evidence.confidence,
        _utc(evidence.effective_at), _utc(evidence.extracted_at), evidence.source_location,
        evidence.fact_type.value, evidence.extraction_method, evidence.extraction_version,
        evidence.model_version, evidence.contradiction_group_id, evidence.status,
    )
    existing = connection.execute(
        "SELECT source_id, company_id, evidence_type, claim, normalized_value_json, direction, "
        "materiality, reliability, confidence, effective_at, extracted_at, source_location, "
        "fact_interpretation_type, extraction_method, extraction_version, model_version, "
        "contradiction_group_id, status FROM evidence_items WHERE evidence_id = ?",
        (evidence.evidence_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != values:
            raise EventIntelligenceError(
                f"Evidence ID {evidence.evidence_id} already has different content."
            )
        return
    try:
        with transaction(connection):
            source = connection.execute(
                "SELECT company_id FROM source_documents WHERE source_id = ?", (evidence.source_id,)
            ).fetchone()
            if source is None or source["company_id"] != evidence.company_id:
                raise EventIntelligenceError("Evidence source is missing or belongs to another company.")
            connection.execute(
                "INSERT INTO evidence_items "
                "(evidence_id, source_id, company_id, evidence_type, claim, normalized_value_json, "
                "direction, materiality, reliability, confidence, effective_at, extracted_at, "
                "source_location, fact_interpretation_type, extraction_method, extraction_version, "
                "model_version, contradiction_group_id, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (evidence.evidence_id, *values),
            )
    except sqlite3.Error as exc:
        raise EventIntelligenceError(f"Could not store evidence item: {exc}") from exc


def create_event_thread(connection: sqlite3.Connection, event: EventThread) -> None:
    """Create an initial Event Thread without performing lifecycle transitions."""
    if event.status not in (EventStatus.DETECTED, EventStatus.UNRESOLVED):
        raise EventIntelligenceError("New Event Threads may only start detected or unresolved.")
    try:
        with transaction(connection):
            if event.primary_evidence_id is not None:
                evidence = connection.execute(
                    "SELECT company_id FROM evidence_items WHERE evidence_id = ?",
                    (event.primary_evidence_id,),
                ).fetchone()
                if evidence is None or evidence["company_id"] != event.company_id:
                    raise EventIntelligenceError("Primary evidence is missing or belongs to another company.")
            connection.execute(
                "INSERT INTO event_threads "
                "(event_id, company_id, event_family, title, summary, detected_at, event_started_at, "
                "last_updated_at, current_status, materiality, initial_severity, current_severity, "
                "fundamental_impact, evidence_confidence, primary_evidence_id, "
                "systemic_event_cluster_id, methodology_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id, event.company_id, event.event_family.value, event.title,
                    event.summary, _utc(event.detected_at), _utc(event.event_started_at),
                    _utc(event.detected_at), event.status.value, event.materiality,
                    event.initial_severity, event.current_severity, event.fundamental_impact.value,
                    event.evidence_confidence, event.primary_evidence_id,
                    event.systemic_event_cluster_id, event.methodology_version,
                ),
            )
    except sqlite3.Error as exc:
        raise EventIntelligenceError(f"Could not create Event Thread: {exc}") from exc


def attach_evidence(
    connection: sqlite3.Connection,
    event_id: str,
    evidence_id: str,
    relationship: EvidenceRelationship,
    *,
    attached_at: datetime,
    match_confidence: float,
) -> None:
    """Attach company-consistent evidence to an event without changing event state."""
    _score(match_confidence, "Event match confidence")
    try:
        with transaction(connection):
            event = connection.execute(
                "SELECT company_id FROM event_threads WHERE event_id = ?", (event_id,)
            ).fetchone()
            evidence = connection.execute(
                "SELECT company_id FROM evidence_items WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
            if event is None or evidence is None:
                raise EventIntelligenceError("Event and evidence must exist before attachment.")
            if event["company_id"] != evidence["company_id"]:
                raise EventIntelligenceError("Event and evidence must belong to the same company.")
            connection.execute(
                "INSERT INTO event_evidence "
                "(event_id, evidence_id, relationship, attached_at, match_confidence) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(event_id, evidence_id) DO UPDATE SET "
                "relationship = excluded.relationship, attached_at = excluded.attached_at, "
                "match_confidence = excluded.match_confidence",
                (event_id, evidence_id, relationship.value, _utc(attached_at), match_confidence),
            )
    except sqlite3.Error as exc:
        raise EventIntelligenceError(f"Could not attach event evidence: {exc}") from exc


def calculate_market_anchor(
    event_id: str,
    became_public_at: datetime,
    stock_bars: Sequence[DailyBar],
    benchmark_bars: Sequence[DailyBar],
) -> EventMarketAnchor:
    """Use the final completed pre-event close as the deterministic event anchor."""
    _require_aware(became_public_at, "Event publication timestamp")
    if not stock_bars or not benchmark_bars:
        raise EventIntelligenceError("Stock and benchmark bars are required for a market anchor.")
    stock = {bar.session_date: bar for bar in stock_bars}
    benchmark = {bar.session_date: bar for bar in benchmark_bars}
    event_date = became_public_at.date()
    aligned = sorted(stock.keys() & benchmark.keys())
    prior = [session for session in aligned if session < event_date]
    reactions = [session for session in aligned if session >= event_date]
    if not prior or not reactions:
        raise EventIntelligenceError("Market anchor requires aligned sessions before and after the event.")
    anchor_session = prior[-1]
    first_reaction_session = reactions[0]
    anchor_price = stock[anchor_session].adjusted_close
    first_price = stock[first_reaction_session].adjusted_close
    benchmark_anchor = benchmark[anchor_session].adjusted_close
    benchmark_first = benchmark[first_reaction_session].adjusted_close
    initial_reaction = first_price / anchor_price - 1
    benchmark_reaction = benchmark_first / benchmark_anchor - 1
    post_event_prices = [stock[session].adjusted_close for session in aligned if session >= first_reaction_session]
    maximum_drawdown = min(price / anchor_price - 1 for price in post_event_prices)
    return EventMarketAnchor(
        anchor_id=f"{event_id}:prior_close:{MARKET_ANCHOR_VERSION}", event_id=event_id,
        anchor_type="prior_close", anchor_session=anchor_session.isoformat(),
        anchor_price=anchor_price, first_reaction_session=first_reaction_session.isoformat(),
        first_reaction_price=first_price, initial_reaction=initial_reaction,
        maximum_drawdown=maximum_drawdown, benchmark_symbol=benchmark[first_reaction_session].symbol,
        benchmark_initial_reaction=benchmark_reaction,
        benchmark_adjusted_initial_reaction=initial_reaction - benchmark_reaction,
    )


def store_market_anchor(connection: sqlite3.Connection, anchor: EventMarketAnchor) -> None:
    """Persist a reproducible event anchor and reject silent recalculation drift."""
    values = (
        anchor.event_id, anchor.anchor_type, anchor.anchor_session, anchor.anchor_price,
        anchor.first_reaction_session, anchor.first_reaction_price, anchor.initial_reaction,
        anchor.maximum_drawdown, anchor.benchmark_symbol, anchor.benchmark_initial_reaction,
        anchor.benchmark_adjusted_initial_reaction, anchor.calculation_version,
    )
    existing = connection.execute(
        "SELECT event_id, anchor_type, anchor_session, anchor_price, first_reaction_session, "
        "first_reaction_price, initial_reaction, maximum_drawdown, benchmark_symbol, "
        "benchmark_initial_reaction, benchmark_adjusted_initial_reaction, calculation_version "
        "FROM event_market_anchors WHERE anchor_id = ?", (anchor.anchor_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != values:
            raise EventIntelligenceError("Stored market anchor conflicts with recalculated values.")
        return
    try:
        with transaction(connection):
            connection.execute(
                "INSERT INTO event_market_anchors "
                "(anchor_id, event_id, anchor_type, anchor_session, anchor_price, "
                "first_reaction_session, first_reaction_price, initial_reaction, maximum_drawdown, "
                "benchmark_symbol, benchmark_initial_reaction, benchmark_adjusted_initial_reaction, "
                "calculation_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (anchor.anchor_id, *values),
            )
    except sqlite3.Error as exc:
        raise EventIntelligenceError(f"Could not store event market anchor: {exc}") from exc


__all__ = [
    "EVENT_METHODOLOGY_VERSION", "MARKET_ANCHOR_VERSION", "EventFamily",
    "EventIntelligenceError", "EventMarketAnchor", "EventStatus", "EventThread",
    "EvidenceDirection", "EvidenceItem", "EvidenceRelationship", "FundamentalImpact",
    "SourceDocument", "attach_evidence", "calculate_market_anchor", "create_event_thread",
    "store_evidence_item", "store_market_anchor", "store_source_document",
]

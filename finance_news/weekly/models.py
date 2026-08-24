"""Typed, serializable contracts for published weekly opportunities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


class DetectorName(str, Enum):
    MARKET_OVERREACTION = "market_overreaction"
    NEGATIVE_NEWS_RESOLUTION = "negative_news_resolution"
    VALUATION_RESET = "valuation_reset"
    FUNDAMENTAL_INFLECTION = "fundamental_inflection"
    TEMPORARY_HEADWIND = "temporary_headwind"
    EMERGING_CATALYST = "emerging_catalyst"


class DetectorFamily(str, Enum):
    MARKET_DISLOCATION = "market_dislocation"
    EVENT_EVOLUTION = "event_evolution"
    BUSINESS_IMPROVEMENT = "business_improvement"
    FORWARD_CATALYST = "forward_catalyst"


DETECTOR_FAMILIES = {
    DetectorName.MARKET_OVERREACTION: DetectorFamily.MARKET_DISLOCATION,
    DetectorName.VALUATION_RESET: DetectorFamily.MARKET_DISLOCATION,
    DetectorName.NEGATIVE_NEWS_RESOLUTION: DetectorFamily.EVENT_EVOLUTION,
    DetectorName.TEMPORARY_HEADWIND: DetectorFamily.EVENT_EVOLUTION,
    DetectorName.FUNDAMENTAL_INFLECTION: DetectorFamily.BUSINESS_IMPROVEMENT,
    DetectorName.EMERGING_CATALYST: DetectorFamily.FORWARD_CATALYST,
}


class AnalysisStatus(str, Enum):
    COMPLETED = "completed"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ThesisMomentum(str, Enum):
    STRENGTHENING = "strengthening"
    STABLE = "stable"
    WEAKENING = "weakening"


class FinalDisposition(str, Enum):
    NOT_TRIGGERED = "not_triggered"
    OBSERVE_ONLY = "observe_only"
    DATA_INCOMPLETE = "data_incomplete"
    CRITICAL_RISK = "critical_risk"
    ANALYSIS_FAILED = "analysis_failed"
    BELOW_CONFIDENCE_THRESHOLD = "below_confidence_threshold"
    BELOW_RANK_THRESHOLD = "below_rank_threshold"
    DIVERSIFICATION_EXCLUDED = "diversification_excluded"
    SELECTED = "selected"


class EvidenceRole(str, Enum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    COUNTER = "counter"


class FactInterpretationType(str, Enum):
    REPORTED_FACT = "reported_fact"
    CALCULATED_FACT = "calculated_fact"
    INTERPRETATION = "interpretation"


def _score(value: float, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
        raise ValueError(f"{label} must be between 0 and 100.")


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware.")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _aware(parsed, "timestamp")
    return parsed


@dataclass(frozen=True)
class DetectorResult:
    detector: DetectorName
    applicable: bool
    analysis_status: AnalysisStatus
    score: float | None
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.applicable:
            if self.score is not None or self.analysis_status is not AnalysisStatus.NOT_APPLICABLE:
                raise ValueError("A non-applicable detector requires a null score and not_applicable status.")
        elif self.analysis_status is AnalysisStatus.COMPLETED:
            if self.score is None:
                raise ValueError("A completed applicable detector requires a score.")
            _score(self.score, "Detector score")
        elif self.score is not None:
            raise ValueError("Failed or insufficient-evidence analyses cannot contain a score.")


@dataclass(frozen=True)
class SupportingSignal:
    detector: DetectorName
    family: DetectorFamily
    score: float

    def __post_init__(self) -> None:
        _score(self.score, "Supporting signal score")
        if DETECTOR_FAMILIES[self.detector] is not self.family:
            raise ValueError("Supporting signal family does not match its detector.")


@dataclass(frozen=True)
class CriticalRiskFlag:
    code: str
    explanation: str


@dataclass(frozen=True)
class CrossDetectorAssessment:
    evidence_confidence: float
    opportunity_risk: float
    freshness: float
    thesis_momentum: ThesisMomentum
    momentum_adjustment: float

    def __post_init__(self) -> None:
        _score(self.evidence_confidence, "Evidence Confidence")
        _score(self.opportunity_risk, "Opportunity Risk")
        _score(self.freshness, "Freshness")


@dataclass(frozen=True)
class RankingCalculation:
    primary_strength: float
    evidence_confidence: float
    freshness: float
    opportunity_risk: float
    signal_convergence_bonus: float
    momentum_adjustment: float
    final_score: float

    def __post_init__(self) -> None:
        for value, label in (
            (self.primary_strength, "Primary strength"),
            (self.evidence_confidence, "Evidence Confidence"),
            (self.freshness, "Freshness"),
            (self.opportunity_risk, "Opportunity Risk"),
        ):
            _score(value, label)


@dataclass(frozen=True)
class PublishedEvidence:
    evidence_id: str
    fact_type: FactInterpretationType
    claim: str
    role: EvidenceRole
    source_type: str
    source_title: str
    published_at: datetime
    url: str
    source_location: str

    def __post_init__(self) -> None:
        _aware(self.published_at, "Evidence publication timestamp")


@dataclass(frozen=True)
class PublishedOpportunity:
    rank: int
    company_id: str
    ticker: str
    company_name: str
    sector: str
    industry: str
    primary_detector: DetectorName
    primary_strength: float
    supporting_signals: tuple[SupportingSignal, ...]
    ranking: RankingCalculation
    equity_score: float
    cross_detector: CrossDetectorAssessment
    critical_risk_flags: tuple[CriticalRiskFlag, ...]
    why_now: str
    supporting_case: str
    counter_case: str
    what_would_invalidate: str
    evidence: tuple[PublishedEvidence, ...]

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError("Published opportunity rank must be positive.")
        _score(self.primary_strength, "Primary strength")
        _score(self.equity_score, "Equity Score")


@dataclass(frozen=True)
class WeeklyCoverage:
    universe_members: int
    refreshed: int
    triggered: int
    analyzed: int
    qualified: int
    published: int

    def __post_init__(self) -> None:
        if any(value < 0 for value in (
            self.universe_members, self.refreshed, self.triggered,
            self.analyzed, self.qualified, self.published,
        )):
            raise ValueError("Coverage counts cannot be negative.")


@dataclass(frozen=True)
class WeeklySnapshot:
    schema_version: int
    snapshot_id: str
    week_ending: date
    published_at: datetime
    information_cutoff: datetime
    market_data_through: date
    universe_id: str
    universe_name: str
    methodology_version: str
    configuration_version: str
    formula_version: str
    coverage: WeeklyCoverage
    warnings: tuple[str, ...]
    opportunities: tuple[PublishedOpportunity, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("Only weekly snapshot schema version 1 is supported.")
        _aware(self.published_at, "Snapshot publication timestamp")
        _aware(self.information_cutoff, "Snapshot information cutoff")
        if len(self.opportunities) > 5:
            raise ValueError("A weekly snapshot cannot contain more than five opportunities.")
        ranks = [opportunity.rank for opportunity in self.opportunities]
        if len(ranks) != len(set(ranks)):
            raise ValueError("Published opportunity ranks must be unique.")

    def to_dict(self) -> dict[str, Any]:
        """Return the explicit schema-versioned publication representation."""
        return {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "week_ending": self.week_ending.isoformat(),
            "published_at": _iso(self.published_at),
            "information_cutoff": _iso(self.information_cutoff),
            "market_data_through": self.market_data_through.isoformat(),
            "universe": {"id": self.universe_id, "name": self.universe_name},
            "methodology": {
                "version": self.methodology_version,
                "configuration_version": self.configuration_version,
                "formula_version": self.formula_version,
            },
            "coverage": self.coverage.__dict__,
            "warnings": list(self.warnings),
            "opportunities": [_opportunity_dict(item) for item in self.opportunities],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WeeklySnapshot":
        """Decode the version-1 publication contract."""
        universe = payload["universe"]
        methodology = payload["methodology"]
        coverage = WeeklyCoverage(**payload["coverage"])
        return cls(
            schema_version=payload["schema_version"],
            snapshot_id=payload["snapshot_id"],
            week_ending=date.fromisoformat(payload["week_ending"]),
            published_at=_datetime(payload["published_at"]),
            information_cutoff=_datetime(payload["information_cutoff"]),
            market_data_through=date.fromisoformat(payload["market_data_through"]),
            universe_id=universe["id"],
            universe_name=universe["name"],
            methodology_version=methodology["version"],
            configuration_version=methodology["configuration_version"],
            formula_version=methodology["formula_version"],
            coverage=coverage,
            warnings=tuple(payload.get("warnings", [])),
            opportunities=tuple(_opportunity(item) for item in payload["opportunities"]),
        )


def _opportunity_dict(item: PublishedOpportunity) -> dict[str, Any]:
    return {
        "rank": item.rank,
        "company": {
            "company_id": item.company_id, "ticker": item.ticker,
            "name": item.company_name, "sector": item.sector, "industry": item.industry,
        },
        "primary_thesis": {"detector": item.primary_detector.value, "strength": item.primary_strength},
        "supporting_signals": [
            {"detector": signal.detector.value, "family": signal.family.value, "score": signal.score}
            for signal in item.supporting_signals
        ],
        "ranking": {
            "primary_strength": item.ranking.primary_strength,
            "evidence_confidence": item.ranking.evidence_confidence,
            "freshness": item.ranking.freshness,
            "opportunity_risk": item.ranking.opportunity_risk,
            "signal_convergence_bonus": item.ranking.signal_convergence_bonus,
            "momentum_adjustment": item.ranking.momentum_adjustment,
            "final_score": round(item.ranking.final_score, 1),
        },
        "equity_score": item.equity_score,
        "cross_detector": {
            "evidence_confidence": item.cross_detector.evidence_confidence,
            "opportunity_risk": item.cross_detector.opportunity_risk,
            "freshness": item.cross_detector.freshness,
            "thesis_momentum": item.cross_detector.thesis_momentum.value,
            "momentum_adjustment": item.cross_detector.momentum_adjustment,
        },
        "critical_risk_flags": [flag.__dict__ for flag in item.critical_risk_flags],
        "explanation": {
            "why_now": item.why_now,
            "supporting_case": item.supporting_case,
            "counter_case": item.counter_case,
            "what_would_invalidate": item.what_would_invalidate,
        },
        "evidence": [
            {
                "evidence_id": evidence.evidence_id,
                "fact_type": evidence.fact_type.value,
                "claim": evidence.claim,
                "role": evidence.role.value,
                "source": {
                    "type": evidence.source_type, "title": evidence.source_title,
                    "published_at": _iso(evidence.published_at), "url": evidence.url,
                },
                "source_location": evidence.source_location,
            }
            for evidence in item.evidence
        ],
    }


def _opportunity(payload: dict[str, Any]) -> PublishedOpportunity:
    company = payload["company"]
    primary = payload["primary_thesis"]
    ranking = payload["ranking"]
    cross = payload["cross_detector"]
    explanation = payload["explanation"]
    return PublishedOpportunity(
        rank=payload["rank"], company_id=company["company_id"], ticker=company["ticker"],
        company_name=company["name"], sector=company["sector"], industry=company["industry"],
        primary_detector=DetectorName(primary["detector"]), primary_strength=primary["strength"],
        supporting_signals=tuple(SupportingSignal(DetectorName(s["detector"]), DetectorFamily(s["family"]), s["score"]) for s in payload["supporting_signals"]),
        ranking=RankingCalculation(**ranking), equity_score=payload["equity_score"],
        cross_detector=CrossDetectorAssessment(
            evidence_confidence=cross["evidence_confidence"], opportunity_risk=cross["opportunity_risk"],
            freshness=cross["freshness"], thesis_momentum=ThesisMomentum(cross["thesis_momentum"]),
            momentum_adjustment=cross["momentum_adjustment"],
        ),
        critical_risk_flags=tuple(CriticalRiskFlag(**flag) for flag in payload["critical_risk_flags"]),
        why_now=explanation["why_now"], supporting_case=explanation["supporting_case"],
        counter_case=explanation["counter_case"], what_would_invalidate=explanation["what_would_invalidate"],
        evidence=tuple(PublishedEvidence(
            evidence_id=e["evidence_id"], fact_type=FactInterpretationType(e["fact_type"]),
            claim=e["claim"], role=EvidenceRole(e["role"]), source_type=e["source"]["type"],
            source_title=e["source"]["title"], published_at=_datetime(e["source"]["published_at"]),
            url=e["source"]["url"], source_location=e["source_location"],
        ) for e in payload["evidence"]),
    )


__all__ = [
    "AnalysisStatus", "CrossDetectorAssessment", "CriticalRiskFlag", "DETECTOR_FAMILIES",
    "DetectorFamily", "DetectorName", "DetectorResult", "EvidenceRole",
    "FactInterpretationType", "FinalDisposition", "PublishedEvidence",
    "PublishedOpportunity", "RankingCalculation", "SupportingSignal", "ThesisMomentum",
    "WeeklyCoverage", "WeeklySnapshot",
]

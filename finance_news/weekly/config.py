"""Load and validate versioned Phase 3.1 opportunity configuration."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class OpportunityConfigError(ValueError):
    """Raised when a weekly opportunity configuration is invalid."""


@dataclass(frozen=True)
class EligibilityConfig:
    minimum_equity_score: float
    minimum_evidence_confidence: float
    minimum_final_rank: float


@dataclass(frozen=True)
class RankingConfig:
    primary_strength_weight: float
    evidence_confidence_weight: float
    freshness_weight: float
    inverse_risk_weight: float

    @property
    def weights(self) -> tuple[float, ...]:
        return (
            self.primary_strength_weight,
            self.evidence_confidence_weight,
            self.freshness_weight,
            self.inverse_risk_weight,
        )


@dataclass(frozen=True)
class SupportingSignalsConfig:
    minimum_detector_score: float
    one_family_bonus: float
    two_family_bonus: float
    three_or_more_family_bonus: float


@dataclass(frozen=True)
class ThesisMomentumConfig:
    strengthening: float
    stable: float
    weakening: float


@dataclass(frozen=True)
class DiversificationConfig:
    maximum_opportunities: int
    maximum_per_sector: int


@dataclass(frozen=True)
class OpportunityConfig:
    schema_version: int
    configuration_version: str
    methodology_version: str
    eligibility: EligibilityConfig
    ranking: RankingConfig
    supporting_signals: SupportingSignalsConfig
    thesis_momentum: ThesisMomentumConfig
    diversification: DiversificationConfig


_ROOT_KEYS = {
    "schema_version", "configuration_version", "methodology_version",
    "eligibility", "ranking", "supporting_signals", "thesis_momentum",
    "diversification",
}


def _exact_keys(payload: dict[str, Any], expected: set[str], section: str) -> None:
    missing = expected - payload.keys()
    unknown = payload.keys() - expected
    if missing:
        raise OpportunityConfigError(f"{section} is missing: {', '.join(sorted(missing))}.")
    if unknown:
        raise OpportunityConfigError(f"{section} has unknown fields: {', '.join(sorted(unknown))}.")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise OpportunityConfigError(f"{label} must be a finite number.")
    return float(value)


def _score(value: Any, label: str) -> float:
    result = _number(value, label)
    if not 0 <= result <= 100:
        raise OpportunityConfigError(f"{label} must be between 0 and 100.")
    return result


def parse_opportunity_config(payload: dict[str, Any]) -> OpportunityConfig:
    """Validate a decoded JSON object and return an immutable configuration."""
    if not isinstance(payload, dict):
        raise OpportunityConfigError("Configuration root must be an object.")
    _exact_keys(payload, _ROOT_KEYS, "Configuration")
    if payload["schema_version"] != 1:
        raise OpportunityConfigError("Only configuration schema version 1 is supported.")
    for field in ("configuration_version", "methodology_version"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise OpportunityConfigError(f"{field} must be a non-empty string.")

    eligibility = payload["eligibility"]
    ranking = payload["ranking"]
    supporting = payload["supporting_signals"]
    momentum = payload["thesis_momentum"]
    diversification = payload["diversification"]
    sections = (eligibility, ranking, supporting, momentum, diversification)
    if not all(isinstance(section, dict) for section in sections):
        raise OpportunityConfigError("Every configuration section must be an object.")

    _exact_keys(eligibility, {"minimum_equity_score", "minimum_evidence_confidence", "minimum_final_rank"}, "eligibility")
    _exact_keys(ranking, {"primary_strength_weight", "evidence_confidence_weight", "freshness_weight", "inverse_risk_weight"}, "ranking")
    _exact_keys(supporting, {"minimum_detector_score", "one_family_bonus", "two_family_bonus", "three_or_more_family_bonus"}, "supporting_signals")
    _exact_keys(momentum, {"strengthening", "stable", "weakening"}, "thesis_momentum")
    _exact_keys(diversification, {"maximum_opportunities", "maximum_per_sector"}, "diversification")

    eligibility_config = EligibilityConfig(*(
        _score(eligibility[key], f"eligibility.{key}") for key in (
            "minimum_equity_score", "minimum_evidence_confidence", "minimum_final_rank"
        )
    ))
    ranking_config = RankingConfig(*(
        _number(ranking[key], f"ranking.{key}") for key in (
            "primary_strength_weight", "evidence_confidence_weight",
            "freshness_weight", "inverse_risk_weight"
        )
    ))
    if any(weight < 0 for weight in ranking_config.weights) or not math.isclose(sum(ranking_config.weights), 1.0, abs_tol=1e-9):
        raise OpportunityConfigError("Ranking weights must be nonnegative and sum to 1.0.")

    supporting_config = SupportingSignalsConfig(
        minimum_detector_score=_score(supporting["minimum_detector_score"], "supporting_signals.minimum_detector_score"),
        one_family_bonus=_number(supporting["one_family_bonus"], "supporting_signals.one_family_bonus"),
        two_family_bonus=_number(supporting["two_family_bonus"], "supporting_signals.two_family_bonus"),
        three_or_more_family_bonus=_number(supporting["three_or_more_family_bonus"], "supporting_signals.three_or_more_family_bonus"),
    )
    bonuses = (supporting_config.one_family_bonus, supporting_config.two_family_bonus, supporting_config.three_or_more_family_bonus)
    if any(value < 0 for value in bonuses) or list(bonuses) != sorted(bonuses):
        raise OpportunityConfigError("Convergence bonuses must be nonnegative and monotonically increasing.")

    momentum_config = ThesisMomentumConfig(*(
        _number(momentum[key], f"thesis_momentum.{key}")
        for key in ("strengthening", "stable", "weakening")
    ))
    if not (momentum_config.strengthening > 0 and momentum_config.stable == 0 and momentum_config.weakening < 0):
        raise OpportunityConfigError("Momentum adjustments must be positive, zero, and negative respectively.")

    maximum = diversification["maximum_opportunities"]
    per_sector = diversification["maximum_per_sector"]
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 5:
        raise OpportunityConfigError("maximum_opportunities must be an integer between 1 and 5.")
    if isinstance(per_sector, bool) or not isinstance(per_sector, int) or not 1 <= per_sector <= maximum:
        raise OpportunityConfigError("maximum_per_sector must be positive and no larger than maximum_opportunities.")

    return OpportunityConfig(
        schema_version=1,
        configuration_version=payload["configuration_version"],
        methodology_version=payload["methodology_version"],
        eligibility=eligibility_config,
        ranking=ranking_config,
        supporting_signals=supporting_config,
        thesis_momentum=momentum_config,
        diversification=DiversificationConfig(maximum, per_sector),
    )


def load_opportunity_config(path: Path | str) -> OpportunityConfig:
    """Read a JSON configuration without performing writes or network access."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OpportunityConfigError(f"Could not load opportunity configuration: {exc}") from exc
    return parse_opportunity_config(payload)


__all__ = ["OpportunityConfig", "OpportunityConfigError", "load_opportunity_config", "parse_opportunity_config"]

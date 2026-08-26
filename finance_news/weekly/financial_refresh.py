"""Index cached SEC artifacts and persist provenance-backed Equity Scores."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from finance_news.dashboard import FinancialOverview, build_financial_snapshot_score
from finance_news.sec_filings import Filing
from finance_news.weekly.storage import transaction


EQUITY_SCORE_FORMULA_VERSION = "financial-snapshot-v1"
DERIVED_METRICS_VERSION = "annual-derived-v1"
SECTION_EXTRACTION_VERSION = "existing-section-extractor-v1"


class FinancialRefreshError(ValueError):
    """Raised when SEC or financial artifacts cannot be indexed safely."""


@dataclass(frozen=True)
class FilingRefreshSummary:
    company_id: str
    known_accessions: tuple[str, ...]
    newly_indexed_accessions: tuple[str, ...]
    cached_artifact_count: int
    latest_10k_accession: str | None
    latest_10q_accession: str | None
    latest_8k_accession: str | None


@dataclass(frozen=True)
class EquityScoreRecord:
    score_id: str
    company_id: str
    score: int | None
    label: str
    available_components: int
    financial_period_end: str
    information_available_at: str
    source_accessions: tuple[str, ...]
    source_ids: tuple[str, ...]


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FinancialRefreshError("Refresh timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise FinancialRefreshError(f"Could not hash cached artifact {path}: {exc}") from exc
    return digest.hexdigest()


def _register_artifact(
    connection: sqlite3.Connection,
    *,
    company_id: str,
    path: Path,
    source_type: str,
    title: str,
    collected_at: str,
    accession_number: str | None = None,
    canonical_url: str | None = None,
) -> str:
    content_hash = _hash(path)
    source_id = f"artifact:{source_type.lower()}:{content_hash}"
    connection.execute(
        "INSERT INTO source_documents "
        "(source_id, company_id, source_type, source_tier, title, published_at, collected_at, "
        "canonical_url, accession_number, local_artifact_path, content_hash, provider, quality_status) "
        "VALUES (?, ?, ?, 1, ?, NULL, ?, ?, ?, ?, ?, 'local_sec_cache', 'complete') "
        "ON CONFLICT(source_id) DO NOTHING",
        (
            source_id, company_id, source_type, title, collected_at, canonical_url,
            accession_number, str(path), content_hash,
        ),
    )
    return source_id


def _cached_filing_path(
    company_id: str,
    filing: Filing,
    raw_root: Path,
    processed_root: Path,
) -> Path | None:
    compact = filing.accession_number.replace("-", "")
    processed = processed_root / company_id / compact / "filing.txt"
    if processed.is_file() and processed.stat().st_size:
        return processed
    raw = raw_root / company_id / compact / filing.primary_document
    if raw.is_file() and raw.stat().st_size:
        return raw
    return None


def index_known_filings(
    connection: sqlite3.Connection,
    company_id: str,
    filings: Sequence[Filing],
    *,
    checked_at: datetime,
    raw_root: Path = Path("data/raw/sec"),
    processed_root: Path = Path("data/processed/sec"),
) -> FilingRefreshSummary:
    """Index known SEC metadata, cached documents, sections, and the filing cursor."""
    if len(company_id) != 10 or not company_id.isdigit():
        raise FinancialRefreshError("company_id must be a normalized 10-digit CIK.")
    checked = _utc(checked_at)
    accessions = [filing.accession_number for filing in filings]
    if len(accessions) != len(set(accessions)):
        raise FinancialRefreshError("Known filings contain duplicate accessions.")
    existing = {
        row["accession_number"]
        for row in connection.execute(
            "SELECT accession_number FROM filings WHERE company_id = ?", (company_id,)
        )
    }
    cached_count = 0
    try:
        with transaction(connection):
            for filing in filings:
                artifact = _cached_filing_path(company_id, filing, raw_root, processed_root)
                source_id = None
                status = "metadata_only"
                if artifact is not None:
                    source_id = _register_artifact(
                        connection, company_id=company_id, path=artifact,
                        source_type=f"SEC_{filing.form.replace('-', '')}",
                        title=f"{filing.form} filing {filing.accession_number}",
                        collected_at=checked, accession_number=filing.accession_number,
                        canonical_url=filing.document_url,
                    )
                    status = "cached"
                    cached_count += 1
                connection.execute(
                    "INSERT INTO filings "
                    "(accession_number, company_id, form, filing_date, document_url, "
                    "primary_document, source_id, processing_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(accession_number) DO UPDATE SET "
                    "document_url = excluded.document_url, primary_document = excluded.primary_document, "
                    "source_id = COALESCE(excluded.source_id, filings.source_id), "
                    "processing_status = CASE WHEN excluded.source_id IS NOT NULL THEN 'cached' "
                    "ELSE filings.processing_status END",
                    (
                        filing.accession_number, company_id, filing.form, filing.filing_date,
                        filing.document_url, filing.primary_document, source_id, status,
                    ),
                )
                compact = filing.accession_number.replace("-", "")
                sections = processed_root / company_id / compact / "sections"
                if sections.is_dir():
                    for section in sorted(sections.glob("*.txt")):
                        content_hash = _hash(section)
                        section_type = section.stem
                        connection.execute(
                            "INSERT INTO filing_sections "
                            "(section_id, accession_number, section_type, title, text_path, content_hash, "
                            "extraction_version, quality_status) VALUES (?, ?, ?, ?, ?, ?, ?, 'complete') "
                            "ON CONFLICT(accession_number, section_type, extraction_version) DO UPDATE SET "
                            "text_path = excluded.text_path, content_hash = excluded.content_hash, "
                            "quality_status = excluded.quality_status",
                            (
                                f"{filing.accession_number}:{section_type}:{SECTION_EXTRACTION_VERSION}",
                                filing.accession_number, section_type,
                                section_type.replace("_", " ").title(), str(section), content_hash,
                                SECTION_EXTRACTION_VERSION,
                            ),
                        )
            ordered = sorted(filings, key=lambda item: (item.filing_date, item.accession_number), reverse=True)
            latest = lambda form: next((item.accession_number for item in ordered if item.form == form), None)
            connection.execute(
                "INSERT INTO filing_refresh_state "
                "(company_id, last_checked_at, latest_known_accession, latest_10k_accession, "
                "latest_10q_accession, latest_8k_accession, known_accessions_json, refresh_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'completed') "
                "ON CONFLICT(company_id) DO UPDATE SET "
                "last_checked_at = excluded.last_checked_at, latest_known_accession = excluded.latest_known_accession, "
                "latest_10k_accession = excluded.latest_10k_accession, latest_10q_accession = excluded.latest_10q_accession, "
                "latest_8k_accession = excluded.latest_8k_accession, "
                "known_accessions_json = excluded.known_accessions_json, refresh_status = 'completed', error_code = NULL",
                (
                    company_id, checked, ordered[0].accession_number if ordered else None,
                    latest("10-K"), latest("10-Q"), latest("8-K"),
                    json.dumps(sorted(accessions)),
                ),
            )
    except (OSError, sqlite3.Error) as exc:
        raise FinancialRefreshError(f"Could not index known filings: {exc}") from exc
    return FilingRefreshSummary(
        company_id=company_id,
        known_accessions=tuple(sorted(accessions)),
        newly_indexed_accessions=tuple(sorted(set(accessions) - existing)),
        cached_artifact_count=cached_count,
        latest_10k_accession=latest("10-K"),
        latest_10q_accession=latest("10-Q"),
        latest_8k_accession=latest("8-K"),
    )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinancialRefreshError(f"Could not read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FinancialRefreshError(f"{label} must contain a JSON object.")
    return payload


def ingest_equity_score_snapshot(
    connection: sqlite3.Connection,
    financial_facts_path: Path,
    derived_metrics_path: Path,
    *,
    calculated_at: datetime,
) -> EquityScoreRecord:
    """Store annual observations and the existing production Equity Score with provenance."""
    calculated = _utc(calculated_at)
    facts_payload = _read_json(financial_facts_path, "financial facts")
    metrics_payload = _read_json(derived_metrics_path, "derived metrics")
    company_id = str(facts_payload.get("cik", ""))
    if len(company_id) != 10 or not company_id.isdigit() or metrics_payload.get("cik") != company_id:
        raise FinancialRefreshError("Financial artifacts do not share one normalized company CIK.")
    facts = facts_payload.get("facts")
    periods = metrics_payload.get("periods")
    if not isinstance(facts, list) or not facts or not isinstance(periods, list) or not periods:
        raise FinancialRefreshError("Financial artifacts are missing facts or derived periods.")
    latest = periods[0]
    period_end = str(latest["period_end"])
    current_facts = [item for item in facts if item.get("period_end") == period_end]
    required = {"revenue", "net_income", "assets", "liabilities", "operating_cash_flow"}
    by_metric = {str(item.get("metric")): item for item in current_facts}
    if not required.issubset(by_metric):
        raise FinancialRefreshError("Latest financial period is missing required score facts.")
    source_accessions = tuple(sorted({str(by_metric[name]["accession_number"]) for name in required}))
    filed_dates = tuple(sorted({str(by_metric[name]["filed"]) for name in required}))
    if len(filed_dates) != 1:
        raise FinancialRefreshError("Score facts do not share one information-available date.")
    overview = FinancialOverview(
        fiscal_year=int(latest["fiscal_year"]), period_end=period_end,
        revenue=by_metric["revenue"]["value"], net_income=by_metric["net_income"]["value"],
        assets=by_metric["assets"]["value"], liabilities=by_metric["liabilities"]["value"],
        operating_cash_flow=by_metric["operating_cash_flow"]["value"],
        revenue_growth_percent=latest.get("revenue_growth_percent"),
        net_profit_margin_percent=latest.get("net_profit_margin_percent"),
        liabilities_to_assets_percent=latest.get("liabilities_to_assets_percent"),
        operating_cash_flow_margin_percent=latest.get("operating_cash_flow_margin_percent"),
    )
    score = build_financial_snapshot_score(overview)
    try:
        with transaction(connection):
            facts_source = _register_artifact(
                connection, company_id=company_id, path=financial_facts_path,
                source_type="SEC_FINANCIAL_FACTS_NORMALIZED", title="Normalized SEC financial facts",
                collected_at=calculated,
            )
            metrics_source = _register_artifact(
                connection, company_id=company_id, path=derived_metrics_path,
                source_type="DERIVED_FINANCIAL_METRICS", title="Derived annual financial metrics",
                collected_at=calculated,
            )
            observation_ids: dict[str, str] = {}
            for item in facts:
                observation_id = (
                    f"{company_id}:{item['accession_number']}:{item['metric']}:{item['period_end']}"
                )
                observation_ids[str(item["metric"])] = observation_id
                connection.execute(
                    "INSERT INTO financial_observations "
                    "(observation_id, company_id, metric, value, unit, period_type, fiscal_year, "
                    "period_end, filed_at, accession_number, form, derivation_type, quality_status) "
                    "VALUES (?, ?, ?, ?, ?, 'annual', ?, ?, ?, ?, ?, 'reported', 'complete') "
                    "ON CONFLICT(observation_id) DO UPDATE SET value = excluded.value, "
                    "filed_at = excluded.filed_at, quality_status = excluded.quality_status",
                    (
                        observation_id, company_id, item["metric"], item["value"], item["unit"],
                        item.get("fiscal_year"), item["period_end"], item["filed"],
                        item["accession_number"], item["form"],
                    ),
                )
            metric_names = (
                "revenue_growth_percent", "net_profit_margin_percent",
                "liabilities_to_assets_percent", "operating_cash_flow_margin_percent",
            )
            for name in metric_names:
                connection.execute(
                    "INSERT INTO derived_financial_metrics "
                    "(metric_id, company_id, metric, value, period_end, information_available_at, "
                    "input_observation_ids_json, calculation_version, quality_status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(company_id, metric, period_end, calculation_version) DO UPDATE SET "
                    "value = excluded.value, information_available_at = excluded.information_available_at, "
                    "input_observation_ids_json = excluded.input_observation_ids_json, "
                    "quality_status = excluded.quality_status",
                    (
                        f"{company_id}:{period_end}:{name}:{DERIVED_METRICS_VERSION}", company_id,
                        name, latest.get(name), period_end, filed_dates[0],
                        json.dumps(sorted(observation_ids.values())), DERIVED_METRICS_VERSION,
                        "complete" if latest.get(name) is not None else "partial",
                    ),
                )
            components = [
                {"name": component.name, "score": component.score, "source_value": component.source_value}
                for component in score.components
            ]
            score_id = f"{company_id}:{period_end}:{EQUITY_SCORE_FORMULA_VERSION}"
            source_ids = (facts_source, metrics_source)
            connection.execute(
                "INSERT INTO equity_score_snapshots "
                "(score_id, company_id, score, label, available_components, eligible_coverage, "
                "financial_period_end, information_available_at, calculated_at, formula_version, "
                "component_json, source_accessions_json, source_ids_json, quality_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(company_id, financial_period_end, formula_version) DO UPDATE SET "
                "score = excluded.score, label = excluded.label, available_components = excluded.available_components, "
                "eligible_coverage = excluded.eligible_coverage, calculated_at = excluded.calculated_at, "
                "component_json = excluded.component_json, source_accessions_json = excluded.source_accessions_json, "
                "source_ids_json = excluded.source_ids_json, quality_status = excluded.quality_status",
                (
                    score_id, company_id, score.score, score.label, score.available_components,
                    int(score.available_components >= 3), period_end, filed_dates[0], calculated,
                    EQUITY_SCORE_FORMULA_VERSION, json.dumps(components, sort_keys=True),
                    json.dumps(source_accessions), json.dumps(source_ids),
                    "complete" if score.available_components == 4 else "partial",
                ),
            )
    except sqlite3.Error as exc:
        raise FinancialRefreshError(f"Could not store financial refresh state: {exc}") from exc
    return EquityScoreRecord(
        score_id=score_id, company_id=company_id, score=score.score, label=score.label,
        available_components=score.available_components, financial_period_end=period_end,
        information_available_at=filed_dates[0], source_accessions=source_accessions,
        source_ids=source_ids,
    )


__all__ = [
    "EQUITY_SCORE_FORMULA_VERSION", "EquityScoreRecord", "FilingRefreshSummary",
    "FinancialRefreshError", "index_known_filings", "ingest_equity_score_snapshot",
]

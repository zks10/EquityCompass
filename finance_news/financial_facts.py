"""Retrieve and normalize annual financial facts from SEC XBRL data."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from finance_news.sec_companies import DEFAULT_USER_AGENT


SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
DEFAULT_RAW_ROOT = Path("data/raw/sec")
DEFAULT_PROCESSED_ROOT = Path("data/processed/sec")


class FinancialFactsError(Exception):
    """Raised when SEC financial facts cannot be retrieved or normalized."""


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    label: str
    tags: tuple[str, ...]
    unit: str = "USD"


@dataclass(frozen=True)
class FinancialFact:
    metric: str
    label: str
    tag: str
    value: int | float
    unit: str
    fiscal_year: int
    period_end: str
    filed: str
    accession_number: str
    form: str


METRICS = (
    MetricDefinition(
        name="revenue",
        label="Revenue",
        tags=(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ),
    ),
    MetricDefinition(
        name="net_income",
        label="Net income",
        tags=("NetIncomeLoss", "ProfitLoss"),
    ),
    MetricDefinition(name="assets", label="Total assets", tags=("Assets",)),
    MetricDefinition(
        name="liabilities", label="Total liabilities", tags=("Liabilities",)
    ),
    MetricDefinition(
        name="operating_cash_flow",
        label="Operating cash flow",
        tags=("NetCashProvidedByUsedInOperatingActivities",),
    ),
)

SUPPLEMENTAL_METRICS = (
    MetricDefinition(
        name="capital_expenditures",
        label="Capital expenditures",
        tags=(
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PaymentsForAdditionsToPropertyPlantAndEquipment",
            "PaymentsForProceedsFromPropertyPlantAndEquipment",
        ),
    ),
    MetricDefinition(
        name="eps",
        label="Earnings per share",
        tags=(
            "EarningsPerShareDiluted",
            "EarningsPerShareBasicAndDiluted",
            "EarningsPerShareBasic",
        ),
        unit="USD/shares",
    ),
)

EQUITY_DEFINITION = MetricDefinition(
    name="stockholders_equity",
    label="Stockholders' equity",
    tags=(
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
)


def _derived_liabilities(assets: FinancialFact, equity: FinancialFact) -> FinancialFact:
    """Derive total liabilities when SEC XBRL omits the standalone concept."""
    return FinancialFact(
        metric="liabilities",
        label="Total liabilities",
        tag=f"derived:{assets.tag}-{equity.tag}",
        value=assets.value - equity.value,
        unit="USD",
        fiscal_year=assets.fiscal_year,
        period_end=assets.period_end,
        filed=max(assets.filed, equity.filed),
        accession_number=assets.accession_number,
        form="10-K",
    )


def _normalize_cik(cik: str) -> str:
    normalized_cik = str(cik).strip().zfill(10)
    if not normalized_cik.isdigit() or len(normalized_cik) != 10:
        raise FinancialFactsError("CIK must contain between 1 and 10 digits.")
    return normalized_cik


def fetch_company_facts(cik: str) -> dict[str, Any]:
    """Fetch the complete SEC Company Facts payload for ``cik``."""
    normalized_cik = _normalize_cik(cik)

    try:
        response = requests.get(
            SEC_COMPANY_FACTS_URL.format(cik=normalized_cik),
            headers={
                "User-Agent": os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT),
                "Accept": "application/json",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise FinancialFactsError(
            f"SEC request failed with HTTP status {status}."
        ) from exc
    except requests.JSONDecodeError as exc:
        raise FinancialFactsError("The SEC returned an unreadable response.") from exc
    except requests.RequestException as exc:
        raise FinancialFactsError(f"Could not connect to the SEC: {exc}") from exc

    if not isinstance(payload, dict):
        raise FinancialFactsError("The SEC response has an unexpected format.")
    return payload


def _latest_annual_fact(
    us_gaap_facts: dict[str, Any], definition: MetricDefinition
) -> FinancialFact | None:
    candidates: list[tuple[str, str, int, str, dict[str, Any]]] = []

    for tag_priority, tag in enumerate(definition.tags):
        concept = us_gaap_facts.get(tag)
        if not isinstance(concept, dict):
            continue

        units = concept.get("units", {})
        if not isinstance(units, dict):
            continue

        for unit, records in units.items():
            if unit != definition.unit or not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                if record.get("form") != "10-K" or record.get("fp") != "FY":
                    continue
                if not all(
                    key in record
                    for key in ("val", "fy", "end", "filed", "accn")
                ):
                    continue
                candidates.append(
                    (
                        str(record["end"]),
                        str(record["filed"]),
                        -tag_priority,
                        tag,
                        record,
                    )
                )

    if not candidates:
        return None

    _, _, _, tag, record = max(candidates, key=lambda candidate: candidate[:3])
    try:
        return FinancialFact(
            metric=definition.name,
            label=definition.label,
            tag=tag,
            value=record["val"],
            unit=definition.unit,
            fiscal_year=int(record["fy"]),
            period_end=str(record["end"]),
            filed=str(record["filed"]),
            accession_number=str(record["accn"]),
            form="10-K",
        )
    except (TypeError, ValueError) as exc:
        raise FinancialFactsError(
            f"The SEC returned an invalid value for {definition.label}."
        ) from exc


def extract_latest_annual_facts(payload: dict[str, Any]) -> list[FinancialFact]:
    """Normalize the latest annual values for the five Phase 1 metrics."""
    try:
        us_gaap_facts = payload["facts"]["us-gaap"]
    except (KeyError, TypeError) as exc:
        raise FinancialFactsError(
            "The SEC response does not contain US-GAAP company facts."
        ) from exc

    if not isinstance(us_gaap_facts, dict):
        raise FinancialFactsError("The SEC response has an unexpected facts format.")

    extracted: list[FinancialFact] = []
    missing: list[str] = []
    for definition in METRICS + SUPPLEMENTAL_METRICS:
        fact = _latest_annual_fact(us_gaap_facts, definition)
        if fact is None and definition in METRICS:
            missing.append(definition.label)
        elif fact is not None:
            extracted.append(fact)

    if "Total liabilities" in missing:
        assets = next((fact for fact in extracted if fact.metric == "assets"), None)
        equity = _latest_annual_fact(us_gaap_facts, EQUITY_DEFINITION)
        if assets is not None and equity is not None and assets.period_end == equity.period_end:
            extracted.append(_derived_liabilities(assets, equity))
            missing.remove("Total liabilities")

    if missing:
        raise FinancialFactsError(
            "Could not locate latest annual fact(s): " + ", ".join(missing) + "."
        )

    return extracted


def _annual_history_for_metric(
    us_gaap_facts: dict[str, Any], definition: MetricDefinition, years: int
) -> list[FinancialFact]:
    records_by_period: dict[str, list[tuple[int, str, dict[str, Any]]]] = {}

    for tag_priority, tag in enumerate(definition.tags):
        concept = us_gaap_facts.get(tag)
        if not isinstance(concept, dict):
            continue
        units = concept.get("units", {})
        if not isinstance(units, dict):
            continue

        records = units.get(definition.unit, [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("form") != "10-K" or record.get("fp") != "FY":
                continue
            if not all(
                key in record for key in ("val", "fy", "end", "filed", "accn")
            ):
                continue
            period_end = str(record["end"])
            records_by_period.setdefault(period_end, []).append(
                (tag_priority, tag, record)
            )

    history: list[FinancialFact] = []
    for period_end in sorted(records_by_period, reverse=True)[:years]:
        period_records = records_by_period[period_end]
        best_priority = min(candidate[0] for candidate in period_records)
        preferred_records = [
            candidate for candidate in period_records if candidate[0] == best_priority
        ]
        _, tag, selected = max(
            preferred_records, key=lambda candidate: str(candidate[2]["filed"])
        )

        try:
            # Comparative values are often repeated in a later 10-K whose ``fy``
            # identifies the filing year rather than the value's own fiscal year.
            # The annual period end is the stable identity for the historical row.
            fiscal_year = int(period_end[:4])
            history.append(
                FinancialFact(
                    metric=definition.name,
                    label=definition.label,
                    tag=tag,
                    value=selected["val"],
                    unit=definition.unit,
                    fiscal_year=fiscal_year,
                    period_end=period_end,
                    filed=str(selected["filed"]),
                    accession_number=str(selected["accn"]),
                    form="10-K",
                )
            )
        except (TypeError, ValueError) as exc:
            raise FinancialFactsError(
                f"The SEC returned an invalid historical value for {definition.label}."
            ) from exc

    return history


def extract_annual_history(
    payload: dict[str, Any], years: int = 5
) -> dict[str, list[FinancialFact]]:
    """Normalize up to ``years`` annual periods for each Phase 1 metric."""
    if years < 1 or years > 20:
        raise FinancialFactsError("Years must be between 1 and 20.")

    try:
        us_gaap_facts = payload["facts"]["us-gaap"]
    except (KeyError, TypeError) as exc:
        raise FinancialFactsError(
            "The SEC response does not contain US-GAAP company facts."
        ) from exc
    if not isinstance(us_gaap_facts, dict):
        raise FinancialFactsError("The SEC response has an unexpected facts format.")

    history: dict[str, list[FinancialFact]] = {}
    missing: list[str] = []
    for definition in METRICS + SUPPLEMENTAL_METRICS:
        metric_history = _annual_history_for_metric(
            us_gaap_facts, definition, years
        )
        if not metric_history and definition in METRICS:
            missing.append(definition.label)
        elif metric_history:
            history[definition.name] = metric_history

    if "Total liabilities" in missing:
        equity_history = _annual_history_for_metric(
            us_gaap_facts, EQUITY_DEFINITION, years
        )
        assets_by_period = {
            fact.period_end: fact for fact in history.get("assets", [])
        }
        equity_by_period = {fact.period_end: fact for fact in equity_history}
        common_periods = sorted(
            set(assets_by_period) & set(equity_by_period), reverse=True
        )[:years]
        if common_periods:
            history["liabilities"] = [
                _derived_liabilities(
                    assets_by_period[period], equity_by_period[period]
                )
                for period in common_periods
            ]
            missing.remove("Total liabilities")

    if missing:
        raise FinancialFactsError(
            "Could not locate annual history for: " + ", ".join(missing) + "."
        )

    return history


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.part")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary_path.replace(path)


def save_financial_facts(
    payload: dict[str, Any],
    facts: list[FinancialFact],
    ticker: str,
    cik: str,
    raw_root: Path = DEFAULT_RAW_ROOT,
    processed_root: Path = DEFAULT_PROCESSED_ROOT,
) -> tuple[Path, Path]:
    """Save the raw SEC payload and normalized annual metrics as JSON."""
    normalized_cik = _normalize_cik(cik)
    raw_path = Path(raw_root) / normalized_cik / "companyfacts.json"
    processed_path = (
        Path(processed_root) / normalized_cik / "financial_facts.json"
    )
    normalized_payload = {
        "ticker": ticker.upper(),
        "cik": normalized_cik,
        "entity_name": str(payload.get("entityName", "")),
        "source": SEC_COMPANY_FACTS_URL.format(cik=normalized_cik),
        "facts": [asdict(fact) for fact in facts],
    }

    try:
        _write_json(raw_path, payload)
        _write_json(processed_path, normalized_payload)
    except OSError as exc:
        raise FinancialFactsError(f"Could not save financial facts: {exc}") from exc

    return raw_path, processed_path


def save_financial_history(
    payload: dict[str, Any],
    history: dict[str, list[FinancialFact]],
    ticker: str,
    cik: str,
    requested_years: int,
    raw_root: Path = DEFAULT_RAW_ROOT,
    processed_root: Path = DEFAULT_PROCESSED_ROOT,
) -> tuple[Path, Path]:
    """Save raw Company Facts and normalized multi-year annual history."""
    normalized_cik = _normalize_cik(cik)
    raw_path = Path(raw_root) / normalized_cik / "companyfacts.json"
    processed_path = (
        Path(processed_root) / normalized_cik / "financial_history.json"
    )
    normalized_payload = {
        "ticker": ticker.upper(),
        "cik": normalized_cik,
        "entity_name": str(payload.get("entityName", "")),
        "source": SEC_COMPANY_FACTS_URL.format(cik=normalized_cik),
        "requested_years": requested_years,
        "metrics": {
            metric: [asdict(fact) for fact in facts]
            for metric, facts in history.items()
        },
    }

    try:
        _write_json(raw_path, payload)
        _write_json(processed_path, normalized_payload)
    except OSError as exc:
        raise FinancialFactsError(f"Could not save financial history: {exc}") from exc

    return raw_path, processed_path

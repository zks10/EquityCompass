"""Save traceable Phase 2 score snapshots from existing Phase 1 outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from finance_news.dashboard import FinancialOverview, build_financial_snapshot_score
from finance_news.financial_facts import EQUITY_DEFINITION, METRICS, MetricDefinition


DEFAULT_SNAPSHOT_ROOT = Path("data/validation/sec")
REQUIRED_METRICS = (
    "revenue",
    "net_income",
    "assets",
    "liabilities",
    "operating_cash_flow",
)
PILOT_COMPANIES = (
    ("AAPL", "0000320193"), ("MSFT", "0000789019"),
    ("NVDA", "0001045810"), ("INTC", "0000050863"),
    ("JPM", "0000019617"), ("BAC", "0000070858"),
    ("GS", "0000886982"), ("C", "0000831001"),
    ("XOM", "0000034088"), ("CVX", "0000093410"),
    ("COP", "0001163165"), ("SLB", "0000087347"),
    ("JNJ", "0000200406"), ("PFE", "0000078003"),
    ("UNH", "0000731766"), ("MDT", "0001613103"),
    ("WMT", "0000104169"), ("TGT", "0000027419"),
    ("KO", "0000021344"), ("NKE", "0000320187"),
)
HISTORICAL_METRICS = tuple(
    MetricDefinition(
        name=definition.name,
        label=definition.label,
        tags=(
            definition.tags
            + (
                ("RevenuesNetOfInterestExpense",)
                if definition.name == "revenue"
                else (
                    ("NetIncomeLossAvailableToCommonStockholdersBasic",)
                    if definition.name == "net_income"
                    else ()
                )
            )
        ),
        unit=definition.unit,
    )
    for definition in METRICS
)


class ScoreSnapshotError(Exception):
    """Raised when a traceable score snapshot cannot be created."""


def _read_json(path: Path, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScoreSnapshotError(f"Could not read {description}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScoreSnapshotError(f"{description.capitalize()} must be a JSON object.")
    return payload


def _facts_for_accession(
    us_gaap: dict[str, Any], definition: MetricDefinition, accession_number: str
) -> dict[str, dict[str, Any]]:
    """Return one preferred fact per period from one specific 10-K."""
    candidates: dict[str, list[tuple[int, str, dict[str, Any]]]] = {}
    for priority, tag in enumerate(definition.tags):
        concept = us_gaap.get(tag, {})
        records = concept.get("units", {}).get(definition.unit, [])
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            if (
                record.get("accn") != accession_number
                or record.get("form") != "10-K"
                or record.get("fp") != "FY"
                or not all(key in record for key in ("val", "end", "filed"))
            ):
                continue
            candidates.setdefault(str(record["end"]), []).append(
                (priority, tag, record)
            )

    selected: dict[str, dict[str, Any]] = {}
    for period_end, period_candidates in candidates.items():
        priority, tag, record = min(period_candidates, key=lambda item: item[0])
        selected[period_end] = {
            "metric": definition.name,
            "label": definition.label,
            "tag": tag,
            "value": record["val"],
            "unit": definition.unit,
            "fiscal_year": int(record.get("fy", period_end[:4])),
            "period_end": period_end,
            "filed": str(record["filed"]),
            "accession_number": accession_number,
            "form": "10-K",
        }
    return selected


def save_historical_score_snapshot(
    companyfacts_path: Path,
    accession_number: str,
    ticker: str,
    output_root: Path = DEFAULT_SNAPSHOT_ROOT,
) -> Path:
    """Recreate one score using only facts included in the named historical 10-K."""
    payload = _read_json(companyfacts_path, "SEC Company Facts")
    try:
        us_gaap = payload["facts"]["us-gaap"]
    except (KeyError, TypeError) as exc:
        raise ScoreSnapshotError("SEC Company Facts is missing US-GAAP facts.") from exc

    by_metric = {
        definition.name: _facts_for_accession(
            us_gaap, definition, accession_number
        )
        for definition in HISTORICAL_METRICS
    }
    revenue_periods = sorted(by_metric["revenue"], reverse=True)
    if len(revenue_periods) < 2:
        raise ScoreSnapshotError(
            "The selected 10-K does not contain current and prior-year revenue."
        )
    period_end, prior_period_end = revenue_periods[:2]

    source_facts: dict[str, dict[str, Any]] = {}
    for metric in REQUIRED_METRICS:
        fact = by_metric[metric].get(period_end)
        if fact is not None:
            source_facts[metric] = fact

    if "liabilities" not in source_facts:
        equity = _facts_for_accession(
            us_gaap, EQUITY_DEFINITION, accession_number
        ).get(period_end)
        assets = source_facts.get("assets")
        if assets is not None and equity is not None:
            source_facts["liabilities"] = {
                **assets,
                "metric": "liabilities",
                "label": "Total liabilities",
                "tag": f"derived:{assets['tag']}-{equity['tag']}",
                "value": assets["value"] - equity["value"],
            }

    missing = [metric for metric in REQUIRED_METRICS if metric not in source_facts]
    if missing:
        raise ScoreSnapshotError(
            "The selected 10-K is missing required metric(s): "
            + ", ".join(missing)
            + "."
        )

    current_revenue = source_facts["revenue"]["value"]
    prior_revenue = by_metric["revenue"][prior_period_end]["value"]
    if prior_revenue == 0:
        revenue_growth = None
    else:
        revenue_growth = (current_revenue - prior_revenue) / prior_revenue * 100

    def percentage(numerator: int | float, denominator: int | float) -> float | None:
        return None if denominator == 0 else numerator / denominator * 100

    overview = FinancialOverview(
        fiscal_year=int(source_facts["revenue"]["fiscal_year"]),
        period_end=period_end,
        revenue=current_revenue,
        net_income=source_facts["net_income"]["value"],
        assets=source_facts["assets"]["value"],
        liabilities=source_facts["liabilities"]["value"],
        operating_cash_flow=source_facts["operating_cash_flow"]["value"],
        revenue_growth_percent=revenue_growth,
        net_profit_margin_percent=percentage(
            source_facts["net_income"]["value"], current_revenue
        ),
        liabilities_to_assets_percent=percentage(
            source_facts["liabilities"]["value"],
            source_facts["assets"]["value"],
        ),
        operating_cash_flow_margin_percent=percentage(
            source_facts["operating_cash_flow"]["value"], current_revenue
        ),
    )
    score = build_financial_snapshot_score(overview)
    filing_dates = {fact["filed"] for fact in source_facts.values()}
    if len(filing_dates) != 1:
        raise ScoreSnapshotError("Historical source facts do not share one filing date.")

    normalized_ticker = ticker.strip().upper()
    cik = str(payload.get("cik", ""))
    if not normalized_ticker:
        raise ScoreSnapshotError("Ticker is required for a historical snapshot.")
    if not cik:
        raise ScoreSnapshotError("SEC Company Facts is missing the company CIK.")
    snapshot = {
        "schema_version": 1,
        "ticker": normalized_ticker,
        "cik": cik.zfill(10),
        "entity_name": str(payload.get("entityName", payload.get("entity_name", ""))),
        "fiscal_year": overview.fiscal_year,
        "period_end": period_end,
        "filing": {
            "form": "10-K",
            "filed": filing_dates.pop(),
            "accession_number": accession_number,
        },
        "score": {
            "value": score.score,
            "label": score.label,
            "available_components": score.available_components,
            "eligible_for_main_comparison": score.available_components >= 3,
            "components": [
                {
                    "name": component.name,
                    "score": component.score,
                    "source_value": component.source_value,
                }
                for component in score.components
            ],
        },
        "source_facts": source_facts,
        "prior_revenue_fact": by_metric["revenue"][prior_period_end],
        "source_files": {"companyfacts": str(companyfacts_path)},
    }
    destination = (
        Path(output_root)
        / snapshot["cik"]
        / f"{accession_number.replace('-', '')}.json"
    )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError as exc:
        raise ScoreSnapshotError(f"Could not save score snapshot: {exc}") from exc
    return destination


def build_company_historical_snapshots(
    companyfacts_path: Path,
    ticker: str,
    years: int = 5,
    output_root: Path = DEFAULT_SNAPSHOT_ROOT,
) -> Path:
    """Attempt recent 10-K snapshots and save a complete success/failure manifest."""
    if years < 1 or years > 20:
        raise ScoreSnapshotError("Years must be between 1 and 20.")
    payload = _read_json(companyfacts_path, "SEC Company Facts")
    try:
        us_gaap = payload["facts"]["us-gaap"]
        cik = str(payload["cik"]).zfill(10)
    except (KeyError, TypeError) as exc:
        raise ScoreSnapshotError("SEC Company Facts is missing company identity.") from exc

    filings: dict[str, str] = {}
    for concept in us_gaap.values():
        if not isinstance(concept, dict):
            continue
        for records in concept.get("units", {}).values():
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict) or record.get("form") != "10-K":
                    continue
                accession = str(record.get("accn", ""))
                filed = str(record.get("filed", ""))
                if accession and filed:
                    filings[accession] = max(filings.get(accession, ""), filed)

    selected = sorted(filings.items(), key=lambda item: item[1], reverse=True)[:years]
    attempts = []
    for accession_number, filing_date in selected:
        try:
            path = save_historical_score_snapshot(
                companyfacts_path,
                accession_number,
                ticker,
                output_root=output_root,
            )
            attempts.append(
                {
                    "accession_number": accession_number,
                    "filing_date": filing_date,
                    "status": "completed",
                    "snapshot_path": str(path),
                }
            )
        except ScoreSnapshotError as exc:
            attempts.append(
                {
                    "accession_number": accession_number,
                    "filing_date": filing_date,
                    "status": "incomplete",
                    "reason": str(exc),
                }
            )

    manifest = {
        "schema_version": 1,
        "ticker": ticker.strip().upper(),
        "cik": cik,
        "requested_years": years,
        "attempted": len(attempts),
        "completed": sum(item["status"] == "completed" for item in attempts),
        "incomplete": sum(item["status"] == "incomplete" for item in attempts),
        "filings": attempts,
        "source_files": {"companyfacts": str(companyfacts_path)},
    }
    destination = Path(output_root) / cik / "manifest.json"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError as exc:
        raise ScoreSnapshotError(f"Could not save snapshot manifest: {exc}") from exc
    return destination


def build_pilot_historical_snapshots(
    source_root: Path = Path("data/raw/sec"),
    output_root: Path = DEFAULT_SNAPSHOT_ROOT,
    years: int = 5,
) -> Path:
    """Attempt the complete Phase 2 pilot and record missing local sources."""
    companies = []
    for ticker, cik in PILOT_COMPANIES:
        source = Path(source_root) / cik / "companyfacts.json"
        if not source.is_file():
            companies.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "status": "incomplete",
                    "reason": "Local SEC Company Facts source is missing.",
                }
            )
            continue
        try:
            manifest_path = build_company_historical_snapshots(
                source, ticker, years=years, output_root=output_root
            )
            company_manifest = _read_json(manifest_path, "company manifest")
            companies.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "status": (
                        "completed"
                        if company_manifest["completed"] == years
                        else "incomplete"
                    ),
                    "completed_years": company_manifest["completed"],
                    "incomplete_years": company_manifest["incomplete"],
                    "manifest_path": str(manifest_path),
                }
            )
        except ScoreSnapshotError as exc:
            companies.append(
                {
                    "ticker": ticker,
                    "cik": cik,
                    "status": "incomplete",
                    "reason": str(exc),
                }
            )

    manifest = {
        "schema_version": 1,
        "requested_companies": len(PILOT_COMPANIES),
        "requested_years_per_company": years,
        "completed_companies": sum(
            company["status"] == "completed" for company in companies
        ),
        "incomplete_companies": sum(
            company["status"] == "incomplete" for company in companies
        ),
        "companies": companies,
    }
    destination = Path(output_root) / "pilot_manifest.json"
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError as exc:
        raise ScoreSnapshotError(f"Could not save pilot manifest: {exc}") from exc
    return destination


def save_score_snapshot(
    history_path: Path,
    metrics_path: Path,
    score: Any,
    output_root: Path = DEFAULT_SNAPSHOT_ROOT,
) -> Path:
    """Save the latest score with the 10-K provenance behind every source value."""
    history = _read_json(history_path, "financial history")
    derived = _read_json(metrics_path, "derived metrics")

    try:
        latest = derived["periods"][0]
        period_end = str(latest["period_end"])
        metrics = history["metrics"]
        source_facts = {
            metric: next(
                record
                for record in metrics[metric]
                if record["period_end"] == period_end
            )
            for metric in REQUIRED_METRICS
        }
    except (KeyError, IndexError, StopIteration, TypeError) as exc:
        raise ScoreSnapshotError(
            "Phase 1 outputs do not contain a complete latest annual period."
        ) from exc

    filing_dates = {str(fact.get("filed", "")) for fact in source_facts.values()}
    accessions = {
        str(fact.get("accession_number", "")) for fact in source_facts.values()
    }
    forms = {str(fact.get("form", "")) for fact in source_facts.values()}
    if len(filing_dates) != 1 or "" in filing_dates:
        raise ScoreSnapshotError("Latest source facts do not share one filing date.")
    if len(accessions) != 1 or "" in accessions:
        raise ScoreSnapshotError("Latest source facts do not share one accession number.")
    if forms != {"10-K"}:
        raise ScoreSnapshotError("Latest source facts must come from one Form 10-K.")

    filing_date = filing_dates.pop()
    accession_number = accessions.pop()
    components = tuple(getattr(score, "components", ()))
    snapshot = {
        "schema_version": 1,
        "ticker": str(history.get("ticker", "")).upper(),
        "cik": str(history.get("cik", "")),
        "entity_name": str(history.get("entity_name", "")),
        "fiscal_year": int(latest["fiscal_year"]),
        "period_end": period_end,
        "filing": {
            "form": "10-K",
            "filed": filing_date,
            "accession_number": accession_number,
        },
        "score": {
            "value": getattr(score, "score", None),
            "label": str(getattr(score, "label", "")),
            "available_components": int(
                getattr(score, "available_components", 0)
            ),
            "eligible_for_main_comparison": int(
                getattr(score, "available_components", 0)
            )
            >= 3,
            "components": [
                {
                    "name": str(getattr(component, "name", "")),
                    "score": getattr(component, "score", None),
                    "source_value": getattr(component, "source_value", None),
                }
                for component in components
            ],
        },
        "source_facts": source_facts,
        "source_files": {
            "financial_history": str(history_path),
            "derived_metrics": str(metrics_path),
        },
    }

    normalized_cik = snapshot["cik"].zfill(10)
    destination = (
        Path(output_root)
        / normalized_cik
        / f"{accession_number.replace('-', '')}.json"
    )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    except OSError as exc:
        raise ScoreSnapshotError(f"Could not save score snapshot: {exc}") from exc
    return destination


__all__ = [
    "ScoreSnapshotError",
    "build_company_historical_snapshots",
    "build_pilot_historical_snapshots",
    "save_historical_score_snapshot",
    "save_score_snapshot",
]

"""Run the complete Equity Compass Phase 1 SEC data pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from finance_news.derived_metrics import DerivedMetricsError, calculate_metrics_file
from finance_news.filing_downloader import FilingDownloadError, download_filing
from finance_news.filing_processor import FilingProcessingError, process_filing
from finance_news.financial_facts import (
    FinancialFactsError,
    extract_annual_history,
    extract_latest_annual_facts,
    fetch_company_facts,
    save_financial_facts,
    save_financial_history,
)
from finance_news.sec_companies import Company, CompanyLookupError, resolve_ticker
from finance_news.sec_filings import Filing, FilingLookupError, fetch_recent_filings
from finance_news.section_extractor import (
    SectionExtractionError,
    extract_sections_file,
)


PIPELINE_ERRORS = (
    CompanyLookupError,
    FilingLookupError,
    FilingDownloadError,
    FilingProcessingError,
    SectionExtractionError,
    FinancialFactsError,
    DerivedMetricsError,
)
StageResult = TypeVar("StageResult")
ProgressCallback = Callable[[str], None]


class PipelineError(Exception):
    """Raised when a named pipeline stage cannot complete."""


@dataclass(frozen=True)
class PipelineResult:
    company: Company
    filing: Filing
    raw_filing_path: Path
    processed_filing_path: Path
    section_paths: tuple[Path, ...]
    raw_facts_path: Path
    latest_facts_path: Path
    history_path: Path
    derived_metrics_path: Path


def run_stage(name: str, operation: Callable[[], StageResult]) -> StageResult:
    try:
        return operation()
    except PIPELINE_ERRORS as exc:
        raise PipelineError(f"{name} failed: {exc}") from exc


def run_pipeline(
    ticker: str,
    years: int = 5,
    force_download: bool = False,
    progress: ProgressCallback | None = None,
) -> PipelineResult:
    """Run all current Phase 1 stages for ``ticker`` in sequence."""
    notify = progress or (lambda _message: None)

    notify("1/9 Resolve ticker and SEC CIK")
    company = run_stage("Resolve ticker", lambda: resolve_ticker(ticker))

    notify("2/9 Find the latest 10-K")

    def select_filing() -> Filing:
        filings = fetch_recent_filings(company.cik, limit=100)
        filing = next((item for item in filings if item.form == "10-K"), None)
        if filing is None:
            raise FilingLookupError(
                f"No recent 10-K filing was found for {company.ticker}."
            )
        return filing

    filing = run_stage("Select latest 10-K", select_filing)

    notify("3/9 Download or reuse the raw 10-K")
    raw_filing_path = run_stage(
        "Download filing",
        lambda: download_filing(
            filing, company.cik, overwrite=force_download
        ),
    )

    notify("4/9 Convert the filing to clean text")
    processed_filing_path = run_stage(
        "Process filing", lambda: process_filing(raw_filing_path)
    )

    notify("5/9 Extract Business, Risk Factors, and MD&A")
    section_paths = tuple(
        run_stage(
            "Extract 10-K sections",
            lambda: extract_sections_file(processed_filing_path),
        )
    )

    notify("6/9 Retrieve SEC Company Facts")
    company_facts = run_stage(
        "Retrieve Company Facts", lambda: fetch_company_facts(company.cik)
    )

    notify("7/9 Save the latest annual financial facts")

    def save_latest() -> tuple[Path, Path]:
        latest = extract_latest_annual_facts(company_facts)
        return save_financial_facts(
            company_facts, latest, company.ticker, company.cik
        )

    raw_facts_path, latest_facts_path = run_stage(
        "Save latest annual facts", save_latest
    )

    notify(f"8/9 Save {years} years of annual financial history")

    def save_history() -> tuple[Path, Path]:
        history = extract_annual_history(company_facts, years=years)
        return save_financial_history(
            company_facts,
            history,
            company.ticker,
            company.cik,
            requested_years=years,
        )

    _, history_path = run_stage("Save financial history", save_history)

    notify("9/9 Calculate deterministic financial metrics")
    derived_metrics_path = run_stage(
        "Calculate financial metrics", lambda: calculate_metrics_file(history_path)
    )

    return PipelineResult(
        company=company,
        filing=filing,
        raw_filing_path=raw_filing_path,
        processed_filing_path=processed_filing_path,
        section_paths=section_paths,
        raw_facts_path=raw_facts_path,
        latest_facts_path=latest_facts_path,
        history_path=history_path,
        derived_metrics_path=derived_metrics_path,
    )

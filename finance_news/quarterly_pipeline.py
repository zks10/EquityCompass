"""Run the Equity Compass latest-quarterly-filing collection pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from finance_news.filing_downloader import download_filing
from finance_news.filing_processor import process_filing
from finance_news.pipeline import PipelineError, run_stage
from finance_news.sec_companies import Company, resolve_ticker
from finance_news.sec_filings import Filing, FilingLookupError, fetch_recent_filings
from finance_news.section_extractor import extract_quarterly_sections_file


@dataclass(frozen=True)
class QuarterlyPipelineResult:
    company: Company
    filing: Filing
    raw_filing_path: Path
    processed_filing_path: Path
    section_paths: tuple[Path, ...]


def run_quarterly_pipeline(
    ticker: str,
    force_download: bool = False,
    progress: Callable[[str], None] | None = None,
) -> QuarterlyPipelineResult:
    """Collect and process the latest 10-Q for ``ticker``."""
    notify = progress or (lambda _message: None)

    notify("1/5 Resolve ticker and SEC CIK")
    company = run_stage("Resolve ticker", lambda: resolve_ticker(ticker))

    notify("2/5 Find the latest 10-Q")

    def select_filing() -> Filing:
        filings = fetch_recent_filings(company.cik, limit=100)
        filing = next((item for item in filings if item.form == "10-Q"), None)
        if filing is None:
            raise FilingLookupError(
                f"No recent 10-Q filing was found for {company.ticker}."
            )
        return filing

    filing = run_stage("Select latest 10-Q", select_filing)

    notify("3/5 Download or reuse the raw 10-Q")
    raw_filing_path = run_stage(
        "Download quarterly filing",
        lambda: download_filing(
            filing, company.cik, overwrite=force_download
        ),
    )

    notify("4/5 Convert the 10-Q to clean text")
    processed_filing_path = run_stage(
        "Process quarterly filing", lambda: process_filing(raw_filing_path)
    )

    notify("5/5 Extract quarterly MD&A and Risk Factors")
    section_paths = tuple(
        run_stage(
            "Extract 10-Q sections",
            lambda: extract_quarterly_sections_file(processed_filing_path),
        )
    )

    return QuarterlyPipelineResult(
        company=company,
        filing=filing,
        raw_filing_path=raw_filing_path,
        processed_filing_path=processed_filing_path,
        section_paths=section_paths,
    )


__all__ = ["PipelineError", "QuarterlyPipelineResult", "run_quarterly_pipeline"]

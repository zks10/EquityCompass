"""Run recent SEC 8-K material-event collection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from finance_news.event_extractor import (
    EventExtractionError,
    extract_8k_items,
    item_metadata,
    save_event_items,
    save_event_manifest,
)
from finance_news.filing_downloader import download_filing
from finance_news.filing_processor import process_filing
from finance_news.pipeline import PipelineError, run_stage
from finance_news.sec_companies import Company, resolve_ticker
from finance_news.sec_filings import Filing, FilingLookupError, fetch_recent_filings


@dataclass(frozen=True)
class EventsPipelineResult:
    company: Company
    filings: tuple[Filing, ...]
    manifest_path: Path
    event_item_count: int


def run_events_pipeline(
    ticker: str,
    limit: int = 3,
    force_download: bool = False,
    progress: Callable[[str], None] | None = None,
) -> EventsPipelineResult:
    """Collect and extract item sections from recent 8-K filings."""
    if limit < 1 or limit > 20:
        raise PipelineError("Validate limit failed: Limit must be between 1 and 20.")
    notify = progress or (lambda _message: None)

    notify("Resolve ticker and SEC CIK")
    company = run_stage("Resolve ticker", lambda: resolve_ticker(ticker))

    notify(f"Find the {limit} most recent 8-K filing(s)")

    def select_filings() -> tuple[Filing, ...]:
        filings = fetch_recent_filings(
            company.cik, limit=limit, forms=frozenset({"8-K"})
        )
        selected = tuple(filing for filing in filings if filing.form == "8-K")[:limit]
        if not selected:
            raise FilingLookupError(
                f"No recent 8-K filings were found for {company.ticker}."
            )
        return selected

    filings = run_stage("Select recent 8-K filings", select_filings)
    manifest_filings = []
    total_items = 0

    for index, filing in enumerate(filings, start=1):
        notify(f"Process 8-K {index}/{len(filings)} filed {filing.filing_date}")
        raw_path = run_stage(
            f"Download 8-K {filing.accession_number}",
            lambda filing=filing: download_filing(
                filing, company.cik, overwrite=force_download
            ),
        )
        processed_path = run_stage(
            f"Clean 8-K {filing.accession_number}",
            lambda raw_path=raw_path: process_filing(raw_path),
        )

        def extract_and_save() -> tuple[list, list[Path]]:
            text = processed_path.read_text(encoding="utf-8")
            items = extract_8k_items(text)
            paths = save_event_items(processed_path, items)
            return items, paths

        try:
            items, paths = run_stage(
                f"Extract 8-K items {filing.accession_number}", extract_and_save
            )
        except OSError as exc:
            raise PipelineError(
                f"Extract 8-K items {filing.accession_number} failed: {exc}"
            ) from exc

        total_items += len(items)
        manifest_filings.append(
            {
                "filing_date": filing.filing_date,
                "accession_number": filing.accession_number,
                "document_url": filing.document_url,
                "raw_path": str(raw_path),
                "processed_path": str(processed_path),
                "items": [
                    item_metadata(item, path) for item, path in zip(items, paths, strict=True)
                ],
            }
        )

    notify("Save normalized 8-K event manifest")
    manifest_path = run_stage(
        "Save 8-K event manifest",
        lambda: save_event_manifest(
            company.cik,
            company.ticker,
            company.name,
            manifest_filings,
        ),
    )
    return EventsPipelineResult(
        company=company,
        filings=filings,
        manifest_path=manifest_path,
        event_item_count=total_items,
    )


__all__ = ["EventExtractionError", "EventsPipelineResult", "run_events_pipeline"]

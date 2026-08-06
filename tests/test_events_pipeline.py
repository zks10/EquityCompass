"""Tests for recent SEC 8-K pipeline orchestration."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from finance_news.event_extractor import EventItem
from finance_news.events_pipeline import run_events_pipeline
from finance_news.pipeline import PipelineError
from finance_news.sec_companies import Company
from finance_news.sec_filings import Filing


COMPANY = Company(ticker="AAPL", name="Apple Inc.", cik="0000320193")
FILING = Filing(
    form="8-K",
    filing_date="2026-07-30",
    accession_number="0000320193-26-000018",
    primary_document="aapl-8k.htm",
    document_url="https://www.sec.gov/aapl-8k.htm",
)


class RunEventsPipelineTests(unittest.TestCase):
    @patch("finance_news.events_pipeline.save_event_manifest")
    @patch("finance_news.events_pipeline.save_event_items")
    @patch("finance_news.events_pipeline.extract_8k_items")
    @patch("finance_news.events_pipeline.process_filing")
    @patch("finance_news.events_pipeline.download_filing")
    @patch("finance_news.events_pipeline.fetch_recent_filings")
    @patch("finance_news.events_pipeline.resolve_ticker")
    def test_collects_recent_8k_items(
        self,
        mock_resolve: Mock,
        mock_filings: Mock,
        mock_download: Mock,
        mock_process: Mock,
        mock_extract: Mock,
        mock_save_items: Mock,
        mock_manifest: Mock,
    ) -> None:
        mock_resolve.return_value = COMPANY
        mock_filings.return_value = [FILING]
        mock_download.return_value = Path("raw.htm")
        item = EventItem("2.02", "Results", "Item 2.02 Results\nBody\n")
        mock_extract.return_value = [item]
        mock_save_items.return_value = [Path("events/item_2_02.txt")]
        mock_manifest.return_value = Path("eight_k_events.json")

        with tempfile.TemporaryDirectory() as temporary_directory:
            processed = Path(temporary_directory) / "filing.txt"
            processed.write_text("processed", encoding="utf-8")
            mock_process.return_value = processed
            result = run_events_pipeline("AAPL", limit=1, force_download=True)

        self.assertEqual(result.event_item_count, 1)
        self.assertEqual(result.manifest_path, Path("eight_k_events.json"))
        mock_download.assert_called_once_with(FILING, COMPANY.cik, overwrite=True)

    def test_rejects_invalid_limit(self) -> None:
        with self.assertRaisesRegex(PipelineError, "between 1 and 20"):
            run_events_pipeline("AAPL", limit=0)

    @patch("finance_news.events_pipeline.fetch_recent_filings", return_value=[])
    @patch("finance_news.events_pipeline.resolve_ticker", return_value=COMPANY)
    def test_reports_no_8k_filings(
        self, _mock_resolve: Mock, _mock_filings: Mock
    ) -> None:
        with self.assertRaisesRegex(PipelineError, "Select recent 8-K filings failed"):
            run_events_pipeline("AAPL")


if __name__ == "__main__":
    unittest.main()

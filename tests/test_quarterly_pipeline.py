"""Tests for latest 10-Q pipeline orchestration."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from finance_news.pipeline import PipelineError
from finance_news.quarterly_pipeline import run_quarterly_pipeline
from finance_news.sec_companies import Company
from finance_news.sec_filings import Filing


COMPANY = Company(ticker="AAPL", name="Apple Inc.", cik="0000320193")
QUARTERLY_FILING = Filing(
    form="10-Q",
    filing_date="2026-07-31",
    accession_number="0000320193-26-000020",
    primary_document="aapl-10q.htm",
    document_url="https://www.sec.gov/aapl-10q.htm",
)


class RunQuarterlyPipelineTests(unittest.TestCase):
    @patch("finance_news.quarterly_pipeline.extract_quarterly_sections_file")
    @patch("finance_news.quarterly_pipeline.process_filing")
    @patch("finance_news.quarterly_pipeline.download_filing")
    @patch("finance_news.quarterly_pipeline.fetch_recent_filings")
    @patch("finance_news.quarterly_pipeline.resolve_ticker")
    def test_runs_all_quarterly_stages(
        self,
        mock_resolve: Mock,
        mock_filings: Mock,
        mock_download: Mock,
        mock_process: Mock,
        mock_sections: Mock,
    ) -> None:
        mock_resolve.return_value = COMPANY
        mock_filings.return_value = [QUARTERLY_FILING]
        mock_download.return_value = Path("data/raw/10q.htm")
        mock_process.return_value = Path("data/processed/filing.txt")
        mock_sections.return_value = [Path("data/processed/sections/mda.txt")]
        progress = Mock()

        result = run_quarterly_pipeline(
            "AAPL", force_download=True, progress=progress
        )

        self.assertEqual(result.filing.form, "10-Q")
        mock_download.assert_called_once_with(
            QUARTERLY_FILING, COMPANY.cik, overwrite=True
        )
        self.assertEqual(progress.call_count, 5)

    @patch("finance_news.quarterly_pipeline.fetch_recent_filings", return_value=[])
    @patch("finance_news.quarterly_pipeline.resolve_ticker", return_value=COMPANY)
    def test_reports_missing_10q(
        self, _mock_resolve: Mock, _mock_filings: Mock
    ) -> None:
        with self.assertRaisesRegex(PipelineError, "Select latest 10-Q failed"):
            run_quarterly_pipeline("AAPL")


if __name__ == "__main__":
    unittest.main()

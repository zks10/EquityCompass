"""Tests for complete Phase 1 pipeline orchestration."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from finance_news.pipeline import PipelineError, run_pipeline
from finance_news.sec_companies import Company, CompanyLookupError
from finance_news.sec_filings import Filing


COMPANY = Company(ticker="AAPL", name="Apple Inc.", cik="0000320193")
FILING = Filing(
    form="10-K",
    filing_date="2025-10-31",
    accession_number="0000320193-25-000079",
    primary_document="aapl.htm",
    document_url="https://www.sec.gov/aapl.htm",
)


class RunPipelineTests(unittest.TestCase):
    @patch("finance_news.pipeline.calculate_metrics_file")
    @patch("finance_news.pipeline.save_financial_history")
    @patch("finance_news.pipeline.extract_annual_history")
    @patch("finance_news.pipeline.save_financial_facts")
    @patch("finance_news.pipeline.extract_latest_annual_facts")
    @patch("finance_news.pipeline.fetch_company_facts")
    @patch("finance_news.pipeline.extract_sections_file")
    @patch("finance_news.pipeline.process_filing")
    @patch("finance_news.pipeline.download_filing")
    @patch("finance_news.pipeline.fetch_recent_filings")
    @patch("finance_news.pipeline.resolve_ticker")
    def test_runs_every_stage_and_returns_paths(
        self,
        mock_resolve: Mock,
        mock_filings: Mock,
        mock_download: Mock,
        mock_process: Mock,
        mock_sections: Mock,
        mock_fetch_facts: Mock,
        mock_extract_latest: Mock,
        mock_save_latest: Mock,
        mock_extract_history: Mock,
        mock_save_history: Mock,
        mock_calculate: Mock,
    ) -> None:
        mock_resolve.return_value = COMPANY
        mock_filings.return_value = [FILING]
        mock_download.return_value = Path("data/raw/filing.htm")
        mock_process.return_value = Path("data/processed/filing.txt")
        mock_sections.return_value = [Path("data/processed/sections/business.txt")]
        mock_fetch_facts.return_value = {"entityName": "Apple Inc."}
        mock_extract_latest.return_value = [Mock()]
        mock_save_latest.return_value = (
            Path("data/raw/companyfacts.json"),
            Path("data/processed/financial_facts.json"),
        )
        mock_extract_history.return_value = {"revenue": [Mock()]}
        mock_save_history.return_value = (
            Path("data/raw/companyfacts.json"),
            Path("data/processed/financial_history.json"),
        )
        mock_calculate.return_value = Path("data/processed/derived_metrics.json")
        progress = Mock()

        result = run_pipeline(
            "aapl", years=3, force_download=True, progress=progress
        )

        self.assertEqual(result.company, COMPANY)
        self.assertEqual(result.filing, FILING)
        self.assertEqual(
            result.derived_metrics_path, Path("data/processed/derived_metrics.json")
        )
        mock_download.assert_called_once_with(
            FILING, COMPANY.cik, overwrite=True
        )
        mock_extract_history.assert_called_once_with(
            mock_fetch_facts.return_value, years=3
        )
        mock_calculate.assert_called_once_with(
            Path("data/processed/financial_history.json")
        )
        self.assertEqual(progress.call_count, 9)

    @patch("finance_news.pipeline.fetch_recent_filings", return_value=[])
    @patch("finance_news.pipeline.resolve_ticker", return_value=COMPANY)
    def test_reports_missing_10k_stage(
        self, _mock_resolve: Mock, _mock_filings: Mock
    ) -> None:
        with self.assertRaisesRegex(PipelineError, "Select latest 10-K failed"):
            run_pipeline("AAPL")

    @patch("finance_news.pipeline.fetch_recent_filings")
    @patch("finance_news.pipeline.resolve_ticker", return_value=COMPANY)
    def test_explains_foreign_20f_issuer_scope(
        self, _mock_resolve: Mock, mock_filings: Mock
    ) -> None:
        mock_filings.return_value = [
            Filing(
                form="20-F",
                filing_date="2026-03-01",
                accession_number="0000000000-26-000001",
                primary_document="foreign.htm",
                document_url="https://www.sec.gov/foreign.htm",
            )
        ]

        with self.assertRaisesRegex(PipelineError, "foreign private issuer"):
            run_pipeline("AAPL")

    @patch("finance_news.pipeline.resolve_ticker")
    def test_reports_resolver_stage_failure(self, mock_resolve: Mock) -> None:
        mock_resolve.side_effect = CompanyLookupError("SEC unavailable")

        with self.assertRaisesRegex(PipelineError, "Resolve ticker failed"):
            run_pipeline("AAPL")


if __name__ == "__main__":
    unittest.main()

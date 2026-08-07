"""Tests for the data summary used by the Streamlit dashboard."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from finance_news.dashboard import DashboardError, analyze_ticker
from finance_news.pipeline import PipelineError
from finance_news.sec_companies import Company
from finance_news.sec_filings import Filing


COMPANY = Company(ticker="AAPL", name="Apple Inc.", cik="0000320193")
TEN_K = Filing("10-K", "2025-10-31", "annual", "10k.htm", "https://example/10k")
TEN_Q = Filing("10-Q", "2026-08-01", "quarterly", "10q.htm", "https://example/10q")


class AnalyzeTickerTests(unittest.TestCase):
    @patch("finance_news.dashboard.run_news_pipeline")
    @patch("finance_news.dashboard.run_quarterly_pipeline")
    @patch("finance_news.dashboard.run_pipeline")
    def test_returns_summary_from_pipeline_results_and_saved_news(
        self, mock_annual: Mock, mock_quarterly: Mock, mock_news: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            latest_facts_path = Path(directory) / "financial_facts.json"
            latest_facts_path.write_text(
                json.dumps(
                    {
                        "facts": [
                            {"metric": "revenue", "value": 120_000_000_000},
                            {"metric": "net_income", "value": 24_000_000_000},
                            {"metric": "assets", "value": 300_000_000_000},
                            {"metric": "liabilities", "value": 150_000_000_000},
                            {
                                "metric": "operating_cash_flow",
                                "value": 30_000_000_000,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            metrics_path = Path(directory) / "derived_metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "periods": [
                            {
                                "fiscal_year": 2025,
                                "period_end": "2025-09-27",
                                "revenue_growth_percent": 20.0,
                                "net_profit_margin_percent": 20.0,
                                "liabilities_to_assets_percent": 50.0,
                                "operating_cash_flow_margin_percent": 25.0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            articles_path = Path(directory) / "articles.json"
            articles_path.write_text(
                json.dumps({"article_count": 7}), encoding="utf-8"
            )
            mock_annual.return_value = SimpleNamespace(
                company=COMPANY,
                filing=TEN_K,
                latest_facts_path=latest_facts_path,
                derived_metrics_path=metrics_path,
            )
            mock_quarterly.return_value = SimpleNamespace(filing=TEN_Q)
            mock_news.return_value = SimpleNamespace(articles_path=articles_path)
            progress = Mock()

            summary = analyze_ticker("aapl", progress=progress)

        self.assertEqual(summary.company_name, "Apple Inc.")
        self.assertEqual(summary.cik, "0000320193")
        self.assertEqual(summary.latest_10k_date, "2025-10-31")
        self.assertEqual(summary.latest_10q_date, "2026-08-01")
        self.assertEqual(summary.news_article_count, 7)
        self.assertEqual(summary.financials.revenue, 120_000_000_000)
        self.assertEqual(summary.financials.revenue_growth_percent, 20.0)
        self.assertEqual(summary.financials.fiscal_year, 2025)
        self.assertTrue(progress.called)

    @patch("finance_news.dashboard.run_pipeline")
    def test_reports_pipeline_error(self, mock_annual: Mock) -> None:
        mock_annual.side_effect = PipelineError("SEC unavailable")

        with self.assertRaisesRegex(DashboardError, "SEC unavailable"):
            analyze_ticker("AAPL")

    @patch("finance_news.dashboard.run_news_pipeline")
    @patch("finance_news.dashboard.run_quarterly_pipeline")
    @patch("finance_news.dashboard.run_pipeline")
    def test_reports_unreadable_saved_news(
        self, mock_annual: Mock, mock_quarterly: Mock, mock_news: Mock
    ) -> None:
        mock_annual.return_value = SimpleNamespace(company=COMPANY, filing=TEN_K)
        mock_quarterly.return_value = SimpleNamespace(filing=TEN_Q)
        mock_news.return_value = SimpleNamespace(
            articles_path=Path("missing-articles.json")
        )

        with self.assertRaisesRegex(DashboardError, "saved news results"):
            analyze_ticker("AAPL")

    @patch("finance_news.dashboard.run_news_pipeline")
    @patch("finance_news.dashboard.run_quarterly_pipeline")
    @patch("finance_news.dashboard.run_pipeline")
    def test_reports_unreadable_financial_outputs(
        self, mock_annual: Mock, mock_quarterly: Mock, mock_news: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            articles_path = Path(directory) / "articles.json"
            articles_path.write_text(
                json.dumps({"article_count": 2}), encoding="utf-8"
            )
            mock_annual.return_value = SimpleNamespace(
                company=COMPANY,
                filing=TEN_K,
                latest_facts_path=Path(directory) / "missing-facts.json",
                derived_metrics_path=Path(directory) / "missing-metrics.json",
            )
            mock_quarterly.return_value = SimpleNamespace(filing=TEN_Q)
            mock_news.return_value = SimpleNamespace(articles_path=articles_path)

            with self.assertRaisesRegex(DashboardError, "financial facts"):
                analyze_ticker("AAPL")


if __name__ == "__main__":
    unittest.main()

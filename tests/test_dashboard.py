"""Tests for the data summary used by the Streamlit dashboard."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from finance_news.dashboard import (
    DashboardError,
    _read_annual_sections,
    analyze_ticker,
)
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
            history_path = Path(directory) / "financial_history.json"
            history_path.write_text(
                json.dumps(
                    {
                        "metrics": {
                            metric: [
                                {
                                    "fiscal_year": 2025,
                                    "period_end": "2025-09-27",
                                    "value": value,
                                },
                                {
                                    "fiscal_year": 2024,
                                    "period_end": "2024-09-28",
                                    "value": previous_value,
                                },
                            ]
                            for metric, value, previous_value in (
                                ("revenue", 120, 100),
                                ("net_income", 24, 18),
                                ("assets", 300, 275),
                                ("liabilities", 150, 140),
                                ("operating_cash_flow", 30, 25),
                            )
                        }
                    }
                ),
                encoding="utf-8",
            )
            articles_path = Path(directory) / "articles.json"
            articles_path.write_text(
                json.dumps(
                    {
                        "article_count": 2,
                        "articles": [
                            {
                                "title": "Apple announces a new product",
                                "publisher": "Example News",
                                "published_at": "2026-08-06T12:00:00Z",
                                "url": "https://example.com/apple-product",
                            },
                            {
                                "title": "Apple reports results",
                                "publisher": "Example Business",
                                "published_at": "2026-08-05T18:30:00Z",
                                "url": "https://example.com/apple-results",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            section_paths = []
            for filename, content in (
                ("business.txt", "The company designs consumer products."),
                ("risk_factors.txt", "Competition may affect future results."),
                ("mda.txt", "Management discusses operations and liquidity."),
            ):
                section_path = Path(directory) / filename
                section_path.write_text(content, encoding="utf-8")
                section_paths.append(section_path)
            mock_annual.return_value = SimpleNamespace(
                company=COMPANY,
                filing=TEN_K,
                latest_facts_path=latest_facts_path,
                derived_metrics_path=metrics_path,
                history_path=history_path,
                section_paths=tuple(section_paths),
            )
            mock_quarterly.return_value = SimpleNamespace(filing=TEN_Q)
            mock_news.return_value = SimpleNamespace(articles_path=articles_path)
            progress = Mock()

            summary = analyze_ticker("aapl", progress=progress)

        self.assertEqual(summary.company_name, "Apple Inc.")
        self.assertEqual(summary.cik, "0000320193")
        self.assertEqual(summary.latest_10k_date, "2025-10-31")
        self.assertEqual(summary.latest_10q_date, "2026-08-01")
        self.assertEqual(summary.news_article_count, 2)
        self.assertEqual(len(summary.recent_news), 2)
        self.assertEqual(
            summary.recent_news[0].publisher, "Example News"
        )
        self.assertEqual(
            summary.recent_news[1].url, "https://example.com/apple-results"
        )
        self.assertIn("consumer products", summary.annual_sections.business)
        self.assertIn("Competition", summary.annual_sections.risk_factors)
        self.assertIn("liquidity", summary.annual_sections.mda)
        self.assertEqual(summary.financials.revenue, 120_000_000_000)
        self.assertEqual(summary.financials.revenue_growth_percent, 20.0)
        self.assertEqual(summary.financials.fiscal_year, 2025)
        self.assertEqual(len(summary.financial_history), 2)
        self.assertEqual(summary.financial_history[0].fiscal_year, 2025)
        self.assertEqual(summary.financial_history[1].revenue, 100)
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
                json.dumps({"article_count": 0, "articles": []}), encoding="utf-8"
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

    @patch("finance_news.dashboard.run_news_pipeline")
    @patch("finance_news.dashboard.run_quarterly_pipeline")
    @patch("finance_news.dashboard.run_pipeline")
    def test_reports_unreadable_financial_history(
        self, mock_annual: Mock, mock_quarterly: Mock, mock_news: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts_path = root / "financial_facts.json"
            facts_path.write_text(
                json.dumps(
                    {
                        "facts": [
                            {"metric": metric, "value": 1}
                            for metric in (
                                "revenue",
                                "net_income",
                                "assets",
                                "liabilities",
                                "operating_cash_flow",
                            )
                        ]
                    }
                ),
                encoding="utf-8",
            )
            metrics_path = root / "derived_metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "periods": [
                            {
                                "fiscal_year": 2025,
                                "period_end": "2025-12-31",
                                "revenue_growth_percent": 1.0,
                                "net_profit_margin_percent": 1.0,
                                "liabilities_to_assets_percent": 1.0,
                                "operating_cash_flow_margin_percent": 1.0,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            articles_path = root / "articles.json"
            articles_path.write_text(
                json.dumps({"article_count": 0, "articles": []}), encoding="utf-8"
            )
            mock_annual.return_value = SimpleNamespace(
                company=COMPANY,
                filing=TEN_K,
                latest_facts_path=facts_path,
                derived_metrics_path=metrics_path,
                history_path=root / "missing-history.json",
            )
            mock_quarterly.return_value = SimpleNamespace(filing=TEN_Q)
            mock_news.return_value = SimpleNamespace(articles_path=articles_path)

            with self.assertRaisesRegex(DashboardError, "financial history"):
                analyze_ticker("AAPL")

    @patch("finance_news.dashboard.run_news_pipeline")
    @patch("finance_news.dashboard.run_quarterly_pipeline")
    @patch("finance_news.dashboard.run_pipeline")
    def test_reports_invalid_saved_news_article(
        self, mock_annual: Mock, mock_quarterly: Mock, mock_news: Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            articles_path = Path(directory) / "articles.json"
            articles_path.write_text(
                json.dumps(
                    {
                        "article_count": 1,
                        "articles": [
                            {
                                "title": "Missing a valid link",
                                "publisher": "Example",
                                "published_at": "2026-08-06T12:00:00Z",
                                "url": "not-a-url",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            mock_annual.return_value = SimpleNamespace(company=COMPANY, filing=TEN_K)
            mock_quarterly.return_value = SimpleNamespace(filing=TEN_Q)
            mock_news.return_value = SimpleNamespace(articles_path=articles_path)

            with self.assertRaisesRegex(DashboardError, "invalid article"):
                analyze_ticker("AAPL")


class ReadAnnualSectionsTests(unittest.TestCase):
    def test_reads_sections_by_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for filename, content in (
                ("mda.txt", "Management analysis"),
                ("business.txt", "Business description"),
                ("risk_factors.txt", "Risk disclosures"),
            ):
                path = root / filename
                path.write_text(content, encoding="utf-8")
                paths.append(path)

            sections = _read_annual_sections(tuple(paths))

        self.assertEqual(sections.business, "Business description")
        self.assertEqual(sections.risk_factors, "Risk disclosures")
        self.assertEqual(sections.mda, "Management analysis")

    def test_reports_missing_section(self) -> None:
        with self.assertRaisesRegex(DashboardError, "risk_factors.txt"):
            _read_annual_sections(
                (Path("business.txt"), Path("mda.txt"))
            )


if __name__ == "__main__":
    unittest.main()

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
    FinancialOverview,
    RecentNewsArticle,
    _read_annual_sections,
    _read_event_manifest,
    _read_quarterly_sections,
    analyze_ticker,
    build_filing_preview,
    build_financial_insights,
    build_financial_snapshot_score,
    check_ticker_eligibility,
    detect_news_topics,
    explain_8k_item,
)
from finance_news.pipeline import PipelineError
from finance_news.sec_companies import Company
from finance_news.sec_filings import Filing


COMPANY = Company(ticker="AAPL", name="Apple Inc.", cik="0000320193")
TEN_K = Filing("10-K", "2025-10-31", "annual", "10k.htm", "https://example/10k")
TEN_Q = Filing("10-Q", "2026-08-01", "quarterly", "10q.htm", "https://example/10q")


class TickerEligibilityTests(unittest.TestCase):
    @patch("finance_news.dashboard.fetch_recent_filings")
    @patch("finance_news.dashboard.resolve_ticker", return_value=COMPANY)
    def test_accepts_domestic_10k_filer(
        self, _mock_resolve: Mock, mock_filings: Mock
    ) -> None:
        mock_filings.return_value = [TEN_K]

        result = check_ticker_eligibility("aapl")

        self.assertTrue(result.supported)
        self.assertIn("Apple", result.message)

    @patch("finance_news.dashboard.fetch_recent_filings")
    @patch("finance_news.dashboard.resolve_ticker", return_value=COMPANY)
    def test_rejects_foreign_20f_filer(
        self, _mock_resolve: Mock, mock_filings: Mock
    ) -> None:
        mock_filings.return_value = [
            Filing("20-F", "2026-03-01", "foreign", "20f.htm", "https://example/20f")
        ]

        result = check_ticker_eligibility("nok")

        self.assertFalse(result.supported)
        self.assertIn("foreign private issuer", result.message)


class FinancialInsightTests(unittest.TestCase):
    def test_explains_positive_financial_metrics_in_plain_language(self) -> None:
        financials = FinancialOverview(
            fiscal_year=2025,
            period_end="2025-09-27",
            revenue=120,
            net_income=24,
            assets=300,
            liabilities=150,
            operating_cash_flow=30,
            revenue_growth_percent=20.0,
            net_profit_margin_percent=20.0,
            liabilities_to_assets_percent=50.0,
            operating_cash_flow_margin_percent=25.0,
        )

        insights = build_financial_insights(financials)

        self.assertEqual(len(insights), 4)
        self.assertEqual(insights[0].label, "Growing")
        self.assertIn("increased 20.0%", insights[0].explanation)
        self.assertIn("20 dollars", insights[1].explanation)
        self.assertIn("comparison", insights[2].explanation)
        self.assertIn("25 dollars", insights[3].explanation)

    def test_explains_negative_and_unavailable_metrics(self) -> None:
        financials = FinancialOverview(
            fiscal_year=2025,
            period_end="2025-09-27",
            revenue=100,
            net_income=-5,
            assets=100,
            liabilities=90,
            operating_cash_flow=-2,
            revenue_growth_percent=-3.5,
            net_profit_margin_percent=-5.0,
            liabilities_to_assets_percent=90.0,
            operating_cash_flow_margin_percent=None,
        )

        insights = build_financial_insights(financials)

        self.assertEqual(insights[0].label, "Revenue declined")
        self.assertEqual(insights[1].label, "Reported a loss")
        self.assertEqual(insights[2].label, "Very high liabilities share")
        self.assertEqual(insights[3].label, "Not available")


class FinancialSnapshotScoreTests(unittest.TestCase):
    def make_financials(
        self,
        growth: float | None,
        margin: float | None,
        liabilities: float | None,
        cash_margin: float | None,
    ) -> FinancialOverview:
        return FinancialOverview(
            fiscal_year=2025,
            period_end="2025-12-31",
            revenue=100,
            net_income=10,
            assets=100,
            liabilities=50,
            operating_cash_flow=10,
            revenue_growth_percent=growth,
            net_profit_margin_percent=margin,
            liabilities_to_assets_percent=liabilities,
            operating_cash_flow_margin_percent=cash_margin,
        )

    def test_scores_four_favorable_metrics(self) -> None:
        result = build_financial_snapshot_score(
            self.make_financials(10.0, 25.0, 40.0, 25.0)
        )

        self.assertEqual(result.score, 100)
        self.assertEqual(result.available_components, 4)
        self.assertEqual(result.label, "Mostly favorable current signals")
        self.assertEqual([component.score for component in result.components], [100] * 4)

    def test_bounds_unfavorable_metrics_at_zero(self) -> None:
        result = build_financial_snapshot_score(
            self.make_financials(-20.0, -5.0, 120.0, -10.0)
        )

        self.assertEqual(result.score, 0)
        self.assertEqual([component.score for component in result.components], [0] * 4)

    def test_averages_available_metrics_and_labels_limited_data(self) -> None:
        result = build_financial_snapshot_score(
            self.make_financials(10.0, None, None, 0.0)
        )

        self.assertEqual(result.score, 50)
        self.assertEqual(result.available_components, 2)
        self.assertEqual(result.label, "Limited data")

    def test_reports_when_every_metric_is_unavailable(self) -> None:
        result = build_financial_snapshot_score(
            self.make_financials(None, None, None, None)
        )

        self.assertIsNone(result.score)
        self.assertEqual(result.available_components, 0)
        self.assertEqual(result.label, "Not enough data")


class Explain8KItemTests(unittest.TestCase):
    def test_explains_common_event_categories(self) -> None:
        self.assertIn("financial results", explain_8k_item("2.02"))
        self.assertIn("director or senior executive", explain_8k_item("5.02"))
        self.assertIn("supporting exhibits", explain_8k_item("9.01"))

    def test_returns_general_explanation_for_unknown_item(self) -> None:
        self.assertIn("event reported to the SEC", explain_8k_item("6.99"))


class BeginnerPreviewTests(unittest.TestCase):
    def test_builds_preview_from_substantive_filing_sentences(self) -> None:
        text = (
            "Item 1. Business\nCompany Background\n"
            "The company designs and sells consumer technology products worldwide. "
            "It also provides digital services to its customers. "
            "A third sentence should not appear."
        )

        preview = build_filing_preview(text)

        self.assertIn("consumer technology", preview)
        self.assertIn("digital services", preview)
        self.assertNotIn("third sentence", preview)

    def test_rejects_invalid_preview_sentence_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 1"):
            build_filing_preview("Some filing text.", max_sentences=0)

    def test_detects_and_counts_news_topics(self) -> None:
        articles = (
            RecentNewsArticle(
                "Company reports quarterly revenue results",
                "Example",
                "2026-08-01",
                "https://example.com/one",
            ),
            RecentNewsArticle(
                "Judge considers company antitrust lawsuit",
                "Example",
                "2026-08-02",
                "https://example.com/two",
            ),
            RecentNewsArticle(
                "Analyst reviews the company's stock",
                "Example",
                "2026-08-03",
                "https://example.com/three",
            ),
        )

        topics = detect_news_topics(articles)

        self.assertEqual(
            {topic.label: topic.article_count for topic in topics},
            {
                "Financial results": 1,
                "Investor commentary": 1,
                "Legal and regulation": 1,
            },
        )


class AnalyzeTickerTests(unittest.TestCase):
    def setUp(self) -> None:
        events_patcher = patch("finance_news.dashboard.run_events_pipeline")
        self.mock_events = events_patcher.start()
        self.addCleanup(events_patcher.stop)

    @patch("finance_news.dashboard.run_news_pipeline")
    @patch("finance_news.dashboard.run_quarterly_pipeline")
    @patch("finance_news.dashboard.run_pipeline")
    def test_returns_summary_from_pipeline_results_and_saved_news(
        self,
        mock_annual: Mock,
        mock_quarterly: Mock,
        mock_news: Mock,
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
                                ("capital_expenditures", 8, 7),
                                ("eps", 6.0, 5.0),
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
            quarterly_paths = []
            for filename, content in (
                ("mda.txt", "Quarterly management discussion."),
                ("risk_factors.txt", "Quarterly risks may change."),
            ):
                path = Path(directory) / "quarterly" / filename
                path.parent.mkdir(exist_ok=True)
                path.write_text(content, encoding="utf-8")
                quarterly_paths.append(path)
            mock_quarterly.return_value = SimpleNamespace(
                filing=TEN_Q, section_paths=tuple(quarterly_paths)
            )
            mock_news.return_value = SimpleNamespace(articles_path=articles_path)
            event_text_path = Path(directory) / "item_2_02.txt"
            event_text_path.write_text(
                "Item 2.02 Results of Operations\nThe company reported results.",
                encoding="utf-8",
            )
            manifest_path = Path(directory) / "eight_k_events.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "filings": [
                            {
                                "filing_date": "2026-07-30",
                                "accession_number": "0000320193-26-000018",
                                "document_url": "https://www.sec.gov/example-8k",
                                "items": [
                                    {
                                        "item_number": "2.02",
                                        "title": "Results of Operations",
                                        "text_path": str(event_text_path),
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.mock_events.return_value = SimpleNamespace(
                manifest_path=manifest_path
            )
            progress = Mock()

            summary = analyze_ticker("aapl", progress=progress)

        self.assertEqual(summary.company_name, "Apple Inc.")
        self.assertEqual(summary.ticker, "AAPL")
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
        self.assertIn("management", summary.quarterly_sections.mda)
        self.assertIn("risks", summary.quarterly_sections.risk_factors)
        self.assertEqual(summary.recent_events[0].filing_date, "2026-07-30")
        self.assertEqual(summary.recent_events[0].items[0].item_number, "2.02")
        self.assertIn("reported results", summary.recent_events[0].items[0].text)
        self.assertEqual(summary.financials.revenue, 120_000_000_000)
        self.assertEqual(summary.financials.revenue_growth_percent, 20.0)
        self.assertEqual(summary.financials.fiscal_year, 2025)
        self.assertEqual(len(summary.financial_history), 2)
        self.assertEqual(summary.financial_history[0].fiscal_year, 2025)
        self.assertEqual(summary.financial_history[1].revenue, 100)
        self.assertEqual(summary.financial_history[0].eps, 6.0)
        self.assertEqual(summary.financial_history[0].free_cash_flow, 22)
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

    def test_preserves_dashboard_when_section_is_missing(self) -> None:
        sections = _read_annual_sections(
            (Path("business.txt"), Path("mda.txt"))
        )

        self.assertEqual(sections.risk_factors, "")


class ReadQuarterlySectionsTests(unittest.TestCase):
    def test_reads_sections_by_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for filename, content in (
                ("risk_factors.txt", "Quarterly risk disclosures"),
                ("mda.txt", "Quarterly management analysis"),
            ):
                path = root / filename
                path.write_text(content, encoding="utf-8")
                paths.append(path)

            sections = _read_quarterly_sections(tuple(paths))

        self.assertEqual(sections.risk_factors, "Quarterly risk disclosures")
        self.assertEqual(sections.mda, "Quarterly management analysis")

    def test_preserves_dashboard_when_section_is_missing(self) -> None:
        sections = _read_quarterly_sections((Path("mda.txt"),))

        self.assertEqual(sections.risk_factors, "")


class ReadEventManifestTests(unittest.TestCase):
    def test_reads_filing_and_item_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text_path = root / "item_8_01.txt"
            text_path.write_text("Item 8.01 Other Events\nEvent body", encoding="utf-8")
            manifest_path = root / "events.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "filings": [
                            {
                                "filing_date": "2026-08-01",
                                "accession_number": "one",
                                "document_url": "https://www.sec.gov/one",
                                "items": [
                                    {
                                        "item_number": "8.01",
                                        "title": "Other Events",
                                        "text_path": str(text_path),
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            filings = _read_event_manifest(manifest_path)

        self.assertEqual(filings[0].items[0].title, "Other Events")
        self.assertIn("Event body", filings[0].items[0].text)

    def test_reports_missing_item_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "events.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "filings": [
                            {
                                "filing_date": "2026-08-01",
                                "accession_number": "one",
                                "document_url": "https://www.sec.gov/one",
                                "items": [
                                    {
                                        "item_number": "8.01",
                                        "title": "Other Events",
                                        "text_path": "missing-event.txt",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(DashboardError, "8-K events"):
                _read_event_manifest(manifest_path)


if __name__ == "__main__":
    unittest.main()

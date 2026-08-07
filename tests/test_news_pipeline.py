"""Tests for company-news pipeline orchestration."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from finance_news.news_collector import NewsArticle, NewsCollectionError
from finance_news.news_pipeline import NewsPipelineError, run_news_pipeline
from finance_news.sec_companies import Company


COMPANY = Company(ticker="AAPL", name="Apple Inc.", cik="0000320193")
ARTICLE = NewsArticle(
    title="Apple news",
    publisher="Example",
    published_at="2026-08-05T12:00:00Z",
    url="https://news.google.com/article",
    guid="one",
)


class RunNewsPipelineTests(unittest.TestCase):
    @patch("finance_news.news_pipeline.save_news_results")
    @patch("finance_news.news_pipeline.parse_news_feed")
    @patch("finance_news.news_pipeline.fetch_news_feed")
    @patch("finance_news.news_pipeline.resolve_ticker")
    def test_runs_all_news_stages(
        self,
        mock_resolve: Mock,
        mock_fetch: Mock,
        mock_parse: Mock,
        mock_save: Mock,
    ) -> None:
        mock_resolve.return_value = COMPANY
        mock_fetch.return_value = ("https://feed", b"xml")
        mock_parse.return_value = [ARTICLE]
        mock_save.return_value = (Path("feed.xml"), Path("articles.json"))
        progress = Mock()

        result = run_news_pipeline("AAPL", days=3, limit=10, progress=progress)

        self.assertEqual(result.article_count, 1)
        mock_parse.assert_called_once_with(b"xml", limit=10)
        self.assertEqual(progress.call_count, 4)

    @patch("finance_news.news_pipeline.fetch_news_feed")
    @patch("finance_news.news_pipeline.resolve_ticker", return_value=COMPANY)
    def test_reports_fetch_stage_failure(
        self, _mock_resolve: Mock, mock_fetch: Mock
    ) -> None:
        mock_fetch.side_effect = NewsCollectionError("feed unavailable")

        with self.assertRaisesRegex(NewsPipelineError, "Fetch company news failed"):
            run_news_pipeline("AAPL")


if __name__ == "__main__":
    unittest.main()

"""Tests for company-news RSS collection and normalization."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from finance_news.news_collector import (
    NewsCollectionError,
    build_news_query,
    fetch_news_feed,
    parse_news_feed,
    save_news_results,
)


SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Apple announces a new product - Example News</title>
    <link>https://news.google.com/rss/articles/one</link>
    <guid>article-one</guid>
    <pubDate>Wed, 05 Aug 2026 15:30:00 GMT</pubDate>
    <source url="https://example.com">Example News</source>
  </item>
  <item>
    <title>Apple reports quarterly results - Finance Daily</title>
    <link>https://news.google.com/rss/articles/two</link>
    <guid>article-two</guid>
    <pubDate>Tue, 04 Aug 2026 12:00:00 GMT</pubDate>
    <source url="https://finance.example">Finance Daily</source>
  </item>
  <item>
    <title>Duplicate title - Example News</title>
    <link>https://news.google.com/rss/articles/duplicate</link>
    <guid>article-one</guid>
    <pubDate>Mon, 03 Aug 2026 12:00:00 GMT</pubDate>
    <source>Example News</source>
  </item>
</channel></rss>"""


class ParseNewsFeedTests(unittest.TestCase):
    def test_builds_company_query_with_lookback(self) -> None:
        self.assertEqual(build_news_query("Apple Inc.", 7), '"Apple Inc." when:7d')

    def test_parses_deduplicates_and_normalizes_articles(self) -> None:
        articles = parse_news_feed(SAMPLE_RSS, limit=20)

        self.assertEqual(len(articles), 2)
        self.assertEqual(articles[0].title, "Apple announces a new product")
        self.assertEqual(articles[0].publisher, "Example News")
        self.assertEqual(articles[0].published_at, "2026-08-05T15:30:00Z")

    def test_applies_article_limit(self) -> None:
        self.assertEqual(len(parse_news_feed(SAMPLE_RSS, limit=1)), 1)

    def test_rejects_invalid_xml(self) -> None:
        with self.assertRaisesRegex(NewsCollectionError, "invalid XML"):
            parse_news_feed(b"not xml")

    @patch("finance_news.news_collector.requests.get")
    def test_handles_connection_failure(self, mock_get: Mock) -> None:
        mock_get.side_effect = requests.ConnectionError("network unavailable")

        with self.assertRaisesRegex(NewsCollectionError, "Could not connect"):
            fetch_news_feed("Apple")


class SaveNewsResultsTests(unittest.TestCase):
    def test_saves_raw_and_normalized_news(self) -> None:
        articles = parse_news_feed(SAMPLE_RSS)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_path, processed_path = save_news_results(
                SAMPLE_RSS,
                articles,
                "AAPL",
                "0000320193",
                "Apple Inc.",
                '"Apple Inc." when:7d',
                "https://news.google.com/rss/search?q=Apple",
                raw_root=root / "raw",
                processed_root=root / "processed",
            )

            self.assertEqual(raw_path.read_bytes(), SAMPLE_RSS)
            saved = json.loads(processed_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["article_count"], 2)
            self.assertEqual(saved["articles"][0]["publisher"], "Example News")
            self.assertFalse(any(root.rglob("*.part")))


if __name__ == "__main__":
    unittest.main()

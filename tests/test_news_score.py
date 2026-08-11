from datetime import datetime, timezone
import unittest
from types import SimpleNamespace

from finance_news.news_score import calculate_news_score


NOW = datetime(2026, 8, 10, 20, tzinfo=timezone.utc)


def article(title: str, publisher: str = "Reuters", published: str = "2026-08-10T18:00:00Z"):
    return SimpleNamespace(title=title, publisher=publisher, published_at=published)


class NewsScoreTests(unittest.TestCase):
    def test_positive_company_results_produce_positive_score(self):
        result = calculate_news_score([
            article("AMD beats estimates and raises guidance"),
            article("AMD reports record revenue on strong demand", "Bloomberg"),
            article("AMD wins contract for new data center chips", "CNBC"),
        ], as_of=NOW)
        self.assertGreaterEqual(result.value, 45)
        self.assertEqual(result.positive_count, 3)
        self.assertIn("positive", result.label.lower())

    def test_negative_events_produce_negative_score(self):
        result = calculate_news_score([
            article("Company cuts guidance after weak demand"),
            article("Company faces regulatory investigation and layoffs", "Bloomberg"),
        ], as_of=NOW)
        self.assertLessEqual(result.value, -35)
        self.assertEqual(result.negative_count, 2)

    def test_negation_reverses_phrase_direction(self):
        result = calculate_news_score([
            article("AMD does not cut guidance after earnings")
        ], as_of=NOW)
        self.assertGreater(result.value, 0)

    def test_duplicate_headlines_do_not_multiply_story_weight(self):
        duplicated = [
            article("AMD beats estimates and raises guidance", f"Publisher {index}")
            for index in range(8)
        ]
        result = calculate_news_score(duplicated, as_of=NOW)
        self.assertEqual(result.independent_story_count, 1)
        self.assertLess(result.confidence_value, 72)

    def test_recent_story_outweighs_old_story(self):
        result = calculate_news_score([
            article("AMD raises guidance after strong demand"),
            article("AMD cuts guidance after weak demand", published="2026-07-20T18:00:00Z"),
        ], as_of=NOW)
        self.assertGreater(result.value, 0)

    def test_non_directional_headlines_remain_neutral(self):
        result = calculate_news_score([
            article("AMD to present at annual technology conference"),
            article("What investors should know about AMD stock", "Yahoo Finance"),
        ], as_of=NOW)
        self.assertEqual(result.value, 0)
        self.assertEqual(result.neutral_count, 2)

    def test_empty_feed_is_low_confidence_neutral(self):
        result = calculate_news_score([], as_of=NOW)
        self.assertEqual(result.value, 0)
        self.assertEqual(result.confidence, "Low")

    def test_unrelated_market_articles_are_excluded(self):
        result = calculate_news_score([
            article("AMD raises guidance after strong demand"),
            article("Intel cuts guidance after weak demand"),
        ], as_of=NOW, company_terms=("AMD", "Advanced Micro Devices"))
        self.assertEqual(result.article_count, 1)
        self.assertGreater(result.value, 0)


if __name__ == "__main__":
    unittest.main()

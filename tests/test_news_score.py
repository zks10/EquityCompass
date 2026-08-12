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
        self.assertGreaterEqual(result.value, 4.5)
        self.assertEqual(result.positive_count, 3)
        self.assertIn("positive", result.label.lower())

    def test_negative_events_produce_negative_score(self):
        result = calculate_news_score([
            article("Company cuts guidance after weak demand"),
            article("Company faces regulatory investigation and layoffs", "Bloomberg"),
        ], as_of=NOW)
        self.assertLessEqual(result.value, -3.5)
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
        self.assertFalse(result.available)
        self.assertEqual(result.label, "No clear signal")

    def test_empty_feed_is_low_confidence_neutral(self):
        result = calculate_news_score([], as_of=NOW)
        self.assertEqual(result.value, 0)
        self.assertEqual(result.confidence, "Limited")
        self.assertFalse(result.available)

    def test_unrelated_market_articles_are_excluded(self):
        result = calculate_news_score([
            article("AMD raises guidance after strong demand"),
            article("Intel cuts guidance after weak demand"),
        ], as_of=NOW, company_terms=("AMD", "Advanced Micro Devices"))
        self.assertEqual(result.article_count, 1)
        self.assertGreater(result.value, 0)

    def test_common_positive_market_language_is_not_left_neutral(self):
        result = calculate_news_score([
            article("Microsoft revenue doubles and tops estimates"),
            article("Microsoft shares surge on accelerating growth", "Bloomberg"),
            article("Hedge funds favor Microsoft after strong fundamentals", "CNBC"),
        ], as_of=NOW, company_terms=("MSFT", "Microsoft"))
        self.assertEqual(result.positive_count, 3)
        self.assertGreaterEqual(result.value, 3.5)

    def test_balanced_price_action_language_has_similar_strength(self):
        positive = calculate_news_score([
            article("Microsoft shares surge after earnings")
        ], as_of=NOW, company_terms=("Microsoft",))
        negative = calculate_news_score([
            article("Microsoft shares slump after earnings")
        ], as_of=NOW, company_terms=("Microsoft",))
        self.assertAlmostEqual(abs(positive.value), abs(negative.value), delta=0.5)

    def test_old_articles_are_not_part_of_daily_score(self):
        result = calculate_news_score([
            article("Microsoft raises guidance", published="2026-08-08T12:00:00Z")
        ], as_of=NOW, company_terms=("Microsoft",))
        self.assertFalse(result.available)
        self.assertEqual(result.article_count, 0)

    def test_neutral_headlines_reduce_confidence_not_erase_direction(self):
        headlines = [article("Microsoft tops estimates after strong demand")]
        headlines.extend(
            article(f"Microsoft presents technology update number {index}", f"Source {index}")
            for index in range(5)
        )
        result = calculate_news_score(
            headlines, as_of=NOW, company_terms=("Microsoft",)
        )
        self.assertTrue(result.available)
        self.assertGreaterEqual(result.value, 1.5)
        self.assertEqual(result.positive_count, 1)

    def test_small_positive_score_has_plain_language_label(self):
        result = calculate_news_score([
            article("Microsoft stock gains after product update")
        ], as_of=NOW, company_terms=("Microsoft",))
        self.assertGreater(result.value, 0)
        self.assertLess(result.value, 2)
        self.assertEqual(result.label, "Slightly positive")
        self.assertIn("1 positive", result.summary)


if __name__ == "__main__":
    unittest.main()

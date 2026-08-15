from __future__ import annotations

import unittest
from datetime import date, timedelta

from finance_news.market_data import MarketOverview, PricePoint
from finance_news.news_score import NewsScore
from finance_news.short_term_score import calculate_short_term_score


def news_score(value: float | None, confidence: int = 60) -> NewsScore:
    available = value is not None
    return NewsScore(
        value=value or 0.0, available=available,
        label="Positive" if value and value > 0 else "Negative" if value else "No clear signal",
        confidence="Moderate" if available else "Limited",
        confidence_value=confidence if available else 0,
        article_count=3 if available else 0, independent_story_count=3 if available else 0,
        scored_article_count=2 if available else 0, positive_count=2 if value and value > 0 else 0,
        negative_count=2 if value and value < 0 else 0, neutral_count=1 if available else 0,
        summary="Fresh catalyst signal.", signals=(),
    )


def market(
    stock_returns: list[float], benchmark_returns: list[float],
    latest_volume_ratio: float = 1.0,
) -> MarketOverview:
    start = date.today() - timedelta(days=len(stock_returns) + 8)
    dates = []
    cursor = start
    while len(dates) < len(stock_returns) + 1:
        if cursor.weekday() < 5:
            dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    stock_close = 100.0
    benchmark_close = 100.0
    stock_points = [PricePoint(dates[0], stock_close, 1_000_000)]
    benchmark_points = [PricePoint(dates[0], benchmark_close)]
    for index, (stock_return, benchmark_return) in enumerate(
        zip(stock_returns, benchmark_returns), start=1
    ):
        stock_close *= 1 + stock_return
        benchmark_close *= 1 + benchmark_return
        volume = 1_000_000 * (latest_volume_ratio if index == len(stock_returns) else 1)
        stock_points.append(PricePoint(dates[index], stock_close, volume))
        benchmark_points.append(PricePoint(dates[index], benchmark_close))
    return MarketOverview(
        ticker="TEST", latest_price=stock_close,
        previous_close=stock_points[-2].close, as_of=dates[-1],
        points=tuple(stock_points), intraday_points=(),
        benchmark_points=tuple(benchmark_points),
    )


class ShortTermScoreTests(unittest.TestCase):
    def test_positive_market_and_catalyst_signals_create_tailwind(self):
        result = calculate_short_term_score(
            market([0.002] * 15 + [0.015] * 5, [0.001] * 20, 1.8),
            news_score(5.0),
        )
        self.assertTrue(result.available)
        self.assertGreater(result.value, 2)
        self.assertIn(result.label, {"Supportive", "Strong tailwind"})

    def test_market_wide_move_is_removed_by_relative_strength(self):
        result = calculate_short_term_score(
            market([0.02] * 20, [0.02] * 20), news_score(None)
        )
        relative = next(c for c in result.components if c.key == "relative")
        self.assertAlmostEqual(relative.value or 0, 0, delta=0.1)

    def test_high_volatility_stock_is_normalized(self):
        steady = calculate_short_term_score(
            market([0.001, -0.001] * 8 + [0.01] * 4, [0.0] * 20), news_score(None)
        )
        volatile = calculate_short_term_score(
            market([0.04, -0.04] * 8 + [0.01] * 4, [0.0] * 20), news_score(None)
        )
        steady_price = next(c.value for c in steady.components if c.key == "price")
        volatile_price = next(c.value for c in volatile.components if c.key == "price")
        self.assertGreater(steady_price or 0, volatile_price or 0)

    def test_volume_confirms_direction_but_does_not_create_it(self):
        moving = calculate_short_term_score(
            market([0.001] * 15 + [0.012] * 5, [0.0] * 20, 2.0), news_score(None)
        )
        flat = calculate_short_term_score(
            market([0.0] * 20, [0.0] * 20, 2.0), news_score(None)
        )
        moving_volume = next(c.value for c in moving.components if c.key == "volume")
        flat_volume = next(c.value for c in flat.components if c.key == "volume")
        self.assertGreater(moving_volume or 0, 0)
        self.assertEqual(flat_volume, 0)

    def test_conflicting_news_and_price_are_labeled(self):
        result = calculate_short_term_score(
            market([-0.002] * 15 + [-0.015] * 5, [0.0] * 20, 1.5),
            news_score(7.0),
        )
        self.assertTrue(result.conflicting)
        self.assertIn("disagree", result.summary)

    def test_missing_market_data_never_becomes_catalyst_only_score(self):
        result = calculate_short_term_score(None, news_score(9.0))
        self.assertFalse(result.available)
        self.assertEqual(result.value, 0)

    def test_unusable_prices_return_unavailable_instead_of_crashing(self):
        invalid_market = MarketOverview(
            ticker="TEST", latest_price=0.0, previous_close=0.0,
            as_of=date.today().isoformat(),
            points=(
                PricePoint((date.today() - timedelta(days=1)).isoformat(), 0.0),
                PricePoint(date.today().isoformat(), 0.0),
            ),
            intraday_points=(), benchmark_points=(),
        )

        result = calculate_short_term_score(invalid_market, news_score(9.0))

        self.assertFalse(result.available)
        self.assertEqual(result.value, 0.0)
        self.assertEqual(result.label, "Market data unavailable")


if __name__ == "__main__":
    unittest.main()

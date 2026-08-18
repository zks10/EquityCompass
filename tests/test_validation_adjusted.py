"""Tests for the industry-adjusted Phase 2 comparison."""

from __future__ import annotations

import unittest

from finance_news.validation_adjusted import analyze_industry_cohorts


class IndustryAdjustedTests(unittest.TestCase):
    def test_compares_scores_within_industry_year(self) -> None:
        records = [
            {
                "ticker": "AAPL",
                "industry": "Technology",
                "fiscal_year": 2024,
                "score": 80,
                "excess_return_percent": 20.0,
            },
            {
                "ticker": "MSFT",
                "industry": "Technology",
                "fiscal_year": 2024,
                "score": 60,
                "excess_return_percent": 0.0,
            },
        ]

        result = analyze_industry_cohorts(records)

        self.assertEqual(result["eligible_cohorts"], 1)
        self.assertEqual(result["adjusted_observations"], 2)
        self.assertEqual(
            result["highest_vs_lowest"]["median_excess_return_difference_percent"],
            20.0,
        )
        self.assertEqual(
            result["highest_vs_lowest"]["higher_score_win_rate_percent"], 100.0
        )
        self.assertEqual(
            result["group_results"]["Higher"][
                "median_industry_adjusted_excess_return_percent"
            ],
            10.0,
        )

    def test_ignores_single_observation_cohorts(self) -> None:
        result = analyze_industry_cohorts(
            [
                {
                    "ticker": "AAPL",
                    "industry": "Technology",
                    "fiscal_year": 2024,
                    "score": 80,
                    "excess_return_percent": 20.0,
                }
            ]
        )

        self.assertEqual(result["eligible_cohorts"], 0)
        self.assertEqual(result["adjusted_observations"], 0)


if __name__ == "__main__":
    unittest.main()

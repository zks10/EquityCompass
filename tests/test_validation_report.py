"""Tests for the Phase 2 pilot comparison report."""

from __future__ import annotations

import unittest

from finance_news.validation_report import score_group, summarize_group


class ValidationReportTests(unittest.TestCase):
    def test_assigns_predefined_score_groups(self) -> None:
        self.assertEqual(score_group(75), "Higher")
        self.assertEqual(score_group(74), "Middle")
        self.assertEqual(score_group(50), "Middle")
        self.assertEqual(score_group(49), "Lower")

    def test_summarizes_returns_benchmark_rate_and_drawdown(self) -> None:
        result = summarize_group(
            [
                {
                    "company_return_percent": 20.0,
                    "excess_return_percent": 5.0,
                    "max_drawdown_12_months_percent": -10.0,
                },
                {
                    "company_return_percent": -10.0,
                    "excess_return_percent": -15.0,
                    "max_drawdown_12_months_percent": -30.0,
                },
            ]
        )

        self.assertEqual(result["observations"], 2)
        self.assertEqual(result["average_company_return_percent"], 5.0)
        self.assertEqual(result["median_excess_return_percent"], -5.0)
        self.assertEqual(result["spy_beating_rate_percent"], 50.0)
        self.assertEqual(result["average_max_drawdown_percent"], -20.0)

    def test_empty_group_stays_empty(self) -> None:
        result = summarize_group([])

        self.assertEqual(result["observations"], 0)
        self.assertIsNone(result["median_company_return_percent"])


if __name__ == "__main__":
    unittest.main()

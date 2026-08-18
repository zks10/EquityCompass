"""Tests for Phase 2 component and industry diagnostics."""

from __future__ import annotations

import unittest

from finance_news.validation_diagnostics import INDUSTRIES, TICKER_INDUSTRY, _spearman


class ValidationDiagnosticsTests(unittest.TestCase):
    def test_pilot_contains_four_companies_in_each_industry(self) -> None:
        self.assertEqual(len(INDUSTRIES), 5)
        self.assertTrue(all(len(tickers) == 4 for tickers in INDUSTRIES.values()))
        self.assertEqual(len(TICKER_INDUSTRY), 20)

    def test_spearman_detects_ordered_relationships(self) -> None:
        self.assertEqual(_spearman([1, 2, 3], [10, 20, 30]), 1.0)
        self.assertEqual(_spearman([1, 2, 3], [30, 20, 10]), -1.0)

    def test_spearman_reports_constant_input_as_unavailable(self) -> None:
        self.assertIsNone(_spearman([1, 1, 1], [10, 20, 30]))


if __name__ == "__main__":
    unittest.main()

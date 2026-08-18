"""Tests for offline Phase 2 market outcomes."""

from __future__ import annotations

import unittest

import pandas as pd

from finance_news.validation_outcomes import calculate_forward_outcomes


class CalculateForwardOutcomesTests(unittest.TestCase):
    def test_uses_next_common_close_and_calculates_returns(self) -> None:
        dates = pd.to_datetime(
            ["2024-01-02", "2024-07-02", "2025-01-02", "2026-01-02"]
        )
        stock = pd.Series([100.0, 110.0, 120.0, 150.0], index=dates)
        spy = pd.Series([200.0, 210.0, 220.0, 240.0], index=dates)

        result = calculate_forward_outcomes(stock, spy, "2024-01-01")

        self.assertEqual(result["measurement_start_date"], "2024-01-02")
        self.assertEqual(
            result["horizons"]["12_months"]["company_return_percent"], 20.0
        )
        self.assertEqual(
            result["horizons"]["12_months"]["excess_return_percent"], 10.0
        )
        self.assertEqual(result["max_drawdown_12_months_percent"], 0.0)

    def test_marks_unelapsed_horizons_pending(self) -> None:
        dates = pd.to_datetime(["2025-01-02", "2025-07-02"])
        prices = pd.Series([100.0, 110.0], index=dates)

        result = calculate_forward_outcomes(prices, prices, "2025-01-01")

        self.assertEqual(result["horizons"]["6_months"]["status"], "completed")
        self.assertEqual(result["horizons"]["12_months"]["status"], "pending")
        self.assertIsNone(result["max_drawdown_12_months_percent"])

    def test_calculates_peak_to_trough_drawdown(self) -> None:
        dates = pd.to_datetime(
            ["2024-01-02", "2024-04-02", "2024-07-02", "2025-01-02"]
        )
        stock = pd.Series([100.0, 125.0, 75.0, 110.0], index=dates)
        spy = pd.Series([100.0, 105.0, 103.0, 110.0], index=dates)

        result = calculate_forward_outcomes(stock, spy, "2024-01-01")

        self.assertEqual(result["max_drawdown_12_months_percent"], -40.0)


if __name__ == "__main__":
    unittest.main()

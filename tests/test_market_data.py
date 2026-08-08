"""Tests for the small market snapshot used on the Overview tab."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import pandas as pd

from finance_news.market_data import MarketDataError, fetch_market_overview


class MarketOverviewTests(unittest.TestCase):
    @patch("finance_news.market_data.yf.Ticker")
    def test_builds_latest_price_change_and_history(self, mock_ticker: Mock) -> None:
        daily = pd.DataFrame(
            {"Close": [100.0, 104.0, 102.0]},
            index=pd.to_datetime(["2026-08-05", "2026-08-06", "2026-08-07"]),
        )
        intraday = pd.DataFrame(
            {"Close": [101.0, 102.0]},
            index=pd.to_datetime(["2026-08-07 09:30", "2026-08-07 09:35"]),
        )
        mock_ticker.return_value.history.side_effect = [daily, intraday]

        result = fetch_market_overview(" aapl ")

        self.assertEqual(result.ticker, "AAPL")
        self.assertEqual(result.latest_price, 102.0)
        self.assertEqual(result.previous_close, 104.0)
        self.assertAlmostEqual(result.price_change, -2.0)
        self.assertAlmostEqual(result.price_change_percent, -1.9230769)
        self.assertEqual(result.as_of, "2026-08-07")
        self.assertEqual(len(result.points), 3)
        self.assertEqual(len(result.intraday_points), 2)

    @patch("finance_news.market_data.yf.Ticker")
    def test_rejects_missing_price_history(self, mock_ticker: Mock) -> None:
        mock_ticker.return_value.history.return_value = pd.DataFrame()

        with self.assertRaisesRegex(MarketDataError, "Not enough"):
            fetch_market_overview("AAPL")

    def test_rejects_empty_ticker(self) -> None:
        with self.assertRaisesRegex(MarketDataError, "ticker"):
            fetch_market_overview("  ")


if __name__ == "__main__":
    unittest.main()

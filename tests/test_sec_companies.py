"""Tests for the SEC company ticker resolver."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from finance_news.sec_companies import (
    CompanyLookupError,
    SEC_TICKERS_URL,
    TickerNotFoundError,
    resolve_ticker,
)


SEC_RECORDS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


def successful_response(records: dict | None = None) -> Mock:
    response = Mock()
    response.json.return_value = SEC_RECORDS if records is None else records
    return response


class ResolveTickerTests(unittest.TestCase):
    @patch("finance_news.sec_companies.requests.get")
    def test_resolves_aapl_and_pads_cik(self, mock_get: Mock) -> None:
        mock_get.return_value = successful_response()

        company = resolve_ticker("AAPL")

        self.assertEqual(company.ticker, "AAPL")
        self.assertEqual(company.name, "Apple Inc.")
        self.assertEqual(company.cik, "0000320193")
        mock_get.assert_called_once()
        self.assertEqual(mock_get.call_args.args[0], SEC_TICKERS_URL)

    @patch("finance_news.sec_companies.requests.get")
    def test_normalizes_lowercase_and_whitespace(self, mock_get: Mock) -> None:
        mock_get.return_value = successful_response()

        company = resolve_ticker("  aapl  ")

        self.assertEqual(company.ticker, "AAPL")

    @patch("finance_news.sec_companies.requests.get")
    def test_rejects_empty_ticker_without_network_request(self, mock_get: Mock) -> None:
        with self.assertRaisesRegex(CompanyLookupError, "Ticker cannot be empty"):
            resolve_ticker("   ")

        mock_get.assert_not_called()

    @patch("finance_news.sec_companies.requests.get")
    def test_reports_unknown_ticker(self, mock_get: Mock) -> None:
        mock_get.return_value = successful_response()

        with self.assertRaisesRegex(TickerNotFoundError, "NOTREAL"):
            resolve_ticker("NOTREAL")

    @patch("finance_news.sec_companies.requests.get")
    def test_handles_connection_failure(self, mock_get: Mock) -> None:
        mock_get.side_effect = requests.ConnectionError("network unavailable")

        with self.assertRaisesRegex(CompanyLookupError, "Could not connect"):
            resolve_ticker("AAPL")

    @patch("finance_news.sec_companies.requests.get")
    def test_handles_http_failure(self, mock_get: Mock) -> None:
        response = Mock(status_code=403)
        mock_get.return_value.raise_for_status.side_effect = requests.HTTPError(
            response=response
        )

        with self.assertRaisesRegex(CompanyLookupError, "HTTP status 403"):
            resolve_ticker("AAPL")

    @patch("finance_news.sec_companies.requests.get")
    def test_handles_invalid_json(self, mock_get: Mock) -> None:
        mock_get.return_value.json.side_effect = requests.JSONDecodeError(
            "Invalid JSON", "", 0
        )

        with self.assertRaisesRegex(CompanyLookupError, "unreadable response"):
            resolve_ticker("AAPL")


if __name__ == "__main__":
    unittest.main()

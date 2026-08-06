"""Tests for recent SEC filing metadata retrieval."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from finance_news.sec_filings import FilingLookupError, fetch_recent_filings


RECENT_FILINGS = {
    "filings": {
        "recent": {
            "form": ["8-K", "4", "10-Q", "10-K"],
            "filingDate": ["2026-08-01", "2026-07-31", "2026-07-30", "2025-10-31"],
            "accessionNumber": [
                "0000320193-26-000001",
                "0000320193-26-000002",
                "0000320193-26-000003",
                "0000320193-25-000004",
            ],
            "primaryDocument": ["aapl-8k.htm", "ownership.xml", "aapl-10q.htm", "aapl-10k.htm"],
        }
    }
}


def successful_response(payload: dict | None = None) -> Mock:
    response = Mock()
    response.json.return_value = RECENT_FILINGS if payload is None else payload
    return response


class FetchRecentFilingsTests(unittest.TestCase):
    @patch("finance_news.sec_filings.requests.get")
    def test_filters_supported_forms_and_builds_document_url(
        self, mock_get: Mock
    ) -> None:
        mock_get.return_value = successful_response()

        filings = fetch_recent_filings("320193", limit=10)

        self.assertEqual([filing.form for filing in filings], ["8-K", "10-Q", "10-K"])
        self.assertEqual(filings[0].filing_date, "2026-08-01")
        self.assertEqual(filings[0].accession_number, "0000320193-26-000001")
        self.assertEqual(
            filings[0].document_url,
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019326000001/aapl-8k.htm",
        )
        self.assertIn("CIK0000320193.json", mock_get.call_args.args[0])

    @patch("finance_news.sec_filings.requests.get")
    def test_applies_limit_after_filtering(self, mock_get: Mock) -> None:
        mock_get.return_value = successful_response()

        filings = fetch_recent_filings("0000320193", limit=2)

        self.assertEqual([filing.form for filing in filings], ["8-K", "10-Q"])

    @patch("finance_news.sec_filings.requests.get")
    def test_returns_empty_list_when_no_supported_forms(self, mock_get: Mock) -> None:
        payload = {
            "filings": {
                "recent": {
                    "form": ["4"],
                    "filingDate": ["2026-08-01"],
                    "accessionNumber": ["0000320193-26-000001"],
                    "primaryDocument": ["ownership.xml"],
                }
            }
        }
        mock_get.return_value = successful_response(payload)

        self.assertEqual(fetch_recent_filings("320193"), [])

    @patch("finance_news.sec_filings.requests.get")
    def test_rejects_invalid_limit_without_request(self, mock_get: Mock) -> None:
        with self.assertRaisesRegex(FilingLookupError, "Limit must be at least 1"):
            fetch_recent_filings("320193", limit=0)

        mock_get.assert_not_called()

    @patch("finance_news.sec_filings.requests.get")
    def test_rejects_invalid_cik_without_request(self, mock_get: Mock) -> None:
        with self.assertRaisesRegex(FilingLookupError, "CIK must contain"):
            fetch_recent_filings("not-a-cik")

        mock_get.assert_not_called()

    @patch("finance_news.sec_filings.requests.get")
    def test_handles_connection_failure(self, mock_get: Mock) -> None:
        mock_get.side_effect = requests.ConnectionError("network unavailable")

        with self.assertRaisesRegex(FilingLookupError, "Could not connect"):
            fetch_recent_filings("320193")

    @patch("finance_news.sec_filings.requests.get")
    def test_handles_unexpected_response_shape(self, mock_get: Mock) -> None:
        mock_get.return_value = successful_response({"filings": {}})

        with self.assertRaisesRegex(FilingLookupError, "unexpected format"):
            fetch_recent_filings("320193")


if __name__ == "__main__":
    unittest.main()

"""Tests for recent SEC filing metadata retrieval."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from finance_news.sec_filings import (
    Filing,
    FilingLookupError,
    fetch_recent_filings,
    find_latest_annual_filing,
)


RECENT_FILINGS = {
    "filings": {
        "recent": {
            "form": ["8-K", "4", "10-Q", "10-K", "20-F"],
            "filingDate": ["2026-08-01", "2026-07-31", "2026-07-30", "2025-10-31", "2025-03-01"],
            "accessionNumber": [
                "0000320193-26-000001",
                "0000320193-26-000002",
                "0000320193-26-000003",
                "0000320193-25-000004",
                "0000320193-25-000005",
            ],
            "primaryDocument": ["aapl-8k.htm", "ownership.xml", "aapl-10q.htm", "aapl-10k.htm", "foreign-20f.htm"],
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

        self.assertEqual(
            [filing.form for filing in filings], ["8-K", "10-Q", "10-K", "20-F"]
        )
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
    def test_follows_older_submission_file_when_recent_has_too_few_supported_forms(
        self, mock_get: Mock
    ) -> None:
        recent = {
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "filingDate": ["2026-08-01"],
                    "accessionNumber": ["0000320193-26-000001"],
                    "primaryDocument": ["aapl-8k.htm"],
                },
                "files": [{"name": "CIK0000320193-submissions-001.json"}],
            }
        }
        older = {
            "form": ["10-K"],
            "filingDate": ["2025-10-31"],
            "accessionNumber": ["0000320193-25-000004"],
            "primaryDocument": ["aapl-10k.htm"],
        }
        mock_get.side_effect = [successful_response(recent), successful_response(older)]

        filings = fetch_recent_filings("320193", limit=2)

        self.assertEqual([filing.form for filing in filings], ["8-K", "10-K"])
        self.assertIn("submissions-001.json", mock_get.call_args_list[1].args[0])

    @patch("finance_news.sec_filings.requests.get")
    def test_form_filter_can_reach_annual_filing_past_many_recent_8ks(
        self, mock_get: Mock
    ) -> None:
        recent = {
            "filings": {
                "recent": {
                    "form": ["8-K", "8-K"],
                    "filingDate": ["2026-08-01", "2026-07-01"],
                    "accessionNumber": ["1-26-1", "1-26-2"],
                    "primaryDocument": ["one.htm", "two.htm"],
                },
                "files": [{"name": "CIK1-submissions-001.json"}],
            }
        }
        older = {
            "form": ["10-K"],
            "filingDate": ["2026-02-01"],
            "accessionNumber": ["0000000001-26-000003"],
            "primaryDocument": ["annual.htm"],
        }
        mock_get.side_effect = [successful_response(recent), successful_response(older)]

        filings = fetch_recent_filings(
            "1", limit=1, forms=frozenset({"10-K"})
        )

        self.assertEqual([filing.form for filing in filings], ["10-K"])

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


class FindLatestAnnualFilingTests(unittest.TestCase):
    @patch("finance_news.sec_filings.fetch_recent_filings")
    def test_uses_validated_predecessor_from_joint_quarterly_accession(
        self, mock_fetch: Mock
    ) -> None:
        quarterly = Filing(
            "10-Q", "2026-08-03", "0000034088-26-000093", "q.htm", "https://q"
        )
        annual = Filing(
            "10-K", "2026-02-18", "0000034088-26-000010", "k.htm", "https://k"
        )
        mock_fetch.side_effect = [[], [quarterly], [annual]]

        filing, source_cik = find_latest_annual_filing("0002115436")

        self.assertEqual(filing, annual)
        self.assertEqual(source_cik, "0000034088")

    @patch("finance_news.sec_filings.fetch_recent_filings")
    def test_ignores_unvalidated_accession_candidate(self, mock_fetch: Mock) -> None:
        quarterly = Filing(
            "10-Q", "2026-08-03", "0001193125-26-000093", "q.htm", "https://q"
        )
        mock_fetch.side_effect = [[], [quarterly], []]

        filing, source_cik = find_latest_annual_filing("0002115436")

        self.assertIsNone(filing)
        self.assertEqual(source_cik, "0002115436")


if __name__ == "__main__":
    unittest.main()

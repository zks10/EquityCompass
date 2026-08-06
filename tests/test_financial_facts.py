"""Tests for SEC XBRL financial fact retrieval and normalization."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from finance_news.financial_facts import (
    FinancialFactsError,
    extract_latest_annual_facts,
    fetch_company_facts,
    save_financial_facts,
)


def annual_record(value: int, end: str, filed: str, fiscal_year: int) -> dict:
    return {
        "val": value,
        "fy": fiscal_year,
        "fp": "FY",
        "form": "10-K",
        "end": end,
        "filed": filed,
        "accn": f"0000000000-{str(fiscal_year)[-2:]}-000001",
    }


def company_facts_payload() -> dict:
    concepts = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            annual_record(100, "2024-09-28", "2024-11-01", 2024),
            annual_record(120, "2025-09-27", "2025-10-31", 2025),
            {
                **annual_record(35, "2025-12-27", "2026-01-30", 2026),
                "form": "10-Q",
                "fp": "Q1",
            },
        ],
        "NetIncomeLoss": [annual_record(25, "2025-09-27", "2025-10-31", 2025)],
        "Assets": [annual_record(500, "2025-09-27", "2025-10-31", 2025)],
        "Liabilities": [annual_record(300, "2025-09-27", "2025-10-31", 2025)],
        "NetCashProvidedByUsedInOperatingActivities": [
            annual_record(90, "2025-09-27", "2025-10-31", 2025)
        ],
    }
    return {
        "entityName": "Example Corp.",
        "facts": {
            "us-gaap": {
                tag: {"units": {"USD": records}} for tag, records in concepts.items()
            }
        },
    }


class FetchCompanyFactsTests(unittest.TestCase):
    @patch("finance_news.financial_facts.requests.get")
    def test_fetches_company_facts_for_padded_cik(self, mock_get: Mock) -> None:
        mock_get.return_value.json.return_value = company_facts_payload()

        payload = fetch_company_facts("320193")

        self.assertEqual(payload["entityName"], "Example Corp.")
        self.assertIn("CIK0000320193.json", mock_get.call_args.args[0])

    @patch("finance_news.financial_facts.requests.get")
    def test_handles_connection_failure(self, mock_get: Mock) -> None:
        mock_get.side_effect = requests.ConnectionError("network unavailable")

        with self.assertRaisesRegex(FinancialFactsError, "Could not connect"):
            fetch_company_facts("320193")


class ExtractLatestAnnualFactsTests(unittest.TestCase):
    def test_extracts_five_metrics(self) -> None:
        facts = extract_latest_annual_facts(company_facts_payload())

        self.assertEqual(
            [fact.metric for fact in facts],
            ["revenue", "net_income", "assets", "liabilities", "operating_cash_flow"],
        )

    def test_selects_latest_10k_instead_of_quarterly_value(self) -> None:
        facts = extract_latest_annual_facts(company_facts_payload())
        revenue = next(fact for fact in facts if fact.metric == "revenue")

        self.assertEqual(revenue.value, 120)
        self.assertEqual(revenue.period_end, "2025-09-27")
        self.assertEqual(revenue.form, "10-K")

    def test_uses_revenue_fallback_tag(self) -> None:
        payload = company_facts_payload()
        facts = payload["facts"]["us-gaap"]
        records = facts.pop("RevenueFromContractWithCustomerExcludingAssessedTax")
        facts["Revenues"] = records

        extracted = extract_latest_annual_facts(payload)
        revenue = next(fact for fact in extracted if fact.metric == "revenue")

        self.assertEqual(revenue.tag, "Revenues")

    def test_reports_missing_metric(self) -> None:
        payload = company_facts_payload()
        del payload["facts"]["us-gaap"]["Liabilities"]

        with self.assertRaisesRegex(FinancialFactsError, "Total liabilities"):
            extract_latest_annual_facts(payload)

    def test_reports_non_us_gaap_payload(self) -> None:
        with self.assertRaisesRegex(FinancialFactsError, "US-GAAP"):
            extract_latest_annual_facts({"facts": {}})


class SaveFinancialFactsTests(unittest.TestCase):
    def test_saves_raw_and_normalized_json(self) -> None:
        payload = company_facts_payload()
        facts = extract_latest_annual_facts(payload)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_path, processed_path = save_financial_facts(
                payload,
                facts,
                "exam",
                "1234",
                raw_root=root / "raw",
                processed_root=root / "processed",
            )

            self.assertTrue(raw_path.is_file())
            normalized = json.loads(processed_path.read_text(encoding="utf-8"))
            self.assertEqual(normalized["ticker"], "EXAM")
            self.assertEqual(normalized["cik"], "0000001234")
            self.assertEqual(len(normalized["facts"]), 5)
            self.assertFalse(any(root.rglob("*.part")))


if __name__ == "__main__":
    unittest.main()

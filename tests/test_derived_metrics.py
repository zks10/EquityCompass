"""Tests for deterministic metrics calculated from annual history."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_news.derived_metrics import (
    DerivedMetricsError,
    calculate_derived_metrics,
    calculate_metrics_file,
)


def fact(value: int, fiscal_year: int, period_end: str) -> dict:
    return {"value": value, "fiscal_year": fiscal_year, "period_end": period_end}


def history_payload() -> dict:
    return {
        "ticker": "EXAM",
        "cik": "0000001234",
        "entity_name": "Example Corp.",
        "metrics": {
            "revenue": [
                fact(120, 2025, "2025-12-31"),
                fact(100, 2024, "2024-12-31"),
            ],
            "net_income": [
                fact(24, 2025, "2025-12-31"),
                fact(10, 2024, "2024-12-31"),
            ],
            "assets": [
                fact(300, 2025, "2025-12-31"),
                fact(250, 2024, "2024-12-31"),
            ],
            "liabilities": [
                fact(150, 2025, "2025-12-31"),
                fact(100, 2024, "2024-12-31"),
            ],
            "operating_cash_flow": [
                fact(30, 2025, "2025-12-31"),
                fact(20, 2024, "2024-12-31"),
            ],
        },
    }


class CalculateDerivedMetricsTests(unittest.TestCase):
    def test_calculates_growth_and_ratios(self) -> None:
        result = calculate_derived_metrics(history_payload())
        latest = result["periods"][0]

        self.assertEqual(latest["revenue_growth_percent"], 20.0)
        self.assertEqual(latest["net_profit_margin_percent"], 20.0)
        self.assertEqual(latest["liabilities_to_assets_percent"], 50.0)
        self.assertEqual(latest["operating_cash_flow_margin_percent"], 25.0)

    def test_returns_newest_period_first(self) -> None:
        result = calculate_derived_metrics(history_payload())

        self.assertEqual(
            [period["fiscal_year"] for period in result["periods"]], [2025, 2024]
        )
        self.assertIsNone(result["periods"][1]["revenue_growth_percent"])

    def test_aligns_metrics_by_period_end(self) -> None:
        payload = history_payload()
        payload["metrics"]["net_income"].reverse()

        result = calculate_derived_metrics(payload)

        self.assertEqual(result["periods"][0]["net_profit_margin_percent"], 20.0)

    def test_zero_denominators_return_null(self) -> None:
        payload = history_payload()
        payload["metrics"]["revenue"][1]["value"] = 0
        payload["metrics"]["assets"][1]["value"] = 0

        result = calculate_derived_metrics(payload)
        oldest = result["periods"][1]
        newest = result["periods"][0]

        self.assertIsNone(oldest["net_profit_margin_percent"])
        self.assertIsNone(oldest["liabilities_to_assets_percent"])
        self.assertIsNone(newest["revenue_growth_percent"])

    def test_reports_missing_metric(self) -> None:
        payload = history_payload()
        del payload["metrics"]["assets"]

        with self.assertRaisesRegex(DerivedMetricsError, "assets"):
            calculate_derived_metrics(payload)

    def test_reports_duplicate_period(self) -> None:
        payload = history_payload()
        payload["metrics"]["revenue"].append(
            fact(999, 2025, "2025-12-31")
        )

        with self.assertRaisesRegex(DerivedMetricsError, "duplicate period"):
            calculate_derived_metrics(payload)


class CalculateMetricsFileTests(unittest.TestCase):
    def test_reads_history_and_writes_derived_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "financial_history.json"
            source.write_text(json.dumps(history_payload()), encoding="utf-8")

            destination = calculate_metrics_file(source)

            saved = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(destination.name, "derived_metrics.json")
            self.assertEqual(saved["ticker"], "EXAM")
            self.assertEqual(len(saved["periods"]), 2)
            self.assertFalse(any(root.rglob("*.part")))

    def test_reports_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "financial_history.json"
            source.write_text("not json", encoding="utf-8")

            with self.assertRaisesRegex(DerivedMetricsError, "invalid JSON"):
                calculate_metrics_file(source)


if __name__ == "__main__":
    unittest.main()

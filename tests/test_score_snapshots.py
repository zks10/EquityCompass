"""Tests for traceable Phase 2 score snapshots."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from finance_news.score_snapshots import (
    ScoreSnapshotError,
    build_company_historical_snapshots,
    save_historical_score_snapshot,
    save_score_snapshot,
)


def source_fact(metric: str, value: int) -> dict:
    return {
        "metric": metric,
        "value": value,
        "period_end": "2025-12-31",
        "filed": "2026-02-20",
        "accession_number": "0000001234-26-000001",
        "form": "10-K",
    }


class SaveScoreSnapshotTests(unittest.TestCase):
    def test_saves_latest_score_with_filing_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history_path = root / "financial_history.json"
            history_path.write_text(
                json.dumps(
                    {
                        "ticker": "EXAM",
                        "cik": "0000001234",
                        "entity_name": "Example Corp.",
                        "metrics": {
                            metric: [source_fact(metric, value)]
                            for metric, value in (
                                ("revenue", 120),
                                ("net_income", 24),
                                ("assets", 300),
                                ("liabilities", 150),
                                ("operating_cash_flow", 30),
                            )
                        },
                    }
                ),
                encoding="utf-8",
            )
            metrics_path = root / "derived_metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "periods": [
                            {
                                "fiscal_year": 2025,
                                "period_end": "2025-12-31",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            score = SimpleNamespace(
                score=80,
                label="Mostly favorable current signals",
                available_components=4,
                components=(
                    SimpleNamespace(
                        name="Revenue growth", score=100, source_value=10.0
                    ),
                ),
            )

            destination = save_score_snapshot(
                history_path, metrics_path, score, output_root=root / "snapshots"
            )
            saved = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(saved["filing"]["filed"], "2026-02-20")
        self.assertEqual(
            saved["filing"]["accession_number"], "0000001234-26-000001"
        )
        self.assertEqual(saved["score"]["value"], 80)
        self.assertTrue(saved["score"]["eligible_for_main_comparison"])
        self.assertEqual(saved["source_facts"]["revenue"]["value"], 120)
        self.assertFalse(any(root.rglob("*.part")))

    def test_rejects_mixed_filing_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = {
                metric: [source_fact(metric, 1)]
                for metric in (
                    "revenue",
                    "net_income",
                    "assets",
                    "liabilities",
                    "operating_cash_flow",
                )
            }
            facts["assets"][0]["filed"] = "2026-02-21"
            history_path = root / "history.json"
            history_path.write_text(
                json.dumps({"metrics": facts}), encoding="utf-8"
            )
            metrics_path = root / "metrics.json"
            metrics_path.write_text(
                json.dumps(
                    {
                        "periods": [
                            {"fiscal_year": 2025, "period_end": "2025-12-31"}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ScoreSnapshotError, "filing date"):
                save_score_snapshot(
                    history_path,
                    metrics_path,
                    SimpleNamespace(available_components=0, components=()),
                    output_root=root / "snapshots",
                )

    def test_reconstructs_score_from_one_historical_accession(self) -> None:
        accession = "0000001234-24-000001"

        def records(current: int, previous: int) -> list[dict]:
            return [
                {
                    "val": previous,
                    "fy": 2024,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": "2025-02-20",
                    "accn": accession,
                    "end": "2023-12-31",
                },
                {
                    "val": current,
                    "fy": 2024,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": "2025-02-20",
                    "accn": accession,
                    "end": "2024-12-31",
                },
            ]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            companyfacts = root / "companyfacts.json"
            companyfacts.write_text(
                json.dumps(
                    {
                        "cik": 1234,
                        "entityName": "Example Corp.",
                        "facts": {
                            "us-gaap": {
                                "Revenues": {"units": {"USD": records(120, 100)}},
                                "NetIncomeLoss": {"units": {"USD": records(24, 10)}},
                                "Assets": {"units": {"USD": records(300, 250)}},
                                "Liabilities": {"units": {"USD": records(150, 100)}},
                                "NetCashProvidedByUsedInOperatingActivities": {
                                    "units": {"USD": records(30, 20)}
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            destination = save_historical_score_snapshot(
                companyfacts,
                accession,
                "EXAM",
                output_root=root / "snapshots",
            )
            saved = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(saved["period_end"], "2024-12-31")
        self.assertEqual(saved["ticker"], "EXAM")
        self.assertEqual(saved["filing"]["filed"], "2025-02-20")
        self.assertEqual(saved["score"]["value"], 91)
        self.assertEqual(saved["prior_revenue_fact"]["value"], 100)

    def test_batch_manifest_records_successes_and_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            companyfacts = root / "companyfacts.json"
            companyfacts.write_text(
                json.dumps(
                    {
                        "cik": 1234,
                        "facts": {
                            "us-gaap": {
                                "Revenues": {
                                    "units": {
                                        "USD": [
                                            {
                                                "form": "10-K",
                                                "filed": "2025-02-20",
                                                "accn": "new",
                                            },
                                            {
                                                "form": "10-K",
                                                "filed": "2024-02-20",
                                                "accn": "old",
                                            },
                                        ]
                                    }
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "finance_news.score_snapshots.save_historical_score_snapshot",
                side_effect=[root / "new.json", ScoreSnapshotError("missing facts")],
            ):
                manifest_path = build_company_historical_snapshots(
                    companyfacts, "EXAM", years=2, output_root=root / "snapshots"
                )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["attempted"], 2)
        self.assertEqual(manifest["completed"], 1)
        self.assertEqual(manifest["incomplete"], 1)
        self.assertEqual(manifest["filings"][1]["reason"], "missing facts")


if __name__ == "__main__":
    unittest.main()

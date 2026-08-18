"""Tests for expanded SEC universe construction helpers."""

from __future__ import annotations

import unittest

from finance_news.expanded_universe import parse_master_index, sic_division


class ExpandedUniverseTests(unittest.TestCase):
    def test_parses_only_original_10k_rows(self) -> None:
        text = "\n".join(
            [
                "CIK|Company Name|Form Type|Date Filed|Filename",
                "320193|Apple Inc.|10-K|2024-11-01|edgar/data/320193/0000320193-24-000123.txt",
                "320193|Apple Inc.|10-K/A|2024-11-02|edgar/data/320193/amended.txt",
                "320193|Apple Inc.|10-Q|2024-08-01|edgar/data/320193/quarterly.txt",
            ]
        )

        rows = parse_master_index(text)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cik"], "0000320193")
        self.assertEqual(rows[0]["accession_number"], "0000320193-24-000123")

    def test_maps_sic_to_registered_divisions(self) -> None:
        self.assertEqual(sic_division(3571), "Manufacturing")
        self.assertEqual(sic_division(6021), "Finance")
        self.assertEqual(sic_division(7372), "Services")


if __name__ == "__main__":
    unittest.main()

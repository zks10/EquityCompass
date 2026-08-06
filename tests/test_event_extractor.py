"""Tests for SEC 8-K event-item extraction and storage."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finance_news.event_extractor import (
    EventExtractionError,
    extract_8k_items,
    save_event_items,
    save_event_manifest,
)


SAMPLE_8K = """Table of Contents
Item 2.02 Results of Operations and Financial Condition
2
Item 9.01 Financial Statements and Exhibits
3
Item 2.02 Results of Operations and Financial Condition
On August 1, Example Corp. announced its quarterly financial results.
The earnings release is furnished as an exhibit to this report.
Item 7.01 Regulation FD Disclosure
The company provided an investor presentation with additional information.
Item 9.01 Financial Statements and Exhibits
(d) Exhibits. Exhibit 99.1 contains the earnings release.
"""


class Extract8KItemsTests(unittest.TestCase):
    def test_extracts_and_deduplicates_event_items(self) -> None:
        items = extract_8k_items(SAMPLE_8K)

        self.assertEqual(
            [item.item_number for item in items], ["2.02", "7.01", "9.01"]
        )
        self.assertIn("quarterly financial results", items[0].text)
        self.assertEqual(items[0].title, "Results of Operations and Financial Condition")

    def test_uses_largest_body_instead_of_table_of_contents(self) -> None:
        item = extract_8k_items(SAMPLE_8K)[0]

        self.assertNotEqual(item.text.strip().splitlines()[-1], "2")

    def test_reports_missing_items(self) -> None:
        with self.assertRaisesRegex(EventExtractionError, "No Item"):
            extract_8k_items("FORM 8-K\nNo parseable headings here.")

    def test_saves_item_files_and_manifest(self) -> None:
        items = extract_8k_items(SAMPLE_8K)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            paths = save_event_items(root / "filing.txt", items)
            manifest = save_event_manifest(
                "0000001234",
                "EXAM",
                "Example Corp.",
                [{"items": [{"item_number": "2.02"}]}],
                output_root=root,
            )

            self.assertEqual(paths[0].name, "item_2_02.txt")
            self.assertTrue(all(path.is_file() for path in paths))
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(saved["ticker"], "EXAM")
            self.assertFalse(any(root.rglob("*.part")))


if __name__ == "__main__":
    unittest.main()

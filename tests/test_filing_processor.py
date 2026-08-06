"""Tests for SEC filing HTML-to-text processing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance_news.filing_processor import (
    FilingProcessingError,
    clean_filing_html,
    default_output_path,
    process_filing,
)


SAMPLE_HTML = b"""
<html>
  <head><style>.hidden { display: none; }</style><script>bad()</script></head>
  <body>
    <!-- metadata that should not appear -->
    <ix:header><ix:hidden>Hidden XBRL metadata</ix:hidden></ix:header>
    <h1>Apple Inc. Annual Report</h1>
    <p>Revenue was <ix:nonfraction>$100 billion</ix:nonfraction>.</p>
    <div style="display: none">Invisible text</div>
    <div aria-hidden="true">Also invisible</div>
    <table><tr><th>Year</th><th>Revenue</th></tr><tr><td>2025</td><td>$100</td></tr></table>
  </body>
</html>
"""


class CleanFilingHtmlTests(unittest.TestCase):
    def test_keeps_readable_content_and_inline_xbrl_values(self) -> None:
        text = clean_filing_html(SAMPLE_HTML)

        self.assertIn("Apple Inc. Annual Report", text)
        self.assertIn("Revenue was", text)
        self.assertIn("$100 billion", text)
        self.assertIn("Year", text)
        self.assertIn("2025", text)

    def test_removes_scripts_styles_comments_and_hidden_content(self) -> None:
        text = clean_filing_html(SAMPLE_HTML)

        self.assertNotIn("bad()", text)
        self.assertNotIn("display: none", text)
        self.assertNotIn("metadata that should not appear", text)
        self.assertNotIn("Hidden XBRL metadata", text)
        self.assertNotIn("Invisible text", text)
        self.assertNotIn("Also invisible", text)

    def test_rejects_empty_document(self) -> None:
        with self.assertRaisesRegex(FilingProcessingError, "empty"):
            clean_filing_html(b"")

    def test_rejects_document_without_readable_text(self) -> None:
        with self.assertRaisesRegex(FilingProcessingError, "No readable text"):
            clean_filing_html("<html><script>onlyCode()</script></html>")


class ProcessFilingTests(unittest.TestCase):
    def test_maps_raw_path_to_processed_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_root = root / "data/raw/sec"
            input_path = raw_root / "0000320193/0001/aapl.htm"

            output_path = default_output_path(
                input_path,
                raw_root=raw_root,
                processed_root=root / "data/processed/sec",
            )

            self.assertEqual(
                output_path,
                root / "data/processed/sec/0000320193/0001/filing.txt",
            )

    def test_processes_file_to_explicit_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "sample.htm"
            destination = root / "output/filing.txt"
            source.write_bytes(SAMPLE_HTML)

            result = process_filing(source, destination)

            self.assertEqual(result, destination)
            self.assertIn("Apple Inc. Annual Report", result.read_text())
            self.assertFalse(destination.with_suffix(".txt.part").exists())

    def test_reports_missing_input_file(self) -> None:
        with self.assertRaisesRegex(FilingProcessingError, "not found"):
            process_filing(Path("missing-filing.htm"), Path("output.txt"))


if __name__ == "__main__":
    unittest.main()

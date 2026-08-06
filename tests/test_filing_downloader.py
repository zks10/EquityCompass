"""Tests for raw SEC filing downloads."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from finance_news.filing_downloader import FilingDownloadError, download_filing
from finance_news.sec_filings import Filing


FILING = Filing(
    form="10-K",
    filing_date="2025-10-31",
    accession_number="0000320193-25-000079",
    primary_document="aapl-20250927.htm",
    document_url=(
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019325000079/aapl-20250927.htm"
    ),
)


class DownloadFilingTests(unittest.TestCase):
    @patch("finance_news.filing_downloader.requests.get")
    def test_downloads_to_predictable_path(self, mock_get: Mock) -> None:
        mock_get.return_value.content = b"<html>Apple filing</html>"

        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = download_filing(
                FILING, "320193", output_root=Path(temporary_directory)
            )

            self.assertEqual(
                destination.relative_to(temporary_directory),
                Path(
                    "0000320193/000032019325000079/aapl-20250927.htm"
                ),
            )
            self.assertEqual(destination.read_bytes(), b"<html>Apple filing</html>")
            self.assertFalse(destination.with_suffix(".htm.part").exists())

    @patch("finance_news.filing_downloader.requests.get")
    def test_reuses_existing_nonempty_file(self, mock_get: Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = (
                Path(temporary_directory)
                / "0000320193/000032019325000079/aapl-20250927.htm"
            )
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"existing filing")

            result = download_filing(
                FILING, "320193", output_root=Path(temporary_directory)
            )

            self.assertEqual(result, destination)
            self.assertEqual(result.read_bytes(), b"existing filing")
            mock_get.assert_not_called()

    @patch("finance_news.filing_downloader.requests.get")
    def test_force_overwrites_existing_file(self, mock_get: Mock) -> None:
        mock_get.return_value.content = b"new filing"

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            destination = (
                root / "0000320193/000032019325000079/aapl-20250927.htm"
            )
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"old filing")

            download_filing(FILING, "320193", output_root=root, overwrite=True)

            self.assertEqual(destination.read_bytes(), b"new filing")

    @patch("finance_news.filing_downloader.requests.get")
    def test_rejects_unsafe_filename_without_request(self, mock_get: Mock) -> None:
        unsafe_filing = Filing(
            form=FILING.form,
            filing_date=FILING.filing_date,
            accession_number=FILING.accession_number,
            primary_document="../outside.htm",
            document_url=FILING.document_url,
        )

        with self.assertRaisesRegex(FilingDownloadError, "unsafe"):
            download_filing(unsafe_filing, "320193")

        mock_get.assert_not_called()

    @patch("finance_news.filing_downloader.requests.get")
    def test_handles_connection_failure(self, mock_get: Mock) -> None:
        mock_get.side_effect = requests.ConnectionError("network unavailable")

        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(FilingDownloadError, "Could not download"):
                download_filing(
                    FILING, "320193", output_root=Path(temporary_directory)
                )

    @patch("finance_news.filing_downloader.requests.get")
    def test_rejects_empty_document(self, mock_get: Mock) -> None:
        mock_get.return_value.content = b""

        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(FilingDownloadError, "empty"):
                download_filing(
                    FILING, "320193", output_root=Path(temporary_directory)
                )


if __name__ == "__main__":
    unittest.main()

"""Download raw SEC filing documents to local storage."""

from __future__ import annotations

import os
from pathlib import Path

import requests

from finance_news.sec_companies import DEFAULT_USER_AGENT
from finance_news.sec_filings import Filing


DEFAULT_OUTPUT_ROOT = Path("data/raw/sec")


class FilingDownloadError(Exception):
    """Raised when a raw SEC filing cannot be saved."""


def download_filing(
    filing: Filing,
    cik: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    overwrite: bool = False,
) -> Path:
    """Download ``filing`` and return the path to the saved raw document."""
    normalized_cik = str(cik).strip().zfill(10)
    if not normalized_cik.isdigit() or len(normalized_cik) != 10:
        raise FilingDownloadError("CIK must contain between 1 and 10 digits.")

    filename = Path(filing.primary_document).name
    if not filename or filename != filing.primary_document:
        raise FilingDownloadError("The filing has an unsafe primary document name.")

    accession_path = filing.accession_number.replace("-", "")
    if not accession_path.isdigit():
        raise FilingDownloadError("The filing has an invalid accession number.")

    destination = Path(output_root) / normalized_cik / accession_path / filename
    if destination.is_file() and destination.stat().st_size > 0 and not overwrite:
        return destination

    try:
        response = requests.get(
            filing.document_url,
            headers={
                "User-Agent": os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT),
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise FilingDownloadError(
            f"SEC download failed with HTTP status {status}."
        ) from exc
    except requests.RequestException as exc:
        raise FilingDownloadError(f"Could not download the SEC filing: {exc}") from exc

    if not response.content:
        raise FilingDownloadError("The SEC returned an empty filing document.")

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_suffix(f"{destination.suffix}.part")
        temporary_path.write_bytes(response.content)
        temporary_path.replace(destination)
    except OSError as exc:
        raise FilingDownloadError(f"Could not save the filing: {exc}") from exc

    return destination

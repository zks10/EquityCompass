"""Retrieve recent filing metadata from the SEC submissions API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from itertools import islice

import requests

from finance_news.sec_companies import DEFAULT_USER_AGENT


SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_SUBMISSIONS_FILE_URL = "https://data.sec.gov/submissions/{name}"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
SUPPORTED_FORMS = frozenset({"10-K", "10-Q", "8-K", "20-F"})


class FilingLookupError(Exception):
    """Raised when recent SEC filing metadata cannot be retrieved."""


@dataclass(frozen=True)
class Filing:
    form: str
    filing_date: str
    accession_number: str
    primary_document: str
    document_url: str


def find_latest_annual_filing(cik: str) -> tuple[Filing | None, str]:
    """Find an annual filing, following a validated joint-filer predecessor."""
    normalized_cik = str(cik).strip().zfill(10)
    annual = fetch_recent_filings(
        normalized_cik, limit=1, forms=frozenset({"10-K", "20-F"})
    )
    if annual:
        return annual[0], normalized_cik

    # Successor registrants can list a joint 10-Q whose accession belongs to the
    # predecessor. Accept that CIK only when its own SEC record confirms an
    # annual filing, which filters out unrelated filing-agent accessions.
    quarterlies = fetch_recent_filings(
        normalized_cik, limit=10, forms=frozenset({"10-Q"})
    )
    for quarterly in quarterlies:
        candidate = quarterly.accession_number.split("-", 1)[0].zfill(10)
        if candidate == normalized_cik or not candidate.isdigit():
            continue
        predecessor_annual = fetch_recent_filings(
            candidate, limit=1, forms=frozenset({"10-K", "20-F"})
        )
        if predecessor_annual:
            return predecessor_annual[0], candidate
    return None, normalized_cik


def fetch_recent_filings(
    cik: str, limit: int = 10, forms: frozenset[str] | None = None
) -> list[Filing]:
    """Return recent domestic filings plus 20-Fs used for scope detection."""
    normalized_cik = str(cik).strip().zfill(10)
    if not normalized_cik.isdigit() or len(normalized_cik) != 10:
        raise FilingLookupError("CIK must contain between 1 and 10 digits.")
    if limit < 1:
        raise FilingLookupError("Limit must be at least 1.")

    try:
        response = requests.get(
            SEC_SUBMISSIONS_URL.format(cik=normalized_cik),
            headers={
                "User-Agent": os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT),
                "Accept": "application/json",
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise FilingLookupError(
            f"SEC request failed with HTTP status {status}."
        ) from exc
    except requests.JSONDecodeError as exc:
        raise FilingLookupError("The SEC returned an unreadable response.") from exc
    except requests.RequestException as exc:
        raise FilingLookupError(f"Could not connect to the SEC: {exc}") from exc

    try:
        requested_forms = forms or SUPPORTED_FORMS
        filing_payloads = [payload["filings"]["recent"]]
        older_files = payload["filings"].get("files", ())
        for older_file in older_files:
            if len(_supported_rows(filing_payloads, requested_forms)) >= limit:
                break
            name = older_file["name"]
            older_response = requests.get(
                SEC_SUBMISSIONS_FILE_URL.format(name=name),
                headers={
                    "User-Agent": os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT),
                    "Accept": "application/json",
                },
                timeout=15,
            )
            older_response.raise_for_status()
            filing_payloads.append(older_response.json())

        matching_rows = iter(_supported_rows(filing_payloads, requested_forms))
        filings = []
        for form, filing_date, accession_number, primary_document in islice(
            matching_rows, limit
        ):
            accession_path = accession_number.replace("-", "")
            document_url = (
                f"{SEC_ARCHIVES_URL}/{int(normalized_cik)}/"
                f"{accession_path}/{primary_document}"
            )
            filings.append(
                Filing(
                    form=form,
                    filing_date=filing_date,
                    accession_number=accession_number,
                    primary_document=primary_document,
                    document_url=document_url,
                )
            )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise FilingLookupError(
            f"SEC request failed with HTTP status {status}."
        ) from exc
    except requests.JSONDecodeError as exc:
        raise FilingLookupError("The SEC returned an unreadable response.") from exc
    except requests.RequestException as exc:
        raise FilingLookupError(f"Could not connect to the SEC: {exc}") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise FilingLookupError("The SEC response has an unexpected format.") from exc

    return filings


def _supported_rows(
    filing_payloads: list[dict], requested_forms: frozenset[str]
) -> list[tuple[str, str, str, str]]:
    """Return supported filing rows from recent and archived submission payloads."""
    matching_rows = []
    for recent in filing_payloads:
        rows = zip(
            recent["form"],
            recent["filingDate"],
            recent["accessionNumber"],
            recent["primaryDocument"],
            strict=True,
        )
        matching_rows.extend(row for row in rows if row[0] in requested_forms)
    return matching_rows

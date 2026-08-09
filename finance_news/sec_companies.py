"""Resolve ticker symbols with the SEC company tickers dataset."""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DEFAULT_USER_AGENT = "finance-news-phase1/0.1 contact@example.com"


class CompanyLookupError(Exception):
    """Base error for SEC company lookup failures."""


class TickerNotFoundError(CompanyLookupError):
    """Raised when a ticker is absent from the SEC dataset."""


@dataclass(frozen=True)
class Company:
    ticker: str
    name: str
    cik: str


def resolve_ticker(ticker: str) -> Company:
    """Return the SEC company record matching ``ticker``."""
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise CompanyLookupError("Ticker cannot be empty.")

    try:
        response = requests.get(
            SEC_TICKERS_URL,
            headers={
                "User-Agent": os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT),
                "Accept": "application/json",
            },
            timeout=15,
        )
        response.raise_for_status()
        records = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CompanyLookupError(f"SEC request failed with HTTP status {status}.") from exc
    except requests.JSONDecodeError as exc:
        raise CompanyLookupError("The SEC returned an unreadable response.") from exc
    except requests.RequestException as exc:
        raise CompanyLookupError(f"Could not connect to the SEC: {exc}") from exc

    for record in records.values():
        if str(record.get("ticker", "")).upper() == normalized_ticker:
            return Company(
                ticker=normalized_ticker,
                name=str(record["title"]),
                cik=str(record["cik_str"]).zfill(10),
            )

    raise TickerNotFoundError(
        f"Ticker '{normalized_ticker}' was not found in the SEC company dataset."
    )


def resolve_company_query(query: str) -> Company:
    """Resolve either a ticker or a company name against the SEC company list."""
    normalized_query = " ".join(query.strip().split())
    if not normalized_query:
        raise CompanyLookupError("Enter a company name or ticker.")

    # Keep the established ticker path fast and compatible with existing callers.
    if " " not in normalized_query and len(normalized_query) <= 6:
        try:
            return resolve_ticker(normalized_query)
        except TickerNotFoundError:
            pass

    try:
        response = requests.get(
            SEC_TICKERS_URL,
            headers={
                "User-Agent": os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT),
                "Accept": "application/json",
            },
            timeout=15,
        )
        response.raise_for_status()
        records = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise CompanyLookupError(f"SEC request failed with HTTP status {status}.") from exc
    except requests.JSONDecodeError as exc:
        raise CompanyLookupError("The SEC returned an unreadable response.") from exc
    except requests.RequestException as exc:
        raise CompanyLookupError(f"Could not connect to the SEC: {exc}") from exc

    needle = normalized_query.casefold()
    candidates = []
    for record in records.values():
        name = str(record.get("title", ""))
        folded_name = name.casefold()
        if needle == folded_name:
            rank = 0
        elif folded_name.startswith(needle):
            rank = 1
        elif needle in folded_name:
            rank = 2
        else:
            continue
        candidates.append((rank, len(name), name, record))

    if not candidates:
        raise TickerNotFoundError(
            f"Company or ticker '{normalized_query}' was not found in the SEC company dataset."
        )

    _, _, name, record = min(candidates, key=lambda item: item[:3])
    return Company(
        ticker=str(record["ticker"]).upper(),
        name=name,
        cik=str(record["cik_str"]).zfill(10),
    )

"""Command-line interface for downloading a raw SEC filing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from finance_news.filing_downloader import FilingDownloadError, download_filing
from finance_news.sec_companies import CompanyLookupError, resolve_ticker
from finance_news.sec_filings import (
    FilingLookupError,
    SUPPORTED_FORMS,
    fetch_recent_filings,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download the newest raw SEC filing of a selected type."
    )
    parser.add_argument("ticker", help="Ticker symbol, for example AAPL")
    parser.add_argument(
        "--form",
        choices=sorted(SUPPORTED_FORMS),
        default="10-K",
        help="Filing type to download (default: 10-K)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw/sec"),
        help="Root directory for downloaded filings (default: data/raw/sec)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Download again if the filing already exists locally",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        company = resolve_ticker(args.ticker)
        filings = fetch_recent_filings(company.cik, limit=100)
        filing = next((item for item in filings if item.form == args.form), None)
        if filing is None:
            raise FilingLookupError(
                f"No recent {args.form} filing was found for {company.ticker}."
            )
        destination = download_filing(
            filing,
            company.cik,
            output_root=args.output,
            overwrite=args.force,
        )
    except (CompanyLookupError, FilingLookupError, FilingDownloadError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Company: {company.name} ({company.ticker})")
    print(f"Filing: {filing.form} filed {filing.filing_date}")
    print(f"Saved: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

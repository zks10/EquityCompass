"""Command-line interface for recent SEC filing metadata."""

from __future__ import annotations

import argparse
import sys

from finance_news.sec_companies import CompanyLookupError, resolve_ticker
from finance_news.sec_filings import FilingLookupError, fetch_recent_filings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List recent 10-K, 10-Q, and 8-K filings for a stock ticker."
    )
    parser.add_argument("ticker", help="Ticker symbol, for example AAPL")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of matching filings to show (default: 10)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        company = resolve_ticker(args.ticker)
        filings = fetch_recent_filings(company.cik, limit=args.limit)
    except (CompanyLookupError, FilingLookupError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"{company.name} ({company.ticker})")
    print(f"SEC CIK: {company.cik}")

    if not filings:
        print("No recent 10-K, 10-Q, or 8-K filings were found.")
        return 0

    for filing in filings:
        print()
        print(f"{filing.form} | Filed: {filing.filing_date}")
        print(f"Accession: {filing.accession_number}")
        print(f"Document: {filing.document_url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

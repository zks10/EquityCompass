"""Command-line interface for company resolution."""

from __future__ import annotations

import argparse
import sys

from finance_news.sec_companies import CompanyLookupError, resolve_ticker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve a stock ticker using the SEC company tickers dataset."
    )
    parser.add_argument("ticker", help="Ticker symbol, for example AAPL")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        company = resolve_ticker(args.ticker)
    except CompanyLookupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Ticker: {company.ticker}")
    print(f"Company: {company.name}")
    print(f"SEC CIK: {company.cik}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

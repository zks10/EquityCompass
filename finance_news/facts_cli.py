"""Command-line interface for SEC annual financial facts."""

from __future__ import annotations

import argparse
import sys

from finance_news.financial_facts import (
    FinancialFactsError,
    extract_latest_annual_facts,
    fetch_company_facts,
    save_financial_facts,
)
from finance_news.sec_companies import CompanyLookupError, resolve_ticker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retrieve latest annual US-GAAP facts from the SEC."
    )
    parser.add_argument("ticker", help="Ticker symbol, for example AAPL")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        company = resolve_ticker(args.ticker)
        payload = fetch_company_facts(company.cik)
        facts = extract_latest_annual_facts(payload)
        raw_path, processed_path = save_financial_facts(
            payload, facts, company.ticker, company.cik
        )
    except (CompanyLookupError, FinancialFactsError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"{company.name} ({company.ticker})")
    print(f"SEC CIK: {company.cik}")
    for fact in facts:
        print(
            f"{fact.label}: ${fact.value:,.0f} "
            f"(period ended {fact.period_end}, {fact.form})"
        )
    print(f"Raw SEC data: {raw_path}")
    print(f"Normalized data: {processed_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

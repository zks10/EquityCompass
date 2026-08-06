"""Command-line interface for multi-year SEC annual financial facts."""

from __future__ import annotations

import argparse
import sys

from finance_news.financial_facts import (
    FinancialFactsError,
    extract_annual_history,
    fetch_company_facts,
    save_financial_history,
)
from finance_news.sec_companies import CompanyLookupError, resolve_ticker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retrieve several years of annual US-GAAP facts from the SEC."
    )
    parser.add_argument("ticker", help="Ticker symbol, for example AAPL")
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="Number of annual periods per metric, from 1 to 20 (default: 5)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        company = resolve_ticker(args.ticker)
        payload = fetch_company_facts(company.cik)
        history = extract_annual_history(payload, years=args.years)
        raw_path, processed_path = save_financial_history(
            payload,
            history,
            company.ticker,
            company.cik,
            requested_years=args.years,
        )
    except (CompanyLookupError, FinancialFactsError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"{company.name} ({company.ticker})")
    print(f"SEC CIK: {company.cik}")
    for facts in history.values():
        print()
        print(facts[0].label)
        for fact in facts:
            print(
                f"  FY {fact.fiscal_year} | {fact.period_end} | "
                f"${fact.value:,.0f}"
            )
    print(f"Raw SEC data: {raw_path}")
    print(f"Normalized history: {processed_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

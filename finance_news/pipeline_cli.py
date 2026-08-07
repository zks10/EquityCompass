"""Command-line interface for the complete Equity Compass Phase 1 pipeline."""

from __future__ import annotations

import argparse
import sys

from finance_news.pipeline import PipelineError, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the complete Equity Compass Phase 1 SEC pipeline."
    )
    parser.add_argument("ticker", help="Ticker symbol, for example AAPL")
    parser.add_argument(
        "--years",
        type=int,
        default=5,
        help="Number of annual history periods, from 1 to 20 (default: 5)",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Download the latest 10-K again even if it already exists",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_pipeline(
            args.ticker,
            years=args.years,
            force_download=args.force_download,
            progress=lambda message: print(f"[{message}]"),
        )
    except PipelineError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"Pipeline complete: {result.company.name} ({result.company.ticker})")
    print(f"10-K filed: {result.filing.filing_date}")
    print(f"Raw filing: {result.raw_filing_path}")
    print(f"Clean filing: {result.processed_filing_path}")
    print(f"Sections: {result.section_paths[0].parent}")
    print(f"Latest facts: {result.latest_facts_path}")
    print(f"History: {result.history_path}")
    print(f"Derived metrics: {result.derived_metrics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

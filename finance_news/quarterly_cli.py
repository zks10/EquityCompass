"""Command-line interface for latest 10-Q collection."""

from __future__ import annotations

import argparse
import sys

from finance_news.pipeline import PipelineError
from finance_news.quarterly_pipeline import run_quarterly_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect and process a company's latest SEC 10-Q."
    )
    parser.add_argument("ticker", help="Ticker symbol, for example AAPL")
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Download the latest 10-Q again even if it already exists",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_quarterly_pipeline(
            args.ticker,
            force_download=args.force_download,
            progress=lambda message: print(f"[{message}]"),
        )
    except PipelineError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"Quarterly collection complete: {result.company.name}")
    print(f"10-Q filed: {result.filing.filing_date}")
    print(f"Raw filing: {result.raw_filing_path}")
    print(f"Clean filing: {result.processed_filing_path}")
    print(f"Sections: {result.section_paths[0].parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

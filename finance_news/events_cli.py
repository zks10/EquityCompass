"""Command-line interface for recent SEC 8-K event collection."""

from __future__ import annotations

import argparse
import sys

from finance_news.events_pipeline import run_events_pipeline
from finance_news.pipeline import PipelineError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect item sections from recent SEC 8-K filings."
    )
    parser.add_argument("ticker", help="Ticker symbol, for example AAPL")
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Number of recent 8-K filings, from 1 to 20 (default: 3)",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Download 8-K documents again even if they already exist",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_events_pipeline(
            args.ticker,
            limit=args.limit,
            force_download=args.force_download,
            progress=lambda message: print(f"[{message}]"),
        )
    except PipelineError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"8-K collection complete: {result.company.name}")
    print(f"Filings collected: {len(result.filings)}")
    print(f"Event items extracted: {result.event_item_count}")
    print(f"Manifest: {result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

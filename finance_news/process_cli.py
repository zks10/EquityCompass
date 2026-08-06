"""Command-line interface for converting a raw SEC filing to clean text."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from finance_news.filing_processor import FilingProcessingError, process_filing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a downloaded SEC filing from HTML to clean text."
    )
    parser.add_argument("input", type=Path, help="Path to the downloaded filing HTML")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output file (defaults to the matching data/processed path)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        destination = process_filing(args.input, args.output)
    except FilingProcessingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Processed filing saved: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

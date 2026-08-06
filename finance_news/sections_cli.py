"""Command-line interface for extracting major 10-K sections."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from finance_news.section_extractor import (
    SectionExtractionError,
    extract_sections_file,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract Business, Risk Factors, and MD&A from a cleaned 10-K."
    )
    parser.add_argument("input", type=Path, help="Path to the processed filing.txt")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output directory (defaults to a sections folder beside the input)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        saved_paths = extract_sections_file(args.input, args.output)
    except SectionExtractionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("Extracted sections:")
    for path in saved_paths:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

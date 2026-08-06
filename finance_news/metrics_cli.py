"""Command-line interface for deterministic annual financial metrics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from finance_news.derived_metrics import DerivedMetricsError, calculate_metrics_file


def format_percentage(value: float | None) -> str:
    return "not available" if value is None else f"{value}%"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate annual metrics from stored financial history."
    )
    parser.add_argument("input", type=Path, help="Path to financial_history.json")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output path (defaults to derived_metrics.json beside the input)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        destination = calculate_metrics_file(args.input, args.output)
        result = json.loads(destination.read_text(encoding="utf-8"))
    except (DerivedMetricsError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"{result.get('entity_name')} ({result.get('ticker')})")
    for period in result["periods"]:
        print()
        print(f"FY {period['fiscal_year']} | {period['period_end']}")
        print(
            "  Revenue growth: "
            f"{format_percentage(period['revenue_growth_percent'])}"
        )
        print(
            "  Net profit margin: "
            f"{format_percentage(period['net_profit_margin_percent'])}"
        )
        print(
            "  Liabilities to assets: "
            f"{format_percentage(period['liabilities_to_assets_percent'])}"
        )
        print(
            "  Operating cash flow margin: "
            f"{format_percentage(period['operating_cash_flow_margin_percent'])}"
        )
    print(f"Saved: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

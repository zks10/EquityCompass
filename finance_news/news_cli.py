"""Command-line interface for company-news collection."""

from __future__ import annotations

import argparse
import sys

from finance_news.news_pipeline import NewsPipelineError, run_news_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect recent company news from an RSS search feed."
    )
    parser.add_argument("ticker", help="Ticker symbol, for example AAPL")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="News lookback from 1 to 30 days (default: 7)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum articles from 1 to 100 (default: 20)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run_news_pipeline(
            args.ticker,
            days=args.days,
            limit=args.limit,
            progress=lambda message: print(f"[{message}]"),
        )
    except NewsPipelineError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"News collection complete: {result.company.name}")
    print(f"Articles collected: {result.article_count}")
    print(f"Raw RSS feed: {result.raw_feed_path}")
    print(f"Normalized articles: {result.articles_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

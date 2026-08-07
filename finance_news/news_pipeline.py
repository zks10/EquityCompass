"""Run company-news collection for a resolved ticker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from finance_news.news_collector import (
    NewsCollectionError,
    build_news_query,
    fetch_news_feed,
    parse_news_feed,
    save_news_results,
)
from finance_news.sec_companies import Company, CompanyLookupError, resolve_ticker


class NewsPipelineError(Exception):
    """Raised when a named company-news stage fails."""


@dataclass(frozen=True)
class NewsPipelineResult:
    company: Company
    article_count: int
    raw_feed_path: Path
    articles_path: Path


def run_news_pipeline(
    ticker: str,
    days: int = 7,
    limit: int = 20,
    progress: Callable[[str], None] | None = None,
) -> NewsPipelineResult:
    """Resolve a ticker and collect recent normalized company news."""
    notify = progress or (lambda _message: None)

    notify("1/4 Resolve ticker and company name")
    try:
        company = resolve_ticker(ticker)
    except CompanyLookupError as exc:
        raise NewsPipelineError(f"Resolve ticker failed: {exc}") from exc

    notify("2/4 Build and fetch the company news feed")
    try:
        query = build_news_query(company.name, days)
        feed_url, raw_xml = fetch_news_feed(query)
    except NewsCollectionError as exc:
        raise NewsPipelineError(f"Fetch company news failed: {exc}") from exc

    notify("3/4 Parse and deduplicate articles")
    try:
        articles = parse_news_feed(raw_xml, limit=limit)
    except NewsCollectionError as exc:
        raise NewsPipelineError(f"Parse company news failed: {exc}") from exc

    notify("4/4 Save raw and normalized news data")
    try:
        raw_path, processed_path = save_news_results(
            raw_xml,
            articles,
            company.ticker,
            company.cik,
            company.name,
            query,
            feed_url,
        )
    except NewsCollectionError as exc:
        raise NewsPipelineError(f"Save company news failed: {exc}") from exc

    return NewsPipelineResult(
        company=company,
        article_count=len(articles),
        raw_feed_path=raw_path,
        articles_path=processed_path,
    )

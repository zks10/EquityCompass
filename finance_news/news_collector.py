"""Collect and normalize company news from a replaceable RSS source."""

from __future__ import annotations

import base64
import io
import json
import os
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
import yfinance as yf
from googlenewsdecoder import gnewsdecoder
from PIL import Image


GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
DEFAULT_NEWS_USER_AGENT = "EquityCompass/0.1 news-rss-collector"
DEFAULT_RAW_ROOT = Path("data/raw/news")
DEFAULT_PROCESSED_ROOT = Path("data/processed/news")


class NewsCollectionError(Exception):
    """Raised when company news cannot be fetched, parsed, or saved."""


@dataclass(frozen=True)
class NewsArticle:
    title: str
    publisher: str
    published_at: str
    url: str
    guid: str
    image_url: str = ""


class _OpenGraphImageParser(HTMLParser):
    """Read the first usable Open Graph or Twitter card image."""

    def __init__(self) -> None:
        super().__init__()
        self.image_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta" or self.image_url:
            return
        attributes = {name.lower(): (value or "") for name, value in attrs}
        image_type = (attributes.get("property") or attributes.get("name") or "").lower()
        if image_type in {"og:image", "og:image:secure_url", "twitter:image"}:
            candidate = attributes.get("content", "").strip()
            if candidate.startswith(("https://", "http://")):
                self.image_url = candidate


def cache_image_data_uri(image_url: str) -> str:
    """Download and optimize a remote thumbnail for reliable browser rendering."""
    if not image_url:
        return ""
    try:
        response = requests.get(
            image_url,
            headers={"User-Agent": "Mozilla/5.0 EquityCompass/0.1"},
            timeout=10,
        )
        response.raise_for_status()
        with Image.open(io.BytesIO(response.content)) as source:
            image = source.convert("RGB")
            image.thumbnail((420, 260), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=80, optimize=True)
        encoded = base64.b64encode(output.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    except (requests.RequestException, OSError, ValueError):
        return ""


def fetch_article_metadata(url: str) -> tuple[str, str]:
    """Resolve the publisher URL and return its preview image when available."""
    try:
        decoded = gnewsdecoder(url)
        resolved_url = (
            str(decoded.get("decoded_url", "")).strip()
            if isinstance(decoded, dict) and decoded.get("status")
            else url
        )
        response = requests.get(
            resolved_url,
            headers={"User-Agent": "Mozilla/5.0 EquityCompass/0.1"},
            timeout=8,
            allow_redirects=True,
        )
        response.raise_for_status()
        parser = _OpenGraphImageParser()
        parser.feed(response.text[:750_000])
        return resolved_url, cache_image_data_uri(parser.image_url)
    except (requests.RequestException, UnicodeError, ValueError):
        return url, ""


def enrich_article_images(articles: list[NewsArticle]) -> list[NewsArticle]:
    """Fetch preview images concurrently while preserving article order."""
    if not articles:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(articles))) as executor:
        metadata = list(
            executor.map(fetch_article_metadata, (article.url for article in articles))
        )
    return [
        NewsArticle(
            title=article.title,
            publisher=article.publisher,
            published_at=article.published_at,
            url=resolved_url,
            guid=article.guid,
            image_url=image_url,
        )
        for article, (resolved_url, image_url) in zip(articles, metadata)
    ]


def fetch_rich_company_news(ticker: str, limit: int = 20) -> list[NewsArticle]:
    """Collect image-rich market news, returning an empty list on source failure."""
    try:
        entries = yf.Ticker(ticker).news
    except Exception:  # The RSS pipeline remains the reliable fallback.
        return []

    articles: list[NewsArticle] = []
    seen: set[str] = set()
    for entry in entries:
        content = entry.get("content", {}) if isinstance(entry, dict) else {}
        title = str(content.get("title", "")).strip()
        provider = content.get("provider", {}) or {}
        publisher = str(provider.get("displayName", "")).strip()
        published_at = str(content.get("pubDate", "")).strip()
        canonical = content.get("canonicalUrl", {}) or {}
        click_through = content.get("clickThroughUrl", {}) or {}
        url = str(canonical.get("url") or click_through.get("url") or "").strip()
        thumbnail = content.get("thumbnail", {}) or {}
        image_url = str(thumbnail.get("originalUrl", "")).strip()
        guid = str(content.get("id") or entry.get("id") or url).strip()
        if not all((title, publisher, published_at, url, image_url)) or guid in seen:
            continue
        seen.add(guid)
        articles.append(
            NewsArticle(
                title=title,
                publisher=publisher,
                published_at=published_at,
                url=url,
                guid=guid,
                image_url=image_url,
            )
        )
        if len(articles) >= limit:
            break
    with ThreadPoolExecutor(max_workers=min(6, len(articles))) as executor:
        cached_images = list(
            executor.map(cache_image_data_uri, (article.image_url for article in articles))
        )
    return [
        NewsArticle(
            title=article.title,
            publisher=article.publisher,
            published_at=article.published_at,
            url=article.url,
            guid=article.guid,
            image_url=image_data,
        )
        for article, image_data in zip(articles, cached_images)
        if image_data
    ]


def build_news_query(company_name: str, days: int) -> str:
    """Build a company-focused RSS search query."""
    clean_name = company_name.strip()
    if not clean_name:
        raise NewsCollectionError("Company name cannot be empty.")
    if days < 1 or days > 30:
        raise NewsCollectionError("Days must be between 1 and 30.")
    return f'"{clean_name}" when:{days}d'


def build_feed_url(query: str) -> str:
    parameters = urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )
    return f"{GOOGLE_NEWS_RSS_URL}?{parameters}"


def fetch_news_feed(query: str) -> tuple[str, bytes]:
    """Fetch raw RSS XML for a prepared search query."""
    feed_url = build_feed_url(query)
    try:
        response = requests.get(
            feed_url,
            headers={
                "User-Agent": os.getenv(
                    "NEWS_USER_AGENT", DEFAULT_NEWS_USER_AGENT
                ),
                "Accept": "application/rss+xml, application/xml, text/xml",
            },
            timeout=20,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise NewsCollectionError(
            f"News feed request failed with HTTP status {status}."
        ) from exc
    except requests.RequestException as exc:
        raise NewsCollectionError(f"Could not connect to the news feed: {exc}") from exc

    if not response.content:
        raise NewsCollectionError("The news feed returned an empty response.")
    return feed_url, response.content


def _publication_time(value: str) -> str:
    try:
        published = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise NewsCollectionError(f"Invalid news publication date: {value}") from exc
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return published.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_news_feed(xml_content: bytes | str, limit: int = 20) -> list[NewsArticle]:
    """Parse, deduplicate, and normalize RSS articles."""
    if limit < 1 or limit > 100:
        raise NewsCollectionError("Limit must be between 1 and 100.")
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise NewsCollectionError("The news feed returned invalid XML.") from exc

    articles: list[NewsArticle] = []
    seen: set[str] = set()
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        publisher = (item.findtext("source") or "").strip()
        published = (item.findtext("pubDate") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or url).strip()
        if not all((title, publisher, published, url)):
            continue

        publisher_suffix = f" - {publisher}"
        if title.endswith(publisher_suffix):
            title = title[: -len(publisher_suffix)].strip()

        deduplication_key = guid or f"{title.casefold()}|{publisher.casefold()}"
        if deduplication_key in seen:
            continue
        seen.add(deduplication_key)
        articles.append(
            NewsArticle(
                title=title,
                publisher=publisher,
                published_at=_publication_time(published),
                url=url,
                guid=guid,
            )
        )

    articles.sort(key=lambda article: article.published_at, reverse=True)
    return articles[:limit]


def save_news_results(
    raw_xml: bytes,
    articles: list[NewsArticle],
    ticker: str,
    cik: str,
    company_name: str,
    query: str,
    feed_url: str,
    raw_root: Path = DEFAULT_RAW_ROOT,
    processed_root: Path = DEFAULT_PROCESSED_ROOT,
) -> tuple[Path, Path]:
    """Save the original RSS feed and normalized article metadata."""
    raw_path = Path(raw_root) / cik / "feed.xml"
    processed_path = Path(processed_root) / cik / "articles.json"
    payload: dict[str, Any] = {
        "ticker": ticker,
        "cik": cik,
        "company_name": company_name,
        "query": query,
        "feed_url": feed_url,
        "collected_at": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "article_count": len(articles),
        "articles": [asdict(article) for article in articles],
    }

    try:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_temporary = raw_path.with_suffix(f"{raw_path.suffix}.part")
        raw_temporary.write_bytes(raw_xml)
        raw_temporary.replace(raw_path)

        processed_path.parent.mkdir(parents=True, exist_ok=True)
        processed_temporary = processed_path.with_suffix(
            f"{processed_path.suffix}.part"
        )
        processed_temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        processed_temporary.replace(processed_path)
    except OSError as exc:
        raise NewsCollectionError(f"Could not save company news: {exc}") from exc

    return raw_path, processed_path

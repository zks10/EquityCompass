"""Explainable, headline-only short-term news scoring.

The score intentionally measures the direction of recent coverage, not company
quality or expected stock returns.  It is deterministic so every result can be
traced back to visible headlines and weights.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Iterable, Protocol


class ScorableArticle(Protocol):
    title: str
    publisher: str
    published_at: str


@dataclass(frozen=True)
class ArticleSignal:
    title: str
    publisher: str
    sentiment: float
    weight: float
    age_hours: float
    direction: str
    reason: str


@dataclass(frozen=True)
class NewsScore:
    value: int
    label: str
    confidence: str
    confidence_value: int
    article_count: int
    independent_story_count: int
    scored_article_count: int
    positive_count: int
    negative_count: int
    neutral_count: int
    summary: str
    signals: tuple[ArticleSignal, ...]


# Multi-word phrases come first. Values reflect directional strength, not price
# impact. Opinion language is deliberately weaker than reported company events.
POSITIVE_PHRASES = {
    "raises guidance": 1.0, "raised guidance": 1.0, "beats estimates": 0.9,
    "beat estimates": 0.9, "record revenue": 0.85, "record profit": 0.85,
    "revenue growth": 0.65, "profit growth": 0.7, "margin expansion": 0.75,
    "wins contract": 0.7, "won contract": 0.7, "regulatory approval": 0.7,
    "share buyback": 0.55, "dividend increase": 0.55, "price target raised": 0.4,
    "rating upgrade": 0.4, "upgraded": 0.35, "outperforms": 0.35,
    "strong demand": 0.6, "higher revenue": 0.55, "higher profit": 0.6,
    "settles claims": 0.1, "launches": 0.2, "partnership": 0.25,
}

NEGATIVE_PHRASES = {
    "cuts guidance": -1.0, "cut guidance": -1.0, "misses estimates": -0.9,
    "missed estimates": -0.9, "revenue decline": -0.7, "profit decline": -0.75,
    "margin contraction": -0.75, "data breach": -0.9, "antitrust investigation": -0.75,
    "regulatory investigation": -0.75, "accounting investigation": -0.9,
    "product recall": -0.8, "layoffs": -0.55, "job cuts": -0.55,
    "price target cut": -0.4, "rating downgrade": -0.4, "downgraded": -0.35,
    "weak demand": -0.6, "lower revenue": -0.55, "lower profit": -0.6,
    "files for bankruptcy": -1.0, "fraud charges": -1.0, "ceo resigns": -0.55,
    "supply disruption": -0.65, "loses contract": -0.7, "lost contract": -0.7,
    "sell rating": -0.35, "lawsuit": -0.35, "fined": -0.45,
    "tumbled": -0.35, "plunged": -0.4, "slumped": -0.35,
}

RELEVANCE_PHRASES = {
    "guidance": 1.0, "earnings": 1.0, "revenue": 1.0, "profit": 1.0,
    "margin": 1.0, "eps": 1.0, "contract": 0.9, "acquisition": 0.9,
    "merger": 0.9, "investigation": 0.9, "recall": 0.9, "breach": 0.9,
    "lawsuit": 0.85, "ceo": 0.85, "layoffs": 0.8, "partnership": 0.75,
    "launch": 0.7, "analyst": 0.65, "upgrade": 0.65, "downgrade": 0.65,
    "price target": 0.65, "stock": 0.5, "shares": 0.5,
}

HIGH_TRUST_PUBLISHERS = (
    "reuters", "associated press", "bloomberg", "financial times",
    "wall street journal", "cnbc", "sec", "business wire", "globe newswire",
)
MARKET_PUBLISHERS = (
    "yahoo finance", "marketwatch", "barron's", "investing.com", "the motley fool",
    "marketbeat", "benzinga", "seeking alpha",
)
NEGATIONS = {"not", "no", "never", "without", "denies", "denied"}


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_title(title: str) -> str:
    title = re.sub(r"\$?[A-Z]{1,5}\b", " ", title)
    title = re.sub(r"[^a-z0-9 ]+", " ", title.lower())
    return " ".join(word for word in title.split() if word not in {"the", "a", "an"})


def _publisher_weight(publisher: str) -> float:
    normalized = publisher.lower()
    if any(name in normalized for name in HIGH_TRUST_PUBLISHERS):
        return 1.0
    if any(name in normalized for name in MARKET_PUBLISHERS):
        return 0.85
    return 0.7


def _phrase_is_negated(text: str, phrase: str) -> bool:
    start = text.find(phrase)
    if start < 0:
        return False
    preceding = text[:start].split()[-3:]
    return any(word in NEGATIONS for word in preceding)


def _headline_sentiment(title: str) -> tuple[float, str]:
    text = re.sub(r"\s+", " ", title.lower())
    matches: list[tuple[str, float]] = []
    occupied: list[tuple[int, int]] = []
    phrases = sorted(
        {**POSITIVE_PHRASES, **NEGATIVE_PHRASES}.items(),
        key=lambda item: len(item[0]), reverse=True,
    )
    for phrase, value in phrases:
        start = text.find(phrase)
        if start < 0 or any(start < end and start + len(phrase) > begin for begin, end in occupied):
            continue
        if _phrase_is_negated(text, phrase):
            value *= -0.65
        matches.append((phrase, value))
        occupied.append((start, start + len(phrase)))
    if not matches:
        return 0.0, "No directional finance phrase detected"
    raw = sum(value for _, value in matches)
    score = max(-1.0, min(1.0, raw))
    strongest = max(matches, key=lambda item: abs(item[1]))[0]
    return score, f'Headline phrase: “{strongest}”'


def _relevance_weight(title: str) -> float:
    text = title.lower()
    matched = [weight for phrase, weight in RELEVANCE_PHRASES.items() if phrase in text]
    return max(matched, default=0.45)


def _cluster_articles(articles: list[ArticleSignal]) -> list[list[ArticleSignal]]:
    clusters: list[list[ArticleSignal]] = []
    normalized: list[str] = []
    for article in articles:
        candidate = _normalized_title(article.title)
        match_index = next(
            (index for index, title in enumerate(normalized)
             if SequenceMatcher(None, candidate, title).ratio() >= 0.82),
            None,
        )
        if match_index is None:
            clusters.append([article])
            normalized.append(candidate)
        else:
            clusters[match_index].append(article)
    return clusters


def calculate_news_score(
    articles: Iterable[ScorableArticle], *, as_of: datetime | None = None,
    company_terms: Iterable[str] = (),
) -> NewsScore:
    """Return a recency-weighted, duplicate-aware score on a -100..100 scale."""
    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized_company_terms = tuple(
        term.strip().lower() for term in company_terms if term and term.strip()
    )
    signals: list[ArticleSignal] = []
    for article in articles:
        headline = article.title.lower()
        if normalized_company_terms and not any(
            re.search(rf"\b{re.escape(term)}\b", headline)
            for term in normalized_company_terms
        ):
            continue
        published = _parse_time(article.published_at)
        age_hours = max(0.0, (as_of - published).total_seconds() / 3600) if published else 168.0
        # 72-hour half-life; stories older than 14 days retain only a small audit weight.
        recency = max(0.08, 2 ** (-age_hours / 72.0))
        sentiment, reason = _headline_sentiment(article.title)
        weight = recency * _relevance_weight(article.title) * _publisher_weight(article.publisher)
        direction = "Positive" if sentiment > 0.12 else "Negative" if sentiment < -0.12 else "Neutral"
        signals.append(ArticleSignal(
            title=article.title, publisher=article.publisher, sentiment=sentiment,
            weight=weight, age_hours=age_hours, direction=direction, reason=reason,
        ))

    if not signals:
        return NewsScore(0, "Neutral", "Low", 0, 0, 0, 0, 0, 0, 0,
                         "No recent articles are available to establish a news direction.", ())

    clusters = _cluster_articles(signals)
    cluster_values: list[tuple[float, float]] = []
    for cluster in clusters:
        base_weight = max(article.weight for article in cluster)
        # Repetition adds at most 25%; syndicated copies cannot dominate the score.
        cluster_weight = base_weight * (1.0 + min(0.25, 0.08 * (len(cluster) - 1)))
        article_weight_total = sum(article.weight for article in cluster) or 1.0
        cluster_sentiment = sum(a.sentiment * a.weight for a in cluster) / article_weight_total
        cluster_values.append((cluster_sentiment, cluster_weight))

    total_weight = sum(weight for _, weight in cluster_values)
    weighted_direction = (
        sum(sentiment * weight for sentiment, weight in cluster_values) / total_weight
        if total_weight else 0.0
    )
    evidence_factor = min(1.0, math.sqrt(total_weight / 4.0))
    value = round(max(-100.0, min(100.0, weighted_direction * evidence_factor * 100)))
    label = (
        "Strongly positive" if value >= 55 else "Moderately positive" if value >= 20
        else "Neutral / mixed" if value > -20 else "Moderately negative" if value > -55
        else "Strongly negative"
    )

    scored = [signal for signal in signals if signal.direction != "Neutral"]
    positive = sum(signal.direction == "Positive" for signal in signals)
    negative = sum(signal.direction == "Negative" for signal in signals)
    neutral = len(signals) - positive - negative
    directional_total = positive + negative
    agreement = (
        max(positive, negative) / directional_total if directional_total else 0.35
    )
    evidence = min(1.0, total_weight / 5.0)
    diversity = min(1.0, len({s.publisher.lower() for s in signals}) / 4.0)
    scored_share = len(scored) / len(signals)
    confidence_value = round(100 * (
        0.55 * evidence + 0.20 * diversity + 0.15 * agreement + 0.10 * scored_share
    ))
    confidence = "High" if confidence_value >= 72 else "Medium" if confidence_value >= 45 else "Low"

    if value >= 20:
        summary = f"Recent directional coverage leans positive; {positive} positive and {negative} negative headline signals were detected."
    elif value <= -20:
        summary = f"Recent directional coverage leans negative; {negative} negative and {positive} positive headline signals were detected."
    else:
        summary = "Recent coverage is balanced, mixed, or mostly non-directional."

    return NewsScore(
        value=value, label=label, confidence=confidence,
        confidence_value=confidence_value, article_count=len(signals),
        independent_story_count=len(clusters), scored_article_count=len(scored),
        positive_count=positive, negative_count=negative, neutral_count=neutral,
        summary=summary,
        signals=tuple(sorted(signals, key=lambda signal: signal.weight, reverse=True)),
    )


__all__ = ["ArticleSignal", "NewsScore", "calculate_news_score"]

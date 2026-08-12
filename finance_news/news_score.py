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
    value: float
    available: bool
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
    "tops estimates": 0.85, "top estimates": 0.8, "above estimates": 0.7,
    "accelerating growth": 0.65, "record growth": 0.75,
    "strong fundamentals": 0.4, "revenue doubles": 0.8,
    "cleared a big hurdle": 0.25, "expanding": 0.3, "expands": 0.3,
    "boosts": 0.35, "boosted": 0.35, "surges": 0.35, "surged": 0.35,
    "jumps": 0.35, "jumped": 0.35, "rallies": 0.3, "rally": 0.3,
    "gains": 0.25, "climbs": 0.3, "pops": 0.3, "smashes": 0.55,
    "upbeat view": 0.35, "favor": 0.2,
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
    "falls": -0.3, "fell": -0.3, "drops": -0.3, "dropped": -0.3,
    "slides": -0.3, "sell-off": -0.3, "trails": -0.25,
    "growth concerns": -0.45, "regulatory scrutiny": -0.35,
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


def _entity_focus_weight(title: str, company_terms: tuple[str, ...]) -> float:
    """Reduce the influence of passing mentions and comparison headlines."""
    if not company_terms:
        return 1.0
    text = title.lower()
    matches = [match for term in company_terms if (match := re.search(rf"\b{re.escape(term)}\b", text))]
    if not matches:
        return 0.0
    first = min(match.start() for match in matches)
    if first <= 12:
        return 1.0
    if re.search(r"\b(vs\.?|versus|compared with|compared to)\b", text):
        return 0.45
    return 0.7


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
    company_terms: Iterable[str] = (), window_hours: float = 36.0,
) -> NewsScore:
    """Return a rolling daily, duplicate-aware score on a -10..10 scale."""
    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized_company_terms = tuple(
        term.strip().lower() for term in company_terms if term and term.strip()
    )
    signals: list[ArticleSignal] = []
    for article in articles:
        headline = article.title.lower()
        focus_weight = _entity_focus_weight(article.title, normalized_company_terms)
        if focus_weight == 0:
            continue
        published = _parse_time(article.published_at)
        age_hours = max(0.0, (as_of - published).total_seconds() / 3600) if published else 168.0
        if age_hours > window_hours:
            continue
        # A rolling 36-hour window covers the current session and prior market close.
        recency = 2 ** (-age_hours / 24.0)
        sentiment, reason = _headline_sentiment(article.title)
        weight = (
            recency * _relevance_weight(article.title)
            * _publisher_weight(article.publisher) * focus_weight
        )
        direction = "Positive" if sentiment > 0.12 else "Negative" if sentiment < -0.12 else "Neutral"
        signals.append(ArticleSignal(
            title=article.title, publisher=article.publisher, sentiment=sentiment,
            weight=weight, age_hours=age_hours, direction=direction, reason=reason,
        ))

    if not signals:
        return NewsScore(0, False, "Not enough fresh news", "Limited", 0, 0, 0, 0, 0, 0, 0,
                         "No fresh company-specific coverage is available for a daily signal.", ())

    clusters = _cluster_articles(signals)
    cluster_values: list[tuple[float, float]] = []
    for cluster in clusters:
        base_weight = max(article.weight for article in cluster)
        # Repetition adds at most 25%; syndicated copies cannot dominate the score.
        cluster_weight = base_weight * (1.0 + min(0.25, 0.08 * (len(cluster) - 1)))
        article_weight_total = sum(article.weight for article in cluster) or 1.0
        cluster_sentiment = sum(a.sentiment * a.weight for a in cluster) / article_weight_total
        cluster_values.append((cluster_sentiment, cluster_weight))

    directional_clusters = [
        (sentiment, weight) for sentiment, weight in cluster_values
        if abs(sentiment) > 0.12
    ]
    neutral_weight = sum(
        weight for sentiment, weight in cluster_values if abs(sentiment) <= 0.12
    )
    directional_weight = sum(weight for _, weight in directional_clusters)
    available = bool(directional_clusters)
    if available:
        weighted_direction = (
            sum(sentiment * weight for sentiment, weight in directional_clusters)
            / directional_weight
        )
        directional_coverage = directional_weight / (directional_weight + 0.35 * neutral_weight)
        evidence_factor = min(1.0, math.sqrt(directional_weight / 2.0))
        raw_value = max(-100.0, min(
            100.0,
            weighted_direction * (0.7 + 0.3 * directional_coverage)
            * evidence_factor * 100,
        ))
        value = round(raw_value / 10.0, 1)
        label = (
            "Strongly positive" if value >= 5.5 else "Positive" if value >= 2.0
            else "Slightly positive" if value > 0
            else "Mixed" if value == 0
            else "Slightly negative" if value > -2.0
            else "Negative" if value > -5.5
            else "Strongly negative"
        )
    else:
        value = 0
        label = "No clear signal"

    scored = [signal for signal in signals if signal.direction != "Neutral"]
    positive = sum(signal.direction == "Positive" for signal in signals)
    negative = sum(signal.direction == "Negative" for signal in signals)
    neutral = len(signals) - positive - negative
    directional_total = positive + negative
    agreement = (
        max(positive, negative) / directional_total if directional_total else 0.35
    )
    evidence = min(1.0, directional_weight / 3.0)
    diversity = min(1.0, len({s.publisher.lower() for s in signals}) / 4.0)
    scored_share = len(scored) / len(signals)
    confidence_value = round(100 * (
        0.55 * evidence + 0.20 * diversity + 0.15 * agreement + 0.10 * scored_share
    ))
    confidence = (
        "Strong" if confidence_value >= 72
        else "Moderate" if confidence_value >= 45
        else "Limited"
    )

    if not available:
        summary = "Fresh headlines contain no clear positive or negative company signal."
    elif value > 0:
        summary = f"{label} coverage: {positive} positive, {neutral} neutral, and {negative} negative fresh headlines."
    elif value < 0:
        summary = f"{label} coverage: {negative} negative, {neutral} neutral, and {positive} positive fresh headlines."
    else:
        summary = "Recent coverage is balanced, mixed, or mostly non-directional."

    return NewsScore(
        value=value, available=available, label=label, confidence=confidence,
        confidence_value=confidence_value, article_count=len(signals),
        independent_story_count=len(clusters), scored_article_count=len(scored),
        positive_count=positive, negative_count=negative, neutral_count=neutral,
        summary=summary,
        signals=tuple(sorted(signals, key=lambda signal: signal.weight, reverse=True)),
    )


__all__ = ["ArticleSignal", "NewsScore", "calculate_news_score"]

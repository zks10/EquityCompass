"""Transparent short-term market and catalyst scoring.

This module describes the current setup; it does not estimate fair value,
business quality, or expected investment return.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime

from finance_news.market_data import MarketOverview, PricePoint
from finance_news.news_score import NewsScore


@dataclass(frozen=True)
class ShortTermComponent:
    key: str
    label: str
    weight: float
    value: float | None
    detail: str


@dataclass(frozen=True)
class ShortTermScore:
    value: float
    available: bool
    label: str
    evidence: str
    evidence_value: int
    summary: str
    as_of: str
    components: tuple[ShortTermComponent, ...]
    conflicting: bool


def _returns(points: tuple[PricePoint, ...]) -> list[float]:
    closes = [point.close for point in points if point.close > 0]
    return [current / previous - 1 for previous, current in zip(closes, closes[1:])]


def _period_return(points: tuple[PricePoint, ...], sessions: int) -> float | None:
    if len(points) <= sessions or points[-1 - sessions].close <= 0:
        return None
    return points[-1].close / points[-1 - sessions].close - 1


def _realized_volatility(returns: list[float]) -> float:
    recent = returns[-20:]
    if len(recent) < 5:
        return 0.02
    return max(0.008, statistics.stdev(recent))


def _bounded_signal(z_score: float) -> float:
    return 10.0 * math.tanh(z_score / 2.0)


def _trend_signal(
    one_day: float | None, five_day: float | None, daily_volatility: float
) -> float | None:
    available: list[tuple[float, float]] = []
    if one_day is not None:
        available.append((0.35, _bounded_signal(one_day / daily_volatility)))
    if five_day is not None:
        scaled_volatility = daily_volatility * math.sqrt(5)
        available.append((0.65, _bounded_signal(five_day / scaled_volatility)))
    if not available:
        return None
    total_weight = sum(weight for weight, _ in available)
    return sum(weight * value for weight, value in available) / total_weight


def _aligned_excess_returns(
    stock: tuple[PricePoint, ...], benchmark: tuple[PricePoint, ...]
) -> tuple[float | None, float | None, float]:
    stock_by_date = {point.date: point.close for point in stock if point.close > 0}
    benchmark_by_date = {
        point.date: point.close for point in benchmark if point.close > 0
    }
    dates = sorted(set(stock_by_date) & set(benchmark_by_date))
    if len(dates) < 2:
        return None, None, 0.02
    stock_aligned = tuple(PricePoint(day, stock_by_date[day]) for day in dates)
    benchmark_aligned = tuple(PricePoint(day, benchmark_by_date[day]) for day in dates)
    stock_returns = _returns(stock_aligned)
    benchmark_returns = _returns(benchmark_aligned)
    residual_returns = [
        stock_return - benchmark_return
        for stock_return, benchmark_return in zip(stock_returns, benchmark_returns)
    ]
    residual_volatility = _realized_volatility(residual_returns)
    stock_1d = _period_return(stock_aligned, 1)
    benchmark_1d = _period_return(benchmark_aligned, 1)
    stock_5d = _period_return(stock_aligned, 5)
    benchmark_5d = _period_return(benchmark_aligned, 5)
    excess_1d = (
        stock_1d - benchmark_1d
        if stock_1d is not None and benchmark_1d is not None else None
    )
    excess_5d = (
        stock_5d - benchmark_5d
        if stock_5d is not None and benchmark_5d is not None else None
    )
    return excess_1d, excess_5d, residual_volatility


def _volume_confirmation(
    points: tuple[PricePoint, ...], direction: float | None
) -> tuple[float | None, float | None]:
    if direction is None or not points or points[-1].volume is None:
        return None, None
    prior_volumes = [
        point.volume for point in points[-21:-1]
        if point.volume is not None and point.volume > 0
    ]
    if len(prior_volumes) < 10:
        return None, None
    average_volume = statistics.mean(prior_volumes)
    ratio = points[-1].volume / average_volume if average_volume else 0.0
    if ratio < 1.10 or abs(direction) < 0.5:
        return 0.0, ratio
    magnitude = 10.0 * math.tanh((ratio - 1.0) / 1.25)
    return math.copysign(magnitude, direction), ratio


def _format_return(value: float | None) -> str:
    return "unavailable" if value is None else f"{value * 100:+.1f}%"


def calculate_short_term_score(
    market: MarketOverview | None, catalyst: NewsScore
) -> ShortTermScore:
    """Combine market behavior and fresh catalysts on a -10..10 scale."""
    if market is None or len(market.points) < 2:
        components = (
            ShortTermComponent("price", "Price trend", 0.35, None, "Market prices unavailable"),
            ShortTermComponent("relative", "Relative strength", 0.25, None, "Benchmark comparison unavailable"),
            ShortTermComponent("catalyst", "Recent catalysts", 0.25, catalyst.value if catalyst.available else None, catalyst.summary),
            ShortTermComponent("volume", "Volume confirmation", 0.15, None, "Trading volume unavailable"),
        )
        return ShortTermScore(
            value=0.0, available=False, label="Market data unavailable",
            evidence="Limited", evidence_value=0,
            summary="A short-term setup needs recent market prices.", as_of="",
            components=components, conflicting=False,
        )

    stock_returns = _returns(market.points)
    daily_volatility = _realized_volatility(stock_returns)
    one_day = _period_return(market.points, 1)
    five_day = _period_return(market.points, 5)
    price_value = _trend_signal(one_day, five_day, daily_volatility)

    excess_1d, excess_5d, residual_volatility = _aligned_excess_returns(
        market.points, market.benchmark_points
    )
    relative_value = _trend_signal(excess_1d, excess_5d, residual_volatility)
    catalyst_value = catalyst.value if catalyst.available else None
    volume_value, volume_ratio = _volume_confirmation(market.points, price_value)

    components = (
        ShortTermComponent(
            "price", "Price trend", 0.35, price_value,
            f"1-day {_format_return(one_day)} · 5-day {_format_return(five_day)}",
        ),
        ShortTermComponent(
            "relative", "Relative strength", 0.25, relative_value,
            f"vs {market.benchmark_ticker}: 1-day {_format_return(excess_1d)} · 5-day {_format_return(excess_5d)}",
        ),
        ShortTermComponent(
            "catalyst", "Recent catalysts", 0.25, catalyst_value,
            catalyst.summary,
        ),
        ShortTermComponent(
            "volume", "Volume confirmation", 0.15, volume_value,
            (
                "Volume unavailable"
                if volume_ratio is None
                else f"{volume_ratio:.1f}× the prior 20-session average"
            ),
        ),
    )

    weighted_value = sum(
        component.weight * (component.value or 0.0)
        for component in components
    )
    value = round(max(-10.0, min(10.0, weighted_value)), 1)
    directional = [
        component.value for component in components
        if component.value is not None and abs(component.value) >= 1.0
    ]
    has_positive = any(value > 0 for value in directional)
    has_negative = any(value < 0 for value in directional)
    conflicting = has_positive and has_negative
    if value >= 6:
        label = "Strong tailwind"
    elif value >= 2:
        label = "Supportive"
    elif value <= -6:
        label = "Strong pressure"
    elif value <= -2:
        label = "Under pressure"
    elif conflicting:
        label = "Mixed signals"
    else:
        label = "Quiet / balanced"

    market_complete = price_value is not None and relative_value is not None
    completeness = sum(component.value is not None for component in components) / 4
    catalyst_evidence = catalyst.confidence_value / 100 if catalyst.available else 0.25
    agreement = 0.45 if conflicting else 0.85 if directional else 0.35
    freshness = 1.0
    try:
        market_date = date.fromisoformat(market.as_of)
        freshness = 1.0 if (date.today() - market_date).days <= 3 else 0.4
    except ValueError:
        freshness = 0.5
    evidence_value = round(100 * (
        0.35 * completeness + 0.25 * catalyst_evidence
        + 0.25 * agreement + 0.15 * freshness
    ))
    if not market_complete:
        evidence_value = min(evidence_value, 44)
    evidence = (
        "Strong" if evidence_value >= 75
        else "Moderate" if evidence_value >= 45
        else "Limited"
    )

    strongest = max(
        (component for component in components if component.value is not None),
        key=lambda component: abs(component.weight * (component.value or 0.0)),
    )
    if conflicting:
        summary = "Market and catalyst signals disagree; the setup is not confirmed."
    elif abs(value) < 2:
        summary = "Recent factors are quiet or too small to establish a clear setup."
    else:
        direction_text = "supportive" if value > 0 else "negative"
        summary = f"{strongest.label} is the main {direction_text} short-term factor."

    return ShortTermScore(
        value=value, available=True, label=label, evidence=evidence,
        evidence_value=evidence_value, summary=summary, as_of=market.as_of,
        components=components, conflicting=conflicting,
    )


__all__ = ["ShortTermComponent", "ShortTermScore", "calculate_short_term_score"]

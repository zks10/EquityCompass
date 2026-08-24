"""Provider-neutral market bars, persistence, and cheap-trigger features."""

from __future__ import annotations

import math
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol, Sequence

from finance_news.weekly.storage import transaction


MARKET_FEATURE_VERSION = "market-features-v1"


class MarketIngestionError(ValueError):
    """Raised when normalized market data cannot be safely used or persisted."""


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    session_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    adjusted_close: float
    volume: float | None
    currency: str
    provider: str
    collected_at: datetime
    quality_status: str = "complete"

    def __post_init__(self) -> None:
        if not self.symbol.strip() or self.symbol != self.symbol.upper():
            raise MarketIngestionError("Market symbol must be non-empty and uppercase.")
        if self.close <= 0 or self.adjusted_close <= 0:
            raise MarketIngestionError("Market closes must be positive.")
        if self.volume is not None and self.volume < 0:
            raise MarketIngestionError("Market volume cannot be negative.")
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise MarketIngestionError("Market collection timestamp must be timezone-aware.")
        if not self.currency.strip() or not self.provider.strip():
            raise MarketIngestionError("Market currency and provider are required.")


class MarketDataProvider(Protocol):
    """Boundary implemented by batch-capable market sources."""

    def get_daily_bars(
        self, symbols: Sequence[str], start: date, end: date
    ) -> tuple[DailyBar, ...]: ...


@dataclass(frozen=True)
class MarketFeature:
    name: str
    value: float | None
    quality_status: str


@dataclass(frozen=True)
class MarketFeatureSet:
    symbol: str
    benchmark_symbol: str
    as_of_session: date
    input_start: date
    input_end: date
    features: tuple[MarketFeature, ...]

    def value(self, name: str) -> float | None:
        return next(feature.value for feature in self.features if feature.name == name)


def store_daily_bars(connection: sqlite3.Connection, bars: Sequence[DailyBar]) -> int:
    """Insert normalized bars idempotently and reject conflicting observations."""
    if not bars:
        return 0
    unique: dict[tuple[str, date, str], DailyBar] = {}
    for bar in bars:
        key = (bar.symbol, bar.session_date, bar.provider)
        prior = unique.get(key)
        if prior is not None and prior != bar:
            raise MarketIngestionError(f"Conflicting bars in batch for {bar.symbol} on {bar.session_date}.")
        unique[key] = bar
    with transaction(connection):
        for bar in unique.values():
            existing = connection.execute(
                "SELECT open, high, low, close, adjusted_close, volume, currency "
                "FROM daily_market_bars WHERE symbol = ? AND session_date = ? AND provider = ?",
                (bar.symbol, bar.session_date.isoformat(), bar.provider),
            ).fetchone()
            values = (bar.open, bar.high, bar.low, bar.close, bar.adjusted_close, bar.volume, bar.currency)
            if existing is not None:
                stored = tuple(existing[key] for key in ("open", "high", "low", "close", "adjusted_close", "volume", "currency"))
                if stored != values:
                    raise MarketIngestionError(
                        f"Stored bar conflicts with {bar.symbol} on {bar.session_date}."
                    )
                continue
            connection.execute(
                "INSERT INTO daily_market_bars "
                "(symbol, session_date, open, high, low, close, adjusted_close, volume, "
                "currency, provider, collected_at, quality_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    bar.symbol, bar.session_date.isoformat(), bar.open, bar.high, bar.low,
                    bar.close, bar.adjusted_close, bar.volume, bar.currency, bar.provider,
                    bar.collected_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    bar.quality_status,
                ),
            )
    return len(unique)


def load_daily_bars(
    connection: sqlite3.Connection,
    symbol: str,
    provider: str,
    start: date,
    end: date,
) -> tuple[DailyBar, ...]:
    """Load a chronologically ordered normalized series."""
    rows = connection.execute(
        "SELECT * FROM daily_market_bars WHERE symbol = ? AND provider = ? "
        "AND session_date BETWEEN ? AND ? ORDER BY session_date",
        (symbol, provider, start.isoformat(), end.isoformat()),
    ).fetchall()
    return tuple(
        DailyBar(
            symbol=row["symbol"], session_date=date.fromisoformat(row["session_date"]),
            open=row["open"], high=row["high"], low=row["low"], close=row["close"],
            adjusted_close=row["adjusted_close"], volume=row["volume"], currency=row["currency"],
            provider=row["provider"], collected_at=datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00")),
            quality_status=row["quality_status"],
        )
        for row in rows
    )


def _returns(values: Sequence[float]) -> list[float]:
    return [current / previous - 1 for previous, current in zip(values, values[1:])]


def _period_return(values: Sequence[float], sessions: int) -> float | None:
    if len(values) <= sessions:
        return None
    return values[-1] / values[-1 - sessions] - 1


def calculate_market_features(
    stock_bars: Sequence[DailyBar],
    benchmark_bars: Sequence[DailyBar],
    *,
    minimum_history: int = 21,
) -> MarketFeatureSet:
    """Calculate deterministic, corporate-action-safe cheap-trigger inputs."""
    if not stock_bars or not benchmark_bars:
        raise MarketIngestionError("Stock and benchmark bars are required.")
    stock = sorted(stock_bars, key=lambda bar: bar.session_date)
    benchmark = sorted(benchmark_bars, key=lambda bar: bar.session_date)
    if len(stock) < minimum_history:
        raise MarketIngestionError(f"At least {minimum_history} stock sessions are required.")
    stock_by_date = {bar.session_date: bar for bar in stock}
    benchmark_by_date = {bar.session_date: bar for bar in benchmark}
    common_dates = sorted(stock_by_date.keys() & benchmark_by_date.keys())
    if len(common_dates) < minimum_history:
        raise MarketIngestionError(f"At least {minimum_history} aligned benchmark sessions are required.")
    stock_values = [stock_by_date[item].adjusted_close for item in common_dates]
    benchmark_values = [benchmark_by_date[item].adjusted_close for item in common_dates]
    stock_returns = _returns(stock_values)
    benchmark_returns = _returns(benchmark_values)
    excess_returns = [left - right for left, right in zip(stock_returns, benchmark_returns)]
    five_day = _period_return(stock_values, 5)
    benchmark_five_day = _period_return(benchmark_values, 5)
    abnormal_five_day = (
        None if five_day is None or benchmark_five_day is None else five_day - benchmark_five_day
    )
    recent_excess = excess_returns[-60:]
    excess_volatility = statistics.stdev(recent_excess) if len(recent_excess) >= 5 else None
    abnormal_z_score = (
        None
        if abnormal_five_day is None or not excess_volatility
        else abnormal_five_day / (excess_volatility * math.sqrt(5))
    )
    recent_dollar_volumes = [
        bar.adjusted_close * bar.volume
        for bar in stock[-20:]
        if bar.volume is not None
    ]
    average_dollar_volume = (
        sum(recent_dollar_volumes) / len(recent_dollar_volumes)
        if len(recent_dollar_volumes) >= 5 else None
    )
    volume_quality = "complete" if average_dollar_volume is not None else "partial"
    return MarketFeatureSet(
        symbol=stock[0].symbol,
        benchmark_symbol=benchmark[0].symbol,
        as_of_session=common_dates[-1], input_start=common_dates[0], input_end=common_dates[-1],
        features=(
            MarketFeature("five_day_return", five_day, "complete"),
            MarketFeature("benchmark_five_day_return", benchmark_five_day, "complete"),
            MarketFeature("abnormal_five_day_return", abnormal_five_day, "complete"),
            MarketFeature("abnormal_return_z_score", abnormal_z_score, "complete"),
            MarketFeature("average_dollar_volume_20d", average_dollar_volume, volume_quality),
        ),
    )


def store_market_features(
    connection: sqlite3.Connection,
    company_id: str,
    feature_set: MarketFeatureSet,
) -> None:
    """Persist reproducible weekly market features without silent replacement."""
    with transaction(connection):
        for feature in feature_set.features:
            feature_id = (
                f"{company_id}:{feature_set.as_of_session}:{feature.name}:{MARKET_FEATURE_VERSION}"
            )
            connection.execute(
                "INSERT INTO market_features "
                "(feature_id, company_id, as_of_session, feature_name, value, benchmark_symbol, "
                "calculation_version, input_start, input_end, quality_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(company_id, as_of_session, feature_name, calculation_version) "
                "DO UPDATE SET value = excluded.value, benchmark_symbol = excluded.benchmark_symbol, "
                "input_start = excluded.input_start, input_end = excluded.input_end, "
                "quality_status = excluded.quality_status",
                (
                    feature_id, company_id, feature_set.as_of_session.isoformat(), feature.name,
                    feature.value, feature_set.benchmark_symbol, MARKET_FEATURE_VERSION,
                    feature_set.input_start.isoformat(), feature_set.input_end.isoformat(),
                    feature.quality_status,
                ),
            )


__all__ = [
    "DailyBar", "MARKET_FEATURE_VERSION", "MarketDataProvider", "MarketFeature",
    "MarketFeatureSet", "MarketIngestionError", "calculate_market_features",
    "load_daily_bars", "store_daily_bars", "store_market_features",
]

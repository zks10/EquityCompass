"""Small, read-only market-price snapshot used by the Overview tab."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import yfinance as yf


class MarketDataError(Exception):
    """Raised when recent market prices cannot be loaded."""


@dataclass(frozen=True)
class PricePoint:
    date: str
    close: float
    volume: float | None = None


@dataclass(frozen=True)
class MarketOverview:
    ticker: str
    latest_price: float
    previous_close: float
    as_of: str
    points: tuple[PricePoint, ...]
    intraday_points: tuple[PricePoint, ...]
    sector: str | None = None
    industry: str | None = None
    headquarters: str | None = None
    employees: int | None = None
    website: str | None = None
    benchmark_ticker: str = "SPY"
    benchmark_points: tuple[PricePoint, ...] = ()

    @property
    def price_change(self) -> float:
        return self.latest_price - self.previous_close

    @property
    def price_change_percent(self) -> float:
        if self.previous_close == 0:
            return 0.0
        return self.price_change / self.previous_close * 100


def fetch_market_overview(ticker: str) -> MarketOverview:
    """Load five years of daily closes and calculate the latest daily move."""
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        raise MarketDataError("A ticker is required for market data.")

    ticker_client = yf.Ticker(normalized_ticker)
    try:
        history = ticker_client.history(
            period="5y", interval="1d", auto_adjust=False
        )
    except Exception as exc:  # yfinance exposes several transport exceptions
        raise MarketDataError(f"Market data is temporarily unavailable: {exc}") from exc

    if history.empty or "Close" not in history or len(history["Close"].dropna()) < 2:
        raise MarketDataError("Not enough recent market prices were returned.")

    closes = history["Close"].dropna()
    volumes = history["Volume"] if "Volume" in history else None
    points = tuple(
        PricePoint(
            date=index.strftime("%Y-%m-%d"),
            close=float(value),
            volume=(
                float(volumes.get(index))
                if volumes is not None and pd.notna(volumes.get(index))
                else None
            ),
        )
        for index, value in closes.items()
    )
    intraday_points: tuple[PricePoint, ...] = ()
    try:
        intraday_history = ticker_client.history(
            period="1d", interval="5m", auto_adjust=False
        )
        if not intraday_history.empty and "Close" in intraday_history:
            intraday_closes = intraday_history["Close"].dropna()
            intraday_points = tuple(
                PricePoint(date=index.isoformat(), close=float(value))
                for index, value in intraday_closes.items()
            )
    except Exception:
        # Intraday data is an enhancement. Daily history should remain usable.
        pass
    benchmark_points: tuple[PricePoint, ...] = ()
    try:
        benchmark_history = yf.Ticker("SPY").history(
            period="3mo", interval="1d", auto_adjust=False
        )
        if not benchmark_history.empty and "Close" in benchmark_history:
            benchmark_closes = benchmark_history["Close"].dropna()
            benchmark_points = tuple(
                PricePoint(date=index.strftime("%Y-%m-%d"), close=float(value))
                for index, value in benchmark_closes.items()
            )
    except Exception:
        # The company chart remains useful when benchmark data is unavailable.
        pass
    profile: dict = {}
    try:
        profile = ticker_client.get_info() or {}
    except Exception:
        # Company profile fields are optional and must not block price history.
        pass
    city = str(profile.get("city", "")).strip()
    country = str(profile.get("country", "")).strip()
    headquarters = ", ".join(part for part in (city, country) if part) or None
    employee_value = profile.get("fullTimeEmployees")
    try:
        employees = int(employee_value) if employee_value is not None else None
    except (TypeError, ValueError):
        employees = None
    return MarketOverview(
        ticker=normalized_ticker,
        latest_price=points[-1].close,
        previous_close=points[-2].close,
        as_of=points[-1].date,
        points=points,
        intraday_points=intraday_points,
        sector=str(profile.get("sector", "")).strip() or None,
        industry=str(profile.get("industry", "")).strip() or None,
        headquarters=headquarters,
        employees=employees,
        website=str(profile.get("website", "")).strip() or None,
        benchmark_points=benchmark_points,
    )


__all__ = [
    "MarketDataError",
    "MarketOverview",
    "PricePoint",
    "fetch_market_overview",
]

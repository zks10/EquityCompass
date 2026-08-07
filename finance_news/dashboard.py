"""Prepare the small data summary displayed by the Streamlit app."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from finance_news.news_pipeline import NewsPipelineError, run_news_pipeline
from finance_news.pipeline import PipelineError, run_pipeline
from finance_news.quarterly_pipeline import run_quarterly_pipeline


class DashboardError(Exception):
    """Raised when Equity Compass cannot prepare the dashboard summary."""


@dataclass(frozen=True)
class FinancialOverview:
    fiscal_year: int
    period_end: str
    revenue: int | float
    net_income: int | float
    assets: int | float
    liabilities: int | float
    operating_cash_flow: int | float
    revenue_growth_percent: float | None
    net_profit_margin_percent: float | None
    liabilities_to_assets_percent: float | None
    operating_cash_flow_margin_percent: float | None


@dataclass(frozen=True)
class FinancialHistoryRow:
    fiscal_year: int
    period_end: str
    revenue: int | float
    net_income: int | float
    assets: int | float
    liabilities: int | float
    operating_cash_flow: int | float


@dataclass(frozen=True)
class DashboardSummary:
    company_name: str
    cik: str
    latest_10k_date: str
    latest_10q_date: str
    news_article_count: int
    financials: FinancialOverview
    financial_history: tuple[FinancialHistoryRow, ...]


def _read_json(path: Path, description: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DashboardError(f"Could not read saved {description}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DashboardError(f"Saved {description} must be a JSON object.")
    return payload


def _read_article_count(path: Path) -> int:
    """Read the saved normalized news output and return its article count."""
    try:
        payload = _read_json(path, "news results")
        article_count = payload["article_count"]
    except (KeyError, TypeError) as exc:
        raise DashboardError(f"Could not read saved news results: {exc}") from exc

    if not isinstance(article_count, int) or article_count < 0:
        raise DashboardError("Saved news results contain an invalid article count.")
    return article_count


def _read_financial_overview(
    facts_path: Path, metrics_path: Path
) -> FinancialOverview:
    """Read the latest annual values and ratios saved by the annual pipeline."""
    facts_payload = _read_json(facts_path, "financial facts")
    metrics_payload = _read_json(metrics_path, "derived metrics")

    try:
        facts = {fact["metric"]: fact["value"] for fact in facts_payload["facts"]}
        latest = metrics_payload["periods"][0]
        financials = FinancialOverview(
            fiscal_year=int(latest["fiscal_year"]),
            period_end=str(latest["period_end"]),
            revenue=facts["revenue"],
            net_income=facts["net_income"],
            assets=facts["assets"],
            liabilities=facts["liabilities"],
            operating_cash_flow=facts["operating_cash_flow"],
            revenue_growth_percent=latest["revenue_growth_percent"],
            net_profit_margin_percent=latest["net_profit_margin_percent"],
            liabilities_to_assets_percent=latest[
                "liabilities_to_assets_percent"
            ],
            operating_cash_flow_margin_percent=latest[
                "operating_cash_flow_margin_percent"
            ],
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise DashboardError(
            f"Saved financial outputs have an unexpected format: {exc}"
        ) from exc

    money_values = (
        financials.revenue,
        financials.net_income,
        financials.assets,
        financials.liabilities,
        financials.operating_cash_flow,
    )
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in money_values):
        raise DashboardError("Saved financial facts contain a non-numeric value.")
    return financials


def _read_financial_history(path: Path) -> tuple[FinancialHistoryRow, ...]:
    """Read and align the saved annual financial history by period end."""
    payload = _read_json(path, "financial history")
    metric_names = (
        "revenue",
        "net_income",
        "assets",
        "liabilities",
        "operating_cash_flow",
    )

    try:
        metrics = payload["metrics"]
        indexed = {
            name: {record["period_end"]: record for record in metrics[name]}
            for name in metric_names
        }
        common_periods = set.intersection(
            *(set(records) for records in indexed.values())
        )
        rows = tuple(
            FinancialHistoryRow(
                fiscal_year=int(indexed["revenue"][period]["fiscal_year"]),
                period_end=period,
                revenue=indexed["revenue"][period]["value"],
                net_income=indexed["net_income"][period]["value"],
                assets=indexed["assets"][period]["value"],
                liabilities=indexed["liabilities"][period]["value"],
                operating_cash_flow=indexed["operating_cash_flow"][period][
                    "value"
                ],
            )
            for period in sorted(common_periods, reverse=True)
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DashboardError(
            f"Saved financial history has an unexpected format: {exc}"
        ) from exc

    if not rows:
        raise DashboardError("Saved financial history has no aligned annual periods.")

    for row in rows:
        values = (
            row.revenue,
            row.net_income,
            row.assets,
            row.liabilities,
            row.operating_cash_flow,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        ):
            raise DashboardError("Saved financial history contains a non-numeric value.")
    return rows


def analyze_ticker(
    ticker: str,
    progress: Callable[[str], None] | None = None,
) -> DashboardSummary:
    """Run the existing collectors and return the fields needed by the UI."""
    notify = progress or (lambda _message: None)

    try:
        notify("Starting annual data collection")
        annual = run_pipeline(
            ticker, progress=lambda message: notify(f"Annual data: {message}")
        )
        notify("Starting quarterly data collection")
        quarterly = run_quarterly_pipeline(
            ticker, progress=lambda message: notify(f"Quarterly data: {message}")
        )
        notify("Starting recent news collection")
        news = run_news_pipeline(
            ticker, progress=lambda message: notify(f"News: {message}")
        )
    except (PipelineError, NewsPipelineError) as exc:
        raise DashboardError(str(exc)) from exc

    return DashboardSummary(
        company_name=annual.company.name,
        cik=annual.company.cik,
        latest_10k_date=annual.filing.filing_date,
        latest_10q_date=quarterly.filing.filing_date,
        news_article_count=_read_article_count(news.articles_path),
        financials=_read_financial_overview(
            annual.latest_facts_path, annual.derived_metrics_path
        ),
        financial_history=_read_financial_history(annual.history_path),
    )


__all__ = [
    "DashboardError",
    "DashboardSummary",
    "FinancialOverview",
    "FinancialHistoryRow",
    "analyze_ticker",
]

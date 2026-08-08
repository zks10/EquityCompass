"""Prepare the small data summary displayed by the Streamlit app."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from finance_news.events_pipeline import run_events_pipeline
from finance_news.news_pipeline import NewsPipelineError, run_news_pipeline
from finance_news.pipeline import PipelineError, run_pipeline
from finance_news.quarterly_pipeline import run_quarterly_pipeline
from finance_news.sec_companies import CompanyLookupError, resolve_ticker
from finance_news.sec_filings import FilingLookupError, fetch_recent_filings


class DashboardError(Exception):
    """Raised when Equity Compass cannot prepare the dashboard summary."""


@dataclass(frozen=True)
class TickerEligibility:
    """Result of checking whether a ticker fits the current product scope."""

    ticker: str
    company_name: str
    supported: bool
    message: str


def check_ticker_eligibility(ticker: str) -> TickerEligibility:
    """Confirm that a ticker belongs to a supported U.S. domestic SEC filer."""
    normalized_ticker = ticker.strip().upper()
    try:
        company = resolve_ticker(normalized_ticker)
        filings = fetch_recent_filings(company.cik, limit=100)
    except (CompanyLookupError, FilingLookupError) as exc:
        return TickerEligibility(
            ticker=normalized_ticker,
            company_name="",
            supported=False,
            message=f"This ticker could not be verified: {exc}",
        )

    forms = {filing.form for filing in filings}
    if "10-K" in forms:
        return TickerEligibility(
            ticker=company.ticker,
            company_name=company.name,
            supported=True,
            message=f"{company.name} is a supported U.S. domestic SEC filer.",
        )
    if "20-F" in forms:
        return TickerEligibility(
            ticker=company.ticker,
            company_name=company.name,
            supported=False,
            message=(
                f"{company.name} is a foreign private issuer that files Form 20-F. "
                "International IFRS issuers are not supported yet."
            ),
        )
    return TickerEligibility(
        ticker=company.ticker,
        company_name=company.name,
        supported=False,
        message=(
            f"{company.name} does not have a recent supported Form 10-K. "
            "Equity Compass currently requires a U.S. domestic annual filing."
        ),
    )


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
class FinancialInsight:
    """A plain-language explanation of one calculated financial metric."""

    title: str
    label: str
    explanation: str


@dataclass(frozen=True)
class ScoreComponent:
    """One transparent component of the financial snapshot score."""

    name: str
    score: int | None
    source_value: float | None
    explanation: str


@dataclass(frozen=True)
class FinancialSnapshotScore:
    """A narrow score of currently available annual financial signals."""

    score: int | None
    label: str
    available_components: int
    components: tuple[ScoreComponent, ...]


@dataclass(frozen=True)
class FinancialHistoryRow:
    fiscal_year: int
    period_end: str
    revenue: int | float
    net_income: int | float
    assets: int | float
    liabilities: int | float
    operating_cash_flow: int | float
    capital_expenditures: int | float | None = None
    eps: int | float | None = None

    @property
    def free_cash_flow(self) -> int | float | None:
        """Return cash left after capital investment."""
        if self.capital_expenditures is None:
            return None
        return self.operating_cash_flow - abs(self.capital_expenditures)


@dataclass(frozen=True)
class RecentNewsArticle:
    title: str
    publisher: str
    published_at: str
    url: str


@dataclass(frozen=True)
class NewsTopic:
    """A topic detected mechanically from recent news headlines."""

    label: str
    article_count: int


@dataclass(frozen=True)
class AnnualFilingSections:
    business: str
    risk_factors: str
    mda: str


@dataclass(frozen=True)
class QuarterlyFilingSections:
    risk_factors: str
    mda: str


@dataclass(frozen=True)
class RecentEventItem:
    item_number: str
    title: str
    text: str


@dataclass(frozen=True)
class RecentEventFiling:
    filing_date: str
    accession_number: str
    document_url: str
    items: tuple[RecentEventItem, ...]


@dataclass(frozen=True)
class DashboardSummary:
    ticker: str
    company_name: str
    cik: str
    latest_10k_date: str
    latest_10q_date: str
    news_article_count: int
    financials: FinancialOverview
    financial_history: tuple[FinancialHistoryRow, ...]
    recent_news: tuple[RecentNewsArticle, ...]
    annual_sections: AnnualFilingSections
    quarterly_sections: QuarterlyFilingSections
    recent_events: tuple[RecentEventFiling, ...]
    data_warnings: tuple[str, ...] = ()


def build_financial_insights(
    financials: FinancialOverview,
) -> tuple[FinancialInsight, ...]:
    """Translate calculated ratios into cautious, beginner-friendly language."""
    growth = financials.revenue_growth_percent
    if growth is None:
        growth_label = "Not available"
        growth_explanation = "There is not enough annual data to compare revenue."
    elif growth >= 5:
        growth_label = "Growing"
        growth_explanation = (
            f"Revenue increased {growth:.1f}% from the previous fiscal year."
        )
    elif growth >= 0:
        growth_label = "Mostly steady"
        growth_explanation = (
            f"Revenue increased {growth:.1f}% from the previous fiscal year."
        )
    else:
        growth_label = "Revenue declined"
        growth_explanation = (
            f"Revenue decreased {abs(growth):.1f}% from the previous fiscal year."
        )

    profit_margin = financials.net_profit_margin_percent
    if profit_margin is None:
        profit_label = "Not available"
        profit_explanation = "A profit margin could not be calculated."
    elif profit_margin >= 20:
        profit_label = "High profit margin"
        profit_explanation = (
            f"The company kept about {profit_margin:.0f} dollars in net profit for "
            "every 100 dollars of revenue."
        )
    elif profit_margin >= 10:
        profit_label = "Profitable"
        profit_explanation = (
            f"The company kept about {profit_margin:.0f} dollars in net profit for "
            "every 100 dollars of revenue."
        )
    elif profit_margin >= 0:
        profit_label = "Low profit margin"
        profit_explanation = (
            f"The company kept about {profit_margin:.0f} dollars in net profit for "
            "every 100 dollars of revenue."
        )
    else:
        profit_label = "Reported a loss"
        profit_explanation = (
            f"The company lost about {abs(profit_margin):.0f} dollars for every 100 "
            "dollars of revenue."
        )

    liabilities_ratio = financials.liabilities_to_assets_percent
    if liabilities_ratio is None:
        liabilities_label = "Not available"
        liabilities_explanation = "The liabilities-to-assets ratio could not be calculated."
    elif liabilities_ratio <= 50:
        liabilities_label = "Lower liabilities share"
        liabilities_explanation = (
            f"Liabilities equal about {liabilities_ratio:.0f}% of assets. This still "
            "needs comparison with similar companies."
        )
    elif liabilities_ratio <= 80:
        liabilities_label = "Higher liabilities share"
        liabilities_explanation = (
            f"Liabilities equal about {liabilities_ratio:.0f}% of assets. This is not "
            "automatically bad, but industry comparison matters."
        )
    else:
        liabilities_label = "Very high liabilities share"
        liabilities_explanation = (
            f"Liabilities equal about {liabilities_ratio:.0f}% of assets, so the "
            "balance sheet deserves closer review."
        )

    cash_margin = financials.operating_cash_flow_margin_percent
    if cash_margin is None:
        cash_label = "Not available"
        cash_explanation = "An operating cash-flow margin could not be calculated."
    elif cash_margin >= 20:
        cash_label = "Strong cash generation"
        cash_explanation = (
            f"Operations generated about {cash_margin:.0f} dollars in cash for every "
            "100 dollars of revenue."
        )
    elif cash_margin >= 10:
        cash_label = "Positive cash generation"
        cash_explanation = (
            f"Operations generated about {cash_margin:.0f} dollars in cash for every "
            "100 dollars of revenue."
        )
    elif cash_margin >= 0:
        cash_label = "Low cash generation"
        cash_explanation = (
            f"Operations generated about {cash_margin:.0f} dollars in cash for every "
            "100 dollars of revenue."
        )
    else:
        cash_label = "Negative operating cash flow"
        cash_explanation = (
            f"Operations used about {abs(cash_margin):.0f} dollars in cash for every "
            "100 dollars of revenue."
        )

    return (
        FinancialInsight("Revenue trend", growth_label, growth_explanation),
        FinancialInsight("Profitability", profit_label, profit_explanation),
        FinancialInsight(
            "Balance sheet", liabilities_label, liabilities_explanation
        ),
        FinancialInsight("Cash generation", cash_label, cash_explanation),
    )


def _bounded_score(value: float) -> int:
    """Round a numeric score and keep it between zero and one hundred."""
    return round(max(0.0, min(100.0, value)))


def build_financial_snapshot_score(
    financials: FinancialOverview,
) -> FinancialSnapshotScore:
    """Score four reported metrics using visible, deterministic thresholds."""
    definitions = (
        (
            "Revenue growth",
            financials.revenue_growth_percent,
            lambda value: 50 + (5 * value),
            "Measures the annual change in revenue. Zero growth scores 50; minus 10% scores 0; plus 10% scores 100.",
        ),
        (
            "Net profit margin",
            financials.net_profit_margin_percent,
            lambda value: 4 * value,
            "Measures net profit earned from each 100 dollars of revenue. A zero margin scores 0; a 25% margin scores 100.",
        ),
        (
            "Liabilities / assets",
            financials.liabilities_to_assets_percent,
            lambda value: ((100 - value) / 60) * 100,
            "Measures liabilities as a share of assets. A 40% ratio scores 100; a 100% ratio scores 0. Industry context is not included.",
        ),
        (
            "Operating cash flow margin",
            financials.operating_cash_flow_margin_percent,
            lambda value: 4 * value,
            "Measures operating cash generated from each 100 dollars of revenue. A zero margin scores 0; a 25% margin scores 100.",
        ),
    )
    components = tuple(
        ScoreComponent(
            name=name,
            score=None if value is None else _bounded_score(calculator(value)),
            source_value=value,
            explanation=explanation,
        )
        for name, value, calculator, explanation in definitions
    )
    available_scores = [
        component.score for component in components if component.score is not None
    ]
    if not available_scores:
        return FinancialSnapshotScore(
            score=None,
            label="Not enough data",
            available_components=0,
            components=components,
        )

    score = round(sum(available_scores) / len(available_scores))
    if len(available_scores) < 3:
        label = "Limited data"
    elif score >= 75:
        label = "Mostly favorable current signals"
    elif score >= 50:
        label = "Mixed-to-favorable current signals"
    elif score >= 25:
        label = "Mixed-to-cautious current signals"
    else:
        label = "Mostly cautious current signals"
    return FinancialSnapshotScore(
        score=score,
        label=label,
        available_components=len(available_scores),
        components=components,
    )


def explain_8k_item(item_number: str) -> str:
    """Explain common 8-K item numbers without interpreting the filing itself."""
    explanations = {
        "1.01": "The company entered into an important agreement.",
        "1.02": "An important company agreement ended.",
        "2.01": "The company completed an acquisition or sold significant assets.",
        "2.02": "The company announced financial results or operating performance.",
        "2.03": "The company took on a significant financial obligation.",
        "2.05": "The company committed to a restructuring or exit plan.",
        "2.06": "The company expects a significant reduction in an asset's recorded value.",
        "3.01": "The company received or reported a stock-exchange listing notice.",
        "4.01": "The company changed or dismissed its accounting firm.",
        "4.02": "Previously issued financial statements should no longer be relied upon.",
        "5.01": "Control of the company changed.",
        "5.02": "A director or senior executive changed, or related compensation was updated.",
        "5.03": "The company changed its charter or bylaws.",
        "5.07": "The company reported the results of a shareholder vote.",
        "7.01": "The company shared information publicly under Regulation FD.",
        "8.01": "The company reported another event it considered important.",
        "9.01": "The filing includes financial statements or supporting exhibits.",
    }
    normalized = str(item_number).strip()
    return explanations.get(
        normalized,
        "This numbered category identifies the type of event reported to the SEC.",
    )


def build_filing_preview(text: str, max_sentences: int = 2) -> str:
    """Create a short extractive preview without inventing or paraphrasing claims."""
    if max_sentences < 1:
        raise ValueError("max_sentences must be at least 1")

    useful_lines = []
    for raw_line in str(text).splitlines():
        line = " ".join(raw_line.split())
        if not line or re.fullmatch(r"Item\s+\d+[A-Z]?(?:\.\d+)?\.?[^.]*", line, re.I):
            continue
        if len(line) < 80 and not line.endswith(('.', '!', '?')):
            continue
        useful_lines.append(line)

    sentences = [
        sentence
        for line in useful_lines
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z“\"])", line)
    ]
    boilerplate = (
        "the following discussion should be read",
        "this item and other sections",
        "this item generally discusses",
        "forward-looking statements can",
        "this section should be read",
        "the company’s fiscal year",
        "the company's fiscal year",
        "is the company’s line",
        "is the company's line",
        "the company assumes no obligation",
        "unless otherwise stated",
    )
    selected = [
        sentence.strip()
        for sentence in sentences
        if len(sentence.split()) >= 8
        and not sentence.lower().startswith(boilerplate)
    ][:max_sentences]
    return " ".join(selected) if selected else "No short preview is available."


def detect_news_topics(
    articles: tuple[RecentNewsArticle, ...],
) -> tuple[NewsTopic, ...]:
    """Count broad topics using headline keywords, without sentiment analysis."""
    topic_keywords = {
        "Products and services": (
            "product", "iphone", "ipad", "mac", "watch", "airtag", "app store",
            "software", "service", "launch", "trade-in",
        ),
        "Legal and regulation": (
            "lawsuit", "suit", "court", "judge", "legal", "antitrust", "regulat",
            "patent", "trade secret",
        ),
        "Financial results": (
            "earnings", "revenue", "profit", "sales", "results", "quarter",
        ),
        "Investor commentary": (
            "stock", "shares", "stake", "buy", "sell", "forecast", "analyst",
            "investor", "holding", "etf",
        ),
        "Leadership and organization": (
            "ceo", "executive", "director", "leadership", "appoint", "resign",
        ),
    }
    counts = []
    for label, keywords in topic_keywords.items():
        count = sum(
            any(keyword in article.title.lower() for keyword in keywords)
            for article in articles
        )
        if count:
            counts.append(NewsTopic(label, count))
    return tuple(sorted(counts, key=lambda topic: (-topic.article_count, topic.label)))


def _read_json(path: Path, description: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DashboardError(f"Could not read saved {description}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DashboardError(f"Saved {description} must be a JSON object.")
    return payload


def _read_news_results(
    path: Path,
) -> tuple[int, tuple[RecentNewsArticle, ...]]:
    """Read the count and article details from normalized saved news."""
    try:
        payload = _read_json(path, "news results")
        article_count = payload["article_count"]
        articles = tuple(
            RecentNewsArticle(
                title=str(article["title"]).strip(),
                publisher=str(article["publisher"]).strip(),
                published_at=str(article["published_at"]).strip(),
                url=str(article["url"]).strip(),
            )
            for article in payload["articles"]
        )
    except (KeyError, TypeError) as exc:
        raise DashboardError(f"Could not read saved news results: {exc}") from exc

    if (
        not isinstance(article_count, int)
        or article_count < 0
        or article_count != len(articles)
    ):
        raise DashboardError("Saved news results contain an invalid article count.")
    if any(
        not all((article.title, article.publisher, article.published_at))
        or not article.url.startswith(("https://", "http://"))
        for article in articles
    ):
        raise DashboardError("Saved news results contain an invalid article.")
    return article_count, articles


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
        optional_indexed = {
            name: {record["period_end"]: record for record in metrics.get(name, [])}
            for name in ("capital_expenditures", "eps")
        }
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
                capital_expenditures=(
                    optional_indexed["capital_expenditures"].get(period, {}).get("value")
                ),
                eps=optional_indexed["eps"].get(period, {}).get("value"),
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
            row.capital_expenditures,
            row.eps,
        )
        if any(
            value is not None
            and (isinstance(value, bool) or not isinstance(value, (int, float)))
            for value in values
        ):
            raise DashboardError("Saved financial history contains a non-numeric value.")
    return rows


def _read_annual_sections(paths: tuple[Path, ...]) -> AnnualFilingSections:
    """Read the three saved sections extracted from the latest 10-K."""
    required_files = {
        "business.txt": "business",
        "risk_factors.txt": "risk_factors",
        "mda.txt": "mda",
    }
    files_by_name = {Path(path).name: Path(path) for path in paths}
    contents: dict[str, str] = {
        field_name: "" for field_name in required_files.values()
    }
    try:
        for filename, field_name in required_files.items():
            if filename not in files_by_name or not files_by_name[filename].is_file():
                continue
            content = files_by_name[filename].read_text(encoding="utf-8").strip()
            contents[field_name] = content
    except OSError as exc:
        raise DashboardError(f"Could not read saved 10-K sections: {exc}") from exc

    return AnnualFilingSections(**contents)


def _read_quarterly_sections(paths: tuple[Path, ...]) -> QuarterlyFilingSections:
    """Read the two saved sections extracted from the latest 10-Q."""
    required_files = {
        "risk_factors.txt": "risk_factors",
        "mda.txt": "mda",
    }
    files_by_name = {Path(path).name: Path(path) for path in paths}
    contents: dict[str, str] = {
        field_name: "" for field_name in required_files.values()
    }
    try:
        for filename, field_name in required_files.items():
            if filename not in files_by_name or not files_by_name[filename].is_file():
                continue
            content = files_by_name[filename].read_text(encoding="utf-8").strip()
            contents[field_name] = content
    except OSError as exc:
        raise DashboardError(f"Could not read saved 10-Q sections: {exc}") from exc

    return QuarterlyFilingSections(**contents)


def _read_event_manifest(path: Path) -> tuple[RecentEventFiling, ...]:
    """Read recent 8-K filing metadata and extracted item text."""
    payload = _read_json(path, "8-K event manifest")
    try:
        filings = []
        for filing in payload["filings"]:
            items = []
            for item in filing["items"]:
                text = Path(item["text_path"]).read_text(encoding="utf-8").strip()
                items.append(
                    RecentEventItem(
                        item_number=str(item["item_number"]).strip(),
                        title=str(item.get("title", "")).strip(),
                        text=text,
                    )
                )
            filings.append(
                RecentEventFiling(
                    filing_date=str(filing["filing_date"]).strip(),
                    accession_number=str(filing["accession_number"]).strip(),
                    document_url=str(filing["document_url"]).strip(),
                    items=tuple(items),
                )
            )
    except (KeyError, TypeError, OSError) as exc:
        raise DashboardError(f"Could not read saved 8-K events: {exc}") from exc

    if not filings:
        raise DashboardError("Saved 8-K event manifest contains no filings.")
    if any(
        not all(
            (filing.filing_date, filing.accession_number, filing.document_url)
        )
        or not filing.document_url.startswith(("https://", "http://"))
        or not filing.items
        or any(not item.item_number or not item.text for item in filing.items)
        for filing in filings
    ):
        raise DashboardError("Saved 8-K event manifest contains an invalid filing.")
    return tuple(filings)


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
    except PipelineError as exc:
        raise DashboardError(str(exc)) from exc

    warnings: list[str] = []
    quarterly = None
    news = None
    events = None

    try:
        notify("Starting quarterly data collection")
        quarterly = run_quarterly_pipeline(
            ticker, progress=lambda message: notify(f"Quarterly data: {message}")
        )
    except PipelineError as exc:
        warnings.append(f"Quarterly filing unavailable: {exc}")

    try:
        notify("Starting recent news collection")
        news = run_news_pipeline(
            ticker, progress=lambda message: notify(f"News: {message}")
        )
    except (PipelineError, NewsPipelineError) as exc:
        warnings.append(f"Recent news unavailable: {exc}")

    try:
        notify("Starting recent 8-K event collection")
        events = run_events_pipeline(
            ticker, progress=lambda message: notify(f"8-K events: {message}")
        )
    except (PipelineError, NewsPipelineError) as exc:
        warnings.append(f"Recent 8-K events unavailable: {exc}")

    article_count, recent_news = (
        _read_news_results(news.articles_path) if news is not None else (0, ())
    )
    annual_sections = _read_annual_sections(getattr(annual, "section_paths", ()))
    quarterly_sections = (
        _read_quarterly_sections(getattr(quarterly, "section_paths", ()))
        if quarterly is not None
        else QuarterlyFilingSections(risk_factors="", mda="")
    )
    event_manifest = getattr(events, "manifest_path", None)
    recent_events = (
        _read_event_manifest(event_manifest)
        if isinstance(event_manifest, Path)
        else ()
    )
    missing_annual = [
        label
        for label, content in (
            ("Business", annual_sections.business),
            ("Risk Factors", annual_sections.risk_factors),
            ("MD&A", annual_sections.mda),
        )
        if not content
    ]
    if missing_annual:
        warnings.append(
            "Some 10-K sections could not be extracted: "
            + ", ".join(missing_annual)
            + "."
        )
    return DashboardSummary(
        ticker=annual.company.ticker,
        company_name=annual.company.name,
        cik=annual.company.cik,
        latest_10k_date=annual.filing.filing_date,
        latest_10q_date=(
            quarterly.filing.filing_date if quarterly is not None else "Not available"
        ),
        news_article_count=article_count,
        financials=_read_financial_overview(
            annual.latest_facts_path, annual.derived_metrics_path
        ),
        financial_history=_read_financial_history(annual.history_path),
        recent_news=recent_news,
        annual_sections=annual_sections,
        quarterly_sections=quarterly_sections,
        recent_events=recent_events,
        data_warnings=tuple(warnings),
    )


__all__ = [
    "DashboardError",
    "DashboardSummary",
    "TickerEligibility",
    "AnnualFilingSections",
    "QuarterlyFilingSections",
    "RecentEventFiling",
    "RecentEventItem",
    "FinancialOverview",
    "FinancialInsight",
    "ScoreComponent",
    "FinancialSnapshotScore",
    "FinancialHistoryRow",
    "RecentNewsArticle",
    "NewsTopic",
    "build_financial_insights",
    "build_financial_snapshot_score",
    "check_ticker_eligibility",
    "build_filing_preview",
    "detect_news_topics",
    "explain_8k_item",
    "analyze_ticker",
]

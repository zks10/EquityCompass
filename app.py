"""Equity Compass Streamlit dashboard."""

import base64
import html
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from finance_news.dashboard import (
    DashboardError,
    DashboardSummary,
    FinancialSnapshotScore,
    RecentNewsArticle,
    analyze_ticker,
    build_filing_preview,
    build_financial_snapshot_score,
    check_ticker_eligibility,
    detect_news_topics,
    explain_8k_item,
)
from finance_news.market_data import (
    MarketDataError,
    MarketOverview,
    fetch_market_overview,
)
from finance_news.news_score import ArticleSignal, calculate_news_score
from finance_news.short_term_score import calculate_short_term_score
from finance_news.sec_companies import CompanyLookupError, resolve_company_query

FINANCIALS_SCHEMA_VERSION = 2
POPULAR_TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
DASHBOARD_SECTIONS = {
    "overview": "Overview",
    "financials": "Financials",
    "filings": "Filings",
    "news": "News & Events",
}
LOGO_PATH = Path(__file__).with_name("assets") / "equity-compass-logo-cropped.png"
FAVICON_PATH = Path(__file__).with_name("assets") / "equity-compass-favicon.png"
LOGO_DATA_URI = (
    "data:image/png;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
)
FAVICON_DATA_URI = (
    "data:image/png;base64," + base64.b64encode(FAVICON_PATH.read_bytes()).decode("ascii")
)


def format_usd(value: int | float) -> str:
    """Format large SEC values in a compact, readable form."""
    absolute_value = abs(value)
    if absolute_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.1f}B"
    if absolute_value >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    return f"${value:,.0f}"


def format_percent(value: float | None) -> str:
    """Format a calculated percentage while preserving unavailable values."""
    return "N/A" if value is None else f"{value:.1f}%"


def format_eps(value: int | float | None) -> str:
    """Format annual earnings per share."""
    return "N/A" if value is None else f"${value:,.2f}"


def financial_history_value(row, field: str) -> int | float | None:
    """Read new metrics safely from rows cached before Financials v1."""
    if field == "free_cash_flow":
        stored_value = getattr(row, "free_cash_flow", None)
        if stored_value is not None:
            return stored_value
        capital_expenditures = getattr(row, "capital_expenditures", None)
        if capital_expenditures is None:
            return None
        return row.operating_cash_flow - abs(capital_expenditures)
    return getattr(row, field, None)


def describe_financial_trend(history, field: str) -> tuple[str, str]:
    """Summarize a five-year series without turning it into a score."""
    rows = sorted(history, key=lambda row: row.fiscal_year)
    points = [
        (row.fiscal_year, financial_history_value(row, field)) for row in rows
    ]
    available = [(year, value) for year, value in points if value is not None]
    if len(available) < 2:
        if available:
            year, value = available[-1]
            latest = format_eps(value) if field == "eps" else format_usd(value)
            return latest, f"Latest available annual figure ({year})."
        return "Data unavailable", "No comparable annual SEC figures were found."

    start_year, start_value = available[0]
    end_year, end_value = available[-1]
    if start_value == 0:
        change_text = f"{format_usd(end_value) if field != 'eps' else format_eps(end_value)} latest"
    else:
        change = (end_value - start_value) / abs(start_value) * 100
        direction = "up" if change > 0 else "down" if change < 0 else "flat"
        change_text = f"{direction} {abs(change):.1f}%"
    annual_moves = [
        current - previous
        for (_, previous), (_, current) in zip(available, available[1:])
    ]
    improving_years = sum(move > 0 for move in annual_moves)
    consistency = f"rose in {improving_years} of {len(annual_moves)} year-to-year periods"
    return change_text, f"From {start_year} to {end_year}; {consistency}."


def financial_series_change(history, field: str) -> float | None:
    """Return the full-period percentage change for one available series."""
    values = [
        financial_history_value(row, field)
        for row in sorted(history, key=lambda row: row.fiscal_year)
    ]
    available = [value for value in values if value is not None]
    if len(available) < 2 or available[0] == 0:
        return None
    return (available[-1] - available[0]) / abs(available[0]) * 100


def build_financial_takeaway(history, field: str) -> str:
    """Connect each chart to another financial signal in one useful sentence."""
    rows = sorted(history, key=lambda row: row.fiscal_year)
    latest = rows[-1]
    revenue_change = financial_series_change(rows, "revenue")
    income_change = financial_series_change(rows, "net_income")

    if field == "revenue":
        if revenue_change is None or income_change is None:
            return "Sales provide the starting point for the company's financial story."
        relationship = "faster" if income_change > revenue_change else "slower"
        return (
            f"Profit changed {relationship} than sales over the same period "
            f"({income_change:+.1f}% versus {revenue_change:+.1f}%)."
        )
    if field == "net_income":
        earliest = rows[0]
        if earliest.revenue == 0 or latest.revenue == 0:
            return "Profit shows how much of the company's sales remained after costs."
        first_margin = earliest.net_income / earliest.revenue * 100
        latest_margin = latest.net_income / latest.revenue * 100
        direction = "expanded" if latest_margin > first_margin else "narrowed"
        return (
            f"Net margin {direction} from {first_margin:.1f}% to "
            f"{latest_margin:.1f}% of revenue."
        )
    if field == "eps":
        eps_change = financial_series_change(rows, "eps")
        if eps_change is None or income_change is None:
            return "Per-share earnings show how much profit growth reached each share."
        relationship = "outpaced" if eps_change > income_change else "trailed"
        return (
            f"Per-share earnings {relationship} total profit growth "
            f"({eps_change:+.1f}% versus {income_change:+.1f}%)."
        )
    latest_fcf = financial_history_value(latest, "free_cash_flow")
    if latest_fcf is None or latest.net_income == 0:
        return "Cash generation shows how much reported profit was backed by cash."
    conversion = latest_fcf / latest.net_income * 100
    return (
        f"Latest free cash flow equaled {conversion:.1f}% of net income, "
        "linking reported profit to cash generation."
    )


def assess_financial_signal(history, field: str) -> tuple[str, str]:
    """Classify visible five-year evidence without creating an investment score."""
    rows = sorted(history, key=lambda row: row.fiscal_year)
    change = financial_series_change(rows, field)
    if change is None:
        return "Context limited", "neutral"

    if field == "revenue":
        values = [financial_history_value(row, field) for row in rows]
        moves = [current - previous for previous, current in zip(values, values[1:])]
        if change > 0 and sum(move > 0 for move in moves) >= len(moves) / 2:
            return "Supportive", "positive"
        if change > 0:
            return "Mixed", "mixed"
        return "Caution", "caution"

    if field == "net_income":
        first_margin = rows[0].net_income / rows[0].revenue if rows[0].revenue else None
        latest_margin = rows[-1].net_income / rows[-1].revenue if rows[-1].revenue else None
        margin_held = (
            first_margin is not None
            and latest_margin is not None
            and latest_margin >= first_margin
        )
        if change > 0 and margin_held:
            return "Supportive", "positive"
        if change > 0 or margin_held:
            return "Mixed", "mixed"
        return "Caution", "caution"

    if field == "eps":
        income_change = financial_series_change(rows, "net_income")
        if change > 0 and income_change is not None and change >= income_change:
            return "Supportive", "positive"
        if change > 0:
            return "Mixed", "mixed"
        return "Caution", "caution"

    latest_fcf = financial_history_value(rows[-1], "free_cash_flow")
    fcf_values = [financial_history_value(row, "free_cash_flow") for row in rows]
    fcf_moves = [
        current - previous
        for previous, current in zip(fcf_values, fcf_values[1:])
        if previous is not None and current is not None
    ]
    consistent_growth = (
        bool(fcf_moves) and sum(move > 0 for move in fcf_moves) > len(fcf_moves) / 2
    )
    if latest_fcf is not None and latest_fcf > 0 and change > 0 and consistent_growth:
        return "Supportive", "positive"
    if latest_fcf is not None and latest_fcf > 0:
        return "Mixed", "mixed"
    return "Caution", "caution"


def build_metric_trend_figure(
    history, field: str, color: str, money: bool = True
) -> go.Figure:
    """Build one focused five-year trend chart for the Financials page."""
    rows = sorted(history, key=lambda row: row.fiscal_year)
    years = [str(row.fiscal_year) for row in rows]
    values = [financial_history_value(row, field) for row in rows]
    scaled = [
        value / 1_000_000_000 if money and value is not None else value
        for value in values
    ]
    figure = go.Figure(
        go.Scatter(
            x=years,
            y=scaled,
            mode="lines+markers",
            line={"color": color, "width": 3},
            marker={"color": color, "size": 8},
            fill="tozeroy",
            fillcolor="rgba(46, 111, 229, 0.07)",
            hovertemplate=(
                "<b>$%{y:.1f}B</b><extra></extra>"
                if money
                else "<b>$%{y:.2f}</b><extra></extra>"
            ),
        )
    )
    figure.update_layout(
        height=245,
        margin={"l": 8, "r": 12, "t": 10, "b": 15},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        hovermode="x unified",
        xaxis={"showgrid": False, "fixedrange": True, "tickfont": {"color": "#6F7784"}},
        yaxis={
            "tickprefix": "$",
            "ticksuffix": "B" if money else "",
            "showgrid": True,
            "gridcolor": "rgba(120, 130, 150, 0.14)",
            "zeroline": True,
            "zerolinecolor": "rgba(120, 130, 150, 0.25)",
            "fixedrange": True,
            "tickfont": {"color": "#8A919D"},
        },
    )
    return figure


def describe_news_relevance(article: RecentNewsArticle) -> tuple[str, str, str, str]:
    """Add cautious, headline-only context without creating a news score."""
    headline = article.title.lower()
    topic_rules = (
        ("Earnings", ("earnings", "revenue", "profit", "quarter", "guidance", "eps"),
         "Updates expectations about recent performance or management's near-term outlook.", "High"),
        ("Company event", ("acquisition", "merger", "launch", "appoint", "ceo", "deal", "partnership"),
         "Describes a company-specific development that investors may reassess in the near term.", "High"),
        ("Regulation", ("regulator", "lawsuit", "court", "antitrust", "investigation", "sec ", "ban", "tariff"),
         "Flags a legal or policy development that may affect risk, costs, or operations.", "High"),
        ("Analyst view", ("analyst", "upgrade", "downgrade", "price target", "rating"),
         "Reflects a market participant's opinion, not a change in the company's fundamentals by itself.", "Medium"),
        ("Market context", ("stock", "shares", "market", "investor", "nasdaq", "dow "),
         "Provides near-term market context; price attention alone does not change business quality.", "Medium"),
    )
    for topic, keywords, explanation, relevance in topic_rules:
        if any(keyword in headline for keyword in keywords):
            return topic, explanation, relevance, "Headline signal"
    return (
        "Company update",
        "Adds recent context about the company; open the source to confirm the details and significance.",
        "Medium",
        "Headline signal",
    )


def format_news_timestamp(value: str) -> str:
    """Turn normalized ISO timestamps into a compact reader-friendly label."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.strftime("%b %d, %Y · %I:%M %p %Z").replace(" 0", " ")


def show_news_article(
    article: RecentNewsArticle, key: str, score_signal: ArticleSignal | None = None,
) -> None:
    """Display one normalized article as a scannable, headline-only card."""
    topic, explanation, relevance, cue_label = describe_news_relevance(article)
    safe_url = html.escape(article.url, quote=True)
    publisher_mark = "".join(
        word[0] for word in article.publisher.split()[:2] if word
    ).upper() or "N"
    image_url = html.escape(getattr(article, "image_url", ""), quote=True)
    article_image = (
        f'<div class="news-card-image"><span>{html.escape(publisher_mark)}</span>'
        f'<img src="{image_url}" alt="Preview image for {html.escape(article.title, quote=True)}" '
        'loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display=\'none\'"></div>'
        if image_url
        else ""
    )
    layout_class = "news-card-layout has-image" if image_url else "news-card-layout"
    # Keep the interpolated HTML line non-empty. A blank, indented line makes
    # Streamlit's Markdown parser end the HTML block and expose the markup.
    score_impact = "<!-- article not included in the score window -->"
    if score_signal is not None:
        impact = score_signal.direction.lower()
        impact_copy = f"Used in score · {score_signal.direction}"
        score_impact = (
            f'<span class="news-score-impact impact-{impact}" '
            f'title="Included in the fresh company-news component">{impact_copy}</span>'
        )
    st.markdown(
        f"""
        <article class="news-card">
          <div class="{layout_class}">
            <div class="news-card-content">
              <div class="news-card-meta">
                <span class="news-source">{html.escape(article.publisher)}</span>
                <span>{html.escape(format_news_timestamp(article.published_at))}</span>
              </div>
              <h3>{html.escape(article.title)}</h3>
              <p>{html.escape(explanation)}</p>
              <div class="news-card-footer">
                <div class="news-cues">
                  <span class="news-topic">{html.escape(topic)}</span>
                  <span class="news-relevance news-relevance-{relevance.lower()}">{cue_label}: {relevance} relevance</span>
                  {score_impact}
                </div>
                <a class="news-read-link" href="{safe_url}" target="_blank" rel="noopener noreferrer" aria-label="Read article: {html.escape(article.title, quote=True)}">Read source <span>↗</span></a>
              </div>
            </div>
            {article_image}
          </div>
        </article>
        """,
        unsafe_allow_html=True,
    )


def build_financial_history_figure(history) -> go.Figure:
    """Build a five-year revenue, profit, and operating-cash-flow chart."""
    rows = sorted(history, key=lambda row: row.fiscal_year)
    years = [str(row.fiscal_year) for row in rows]
    revenue = [row.revenue / 1_000_000_000 for row in rows]
    net_income = [row.net_income / 1_000_000_000 for row in rows]
    cash_flow = [row.operating_cash_flow / 1_000_000_000 for row in rows]
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=years,
            y=revenue,
            name="Revenue",
            marker={"color": "rgba(46, 111, 229, 0.42)", "cornerradius": 5},
            hovertemplate="Revenue<br><b>$%{y:.1f}B</b><extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=years,
            y=net_income,
            name="Net income",
            mode="lines+markers",
            line={"color": "#0A8F6A", "width": 3},
            marker={"size": 8, "color": "#0A8F6A"},
            hovertemplate="Net income<br><b>$%{y:.1f}B</b><extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=years,
            y=cash_flow,
            name="Operating cash flow",
            mode="lines+markers",
            line={"color": "#7A5AF8", "width": 3},
            marker={"size": 8, "color": "#7A5AF8"},
            hovertemplate="Operating cash flow<br><b>$%{y:.1f}B</b><extra></extra>",
        )
    )
    figure.update_layout(
        height=315,
        margin={"l": 8, "r": 12, "t": 45, "b": 20},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        legend={
            "orientation": "h",
            "x": 0,
            "y": 1.16,
            "font": {"size": 11},
        },
        bargap=0.38,
        xaxis={
            "showgrid": False,
            "zeroline": False,
            "fixedrange": True,
            "tickfont": {"size": 11, "color": "#6F7784"},
        },
        yaxis={
            "tickprefix": "$",
            "ticksuffix": "B",
            "showgrid": True,
            "gridcolor": "rgba(120, 130, 150, 0.14)",
            "zeroline": False,
            "fixedrange": True,
            "tickfont": {"size": 10, "color": "#8A919D"},
        },
    )
    return figure


@st.cache_data(ttl=900, show_spinner=False)
def load_market_overview(ticker: str) -> MarketOverview:
    """Cache market prices briefly so tab reruns remain quick."""
    return fetch_market_overview(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def load_ticker_eligibility(ticker: str):
    """Cache the quick SEC eligibility check for repeated searches."""
    return check_ticker_eligibility(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def resolve_search_query(query: str):
    """Resolve a company-name-or-ticker search before starting analysis."""
    return resolve_company_query(query)


@st.cache_resource(show_spinner=False)
def dashboard_analysis_cache() -> dict[str, DashboardSummary]:
    """Store completed workspaces without caching their loading-screen effects."""
    return {}


def load_dashboard_analysis(ticker: str, progress=None) -> DashboardSummary:
    """Reuse completed company data while keeping progress UI outside the cache."""
    normalized_ticker = ticker.strip().upper()
    completed_workspaces = dashboard_analysis_cache()
    if normalized_ticker not in completed_workspaces:
        completed_workspaces[normalized_ticker] = analyze_ticker(
            normalized_ticker, progress=progress
        )
    return completed_workspaces[normalized_ticker]


def remember_dashboard_section(state_key: str) -> None:
    """Keep the selected company tab in the shareable page URL."""
    selected_label = st.session_state.get(state_key, "Overview")
    selected_slug = next(
        (
            slug
            for slug, label in DASHBOARD_SECTIONS.items()
            if label == selected_label
        ),
        "overview",
    )
    st.query_params["section"] = selected_slug


def select_price_points(market: MarketOverview, period: str):
    """Select the appropriate daily or intraday points for one chart range."""
    intraday = pd.DataFrame(
        [
            {"date": point.date, "close": point.close}
            for point in getattr(market, "intraday_points", ())
        ]
    )
    daily = pd.DataFrame(
        [{"date": point.date, "close": point.close} for point in market.points]
    )
    frame = intraday if period == "1D" and not intraday.empty else daily
    frame["date"] = pd.to_datetime(frame["date"])
    end = frame["date"].max()
    if period == "1D":
        start = end.normalize()
    elif period == "1M":
        start = end - pd.DateOffset(months=1)
    elif period == "6M":
        start = end - pd.DateOffset(months=6)
    elif period == "YTD":
        start = pd.Timestamp(year=end.year, month=1, day=1, tz=end.tz)
    elif period == "1Y":
        start = end - pd.DateOffset(years=1)
    else:
        start = frame["date"].min()
    selected = frame.loc[frame["date"] >= start].copy()
    return selected if len(selected) >= 2 else frame.tail(2)


def build_price_figure(market: MarketOverview, period: str) -> go.Figure:
    """Build a price chart with visible high, low, and average reference points."""
    selected = select_price_points(market, period)
    dates = selected["date"]
    prices = selected["close"]
    x_span = dates.max() - dates.min()
    x_padding = max(x_span * 0.035, pd.Timedelta(minutes=10))
    price_low = float(prices.min())
    price_high = float(prices.max())
    price_span = price_high - price_low
    y_padding = max(price_span * 0.18, price_high * 0.004)
    plotted_dates: list[object] = []
    plotted_prices: list[float | None] = []
    previous_date = None
    for date, price in zip(dates, prices):
        if (
            period == "1D"
            and previous_date is not None
            and date - previous_date > pd.Timedelta(minutes=30)
        ):
            # Do not draw a fake line while the market is closed.
            plotted_dates.append(None)
            plotted_prices.append(None)
        plotted_dates.append(date)
        plotted_prices.append(float(price))
        previous_date = date
    positive = market.price_change >= 0
    line_color = "#0A8F6A" if positive else "#D9304F"
    fill_color = "rgba(10, 143, 106, 0.13)" if positive else "rgba(217, 48, 79, 0.12)"

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=plotted_dates,
            y=plotted_prices,
            mode="lines",
            fill="tozeroy",
            line={"color": line_color, "width": 2.5},
            fillcolor=fill_color,
            hovertemplate="%{x|%b %d, %Y}<br><b>$%{y:,.2f}</b><extra></extra>",
        )
    )
    high_index = prices.idxmax()
    low_index = prices.idxmin()
    for label, index, color in (
        ("High", high_index, "#0A8F6A"),
        ("Low", low_index, "#D9304F"),
    ):
        figure.add_trace(
            go.Scatter(
                x=[dates.loc[index]],
                y=[prices.loc[index]],
                mode="markers+text",
                marker={"size": 10, "color": color, "line": {"color": "white", "width": 2}},
                text=[f"{label}  ${prices.loc[index]:,.2f}"],
                textposition="top center" if label == "High" else "bottom center",
                hoverinfo="skip",
            )
        )
    average = float(prices.mean())
    figure.add_hline(
        y=average,
        line_dash="dot",
        line_color="#7A8291",
        line_width=1.5,
        annotation_text=f"Average ${average:,.2f}",
        annotation_position="bottom right",
    )
    figure.update_layout(
        height=355,
        margin={"l": 4, "r": 12, "t": 18, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        showlegend=False,
        xaxis={
            "showgrid": False,
            "rangeslider": {"visible": False},
            "range": [dates.min() - x_padding, dates.max() + x_padding],
            "fixedrange": True,
        },
        yaxis={
            "side": "right",
            "tickprefix": "$",
            "showgrid": True,
            "gridcolor": "rgba(120,130,150,0.14)",
            "zeroline": False,
            "range": [price_low - y_padding, price_high + y_padding],
            "fixedrange": True,
        },
    )
    return figure


@st.fragment
def show_price_chart(market: MarketOverview, ticker: str) -> None:
    """Redraw only the chart when its range changes, preserving page position."""
    chart_period = st.segmented_control(
        "Price range",
        ["1D", "1M", "6M", "YTD", "1Y", "5Y"],
        default="1D",
        key=f"price-range-{ticker}",
        label_visibility="collapsed",
    )
    st.plotly_chart(
        build_price_figure(market, chart_period or "1D"),
        width="stretch",
        config={"displayModeBar": False},
    )


def show_overview(summary: DashboardSummary) -> None:
    """Display company context, market history, and financial signals."""
    financials = summary.financials
    snapshot_score = build_financial_snapshot_score(financials)
    try:
        market = load_market_overview(summary.ticker)
    except MarketDataError:
        market = None

    st.subheader("Company at a glance")
    business_summary = build_filing_preview(
        summary.annual_sections.business, max_sentences=1
    )
    if len(business_summary) > 300:
        business_summary = business_summary[:297].rsplit(" ", 1)[0] + "…"
    sector = market.sector if market and market.sector else "Not available"
    industry = market.industry if market and market.industry else "Not available"
    headquarters = (
        market.headquarters if market and market.headquarters else "Not available"
    )
    employees = (
        f"{market.employees:,}" if market and market.employees else "Not available"
    )
    website_link = (
        f'<a class="profile-website" href="{html.escape(market.website, quote=True)}" '
        'target="_blank">Official website ↗</a>'
        if market and market.website
        else ""
    )
    logo_url = (
        "https://www.google.com/s2/favicons?sz=128&domain_url="
        f"{quote(market.website, safe='')}"
        if market and market.website
        else ""
    )
    company_initials = "".join(
        word[0] for word in summary.company_name.split()[:2] if word
    ).upper()
    company_mark = (
        f'<img src="{html.escape(logo_url, quote=True)}" '
        f'alt="{html.escape(summary.company_name, quote=True)} logo" '
        'onerror="this.style.display=\'none\'">'
        if logo_url
        else ""
    )
    st.markdown(
        f"""
        <div class="profile-panel">
          <div class="profile-summary">
            <div class="profile-mark"><span>{html.escape(company_initials)}</span>{company_mark}</div>
            <div class="profile-copy">
              <div class="highlight-label">WHAT THE COMPANY DOES</div>
              <div class="profile-description">{html.escape(business_summary)}</div>
              {website_link}
            </div>
          </div>
          <div class="profile-facts">
            <div class="profile-fact"><div class="profile-fact-heading"><i>◈</i><span>SECTOR</span></div><strong>{html.escape(sector)}</strong></div>
            <div class="profile-fact"><div class="profile-fact-heading"><i>▦</i><span>INDUSTRY</span></div><strong>{html.escape(industry)}</strong></div>
            <div class="profile-fact"><div class="profile-fact-heading"><i>⌖</i><span>HEADQUARTERS</span></div><strong>{html.escape(headquarters)}</strong></div>
            <div class="profile-fact"><div class="profile-fact-heading"><i>●</i><span>EMPLOYEES</span></div><strong>{employees}</strong></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if market is None:
        st.info("The price chart is temporarily unavailable. The company research below is still complete.")
    else:
        delta = f"{market.price_change:+.2f} ({market.price_change_percent:+.2f}%)"
        st.metric(
            "Latest close (USD)",
            f"${market.latest_price:,.2f}",
            delta=delta,
        )
        st.caption(f"As of {market.as_of} · Daily closing prices")
        show_price_chart(market, summary.ticker)

    with st.container(border=True):
        st.subheader("Financial Compass")
        available_components = [
            component for component in snapshot_score.components if component.score is not None
        ]
        strongest = (
            max(available_components, key=lambda component: component.score or 0)
            if available_components
            else None
        )
        weakest = (
            min(available_components, key=lambda component: component.score or 0)
            if available_components
            else None
        )
        friendly_names = {
            "Revenue growth": "Revenue growth",
            "Net profit margin": "Profitability",
            "Liabilities / assets": "Balance sheet",
            "Operating cash flow margin": "Cash generation",
        }

        compass_column, summary_column = st.columns([1.35, 1], gap="large")
        with compass_column:
            st.markdown("**Five-year fundamentals**")
            st.plotly_chart(
                build_financial_history_figure(summary.financial_history),
                width="stretch",
                config={"displayModeBar": False},
            )
        with summary_column:
            score_value = (
                "N/A" if snapshot_score.score is None else f"{snapshot_score.score}/100"
            )
            st.markdown(
                f"""
                <div class="score-summary">
                  <div class="score-summary-label">OVERALL FINANCIAL SIGNAL</div>
                  <div class="score-summary-value">{score_value}</div>
                  <div class="score-summary-title">{html.escape(snapshot_score.label.replace(' current signals', ''))}</div>
                  <div class="score-summary-divider"></div>
                  <div class="score-summary-item"><span class="summary-dot summary-strong"></span><div><small>STRONGEST SIGNAL</small><strong>{html.escape(friendly_names[strongest.name] if strongest else 'Unavailable')}</strong></div></div>
                  <div class="score-summary-item"><span class="summary-dot summary-watch"></span><div><small>NEEDS THE MOST CONTEXT</small><strong>{html.escape(friendly_names[weakest.name] if weakest else 'Unavailable')}</strong></div></div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        growth = financials.revenue_growth_percent
        margin = financials.net_profit_margin_percent
        liabilities = financials.liabilities_to_assets_percent
        cash_margin = financials.operating_cash_flow_margin_percent
        factor_details = [
            (
                "Revenue growth",
                "Revenue data unavailable."
                if growth is None
                else f"Sales {'rose' if growth >= 0 else 'fell'} {abs(growth):.1f}% from last year.",
                snapshot_score.components[0],
            ),
            (
                "Net profit margin",
                "Margin data unavailable."
                if margin is None
                else f"Kept ${margin:.1f} as net profit per $100 of sales.",
                snapshot_score.components[1],
            ),
            (
                "Liabilities / assets",
                "Balance-sheet data unavailable."
                if liabilities is None
                else f"Had ${liabilities:.1f} of liabilities per $100 of assets.",
                snapshot_score.components[2],
            ),
            (
                "Operating cash flow margin",
                "Cash-flow data unavailable."
                if cash_margin is None
                else f"Generated ${cash_margin:.1f} of operating cash per $100 of sales.",
                snapshot_score.components[3],
            ),
        ]
        factor_rows = []
        for title, current_result, component in factor_details:
            component_score = component.score
            if component_score is None:
                tone, reading, displayed_score, bar_width = "neutral", "Unavailable", "N/A", 0
            elif component_score >= 71:
                tone, reading, displayed_score, bar_width = "strong", "Strong", str(component_score), component_score
            elif component_score >= 41:
                tone, reading, displayed_score, bar_width = "middle", "Mixed", str(component_score), component_score
            else:
                tone, reading, displayed_score, bar_width = "watch", "Weak", str(component_score), component_score
            factor_rows.append(
                f'<tr class="factor-row"><td class="factor-name">{html.escape(title)}</td>'
                f'<td class="factor-metric">{html.escape(current_result)}</td>'
                f'<td class="factor-score-cell"><div class="inline-score"><div class="inline-track"><span class="inline-fill factor-{tone}" style="width:{bar_width}%"></span></div><strong>{displayed_score}</strong></div></td>'
                f'<td class="factor-assessment"><span class="factor-reading factor-{tone}">{reading}</span></td></tr>'
            )
        st.markdown("#### Signal components")
        score_help = """
          <span class="score-popover">
            <strong>How the score works</strong>
            <small>We score four financial signals from 0 to 100, then average them equally. Higher growth, profit, and cash generation help the score; a lower liabilities share helps the balance-sheet score.</small>
          </span>
        """
        st.markdown(
            f'<table class="factor-table"><thead><tr><th>Factor</th><th>Current result</th><th>Score <span class="score-info" tabindex="0" aria-label="How scores are calculated">?{score_help}</span></th><th>Assessment</th></tr></thead><tbody>{"".join(factor_rows)}</tbody></table>',
            unsafe_allow_html=True,
        )
        st.caption(
            "Latest annual SEC figures. Excludes valuation and forward estimates."
        )


def show_financials(summary: DashboardSummary) -> None:
    """Explain the company's financial story through four investor questions."""
    financials = summary.financials
    history = summary.financial_history
    st.subheader("Financial story")
    st.caption(
        f"Fiscal year {financials.fiscal_year}, ended {financials.period_end} · "
        "Annual SEC figures"
    )

    latest_eps = financial_history_value(history[0], "eps")
    latest_fcf = financial_history_value(history[0], "free_cash_flow")
    story_cards = (
        ("1", "Is the business growing?", "Revenue", "revenue"),
        ("2", "Is growth becoming profit?", "Net income", "net_income"),
        ("3", "Are shareholders benefiting?", "Earnings per share", "eps"),
        ("4", "Is the profit backed by cash?", "Free cash flow", "free_cash_flow"),
    )
    available_story_cards = tuple(
        card
        for card in story_cards
        if any(
            financial_history_value(row, card[3]) is not None for row in history
        )
    )
    card_columns = st.columns(len(available_story_cards))
    for column, (number, question, label, field) in zip(
        card_columns, available_story_cards
    ):
        change_text, _ = describe_financial_trend(history, field)
        assessment, assessment_tone = assess_financial_signal(history, field)
        column.markdown(
            f"""
            <div class="financial-story-card">
              <span>{number}</span>
              <small>{html.escape(question)}</small>
              <strong>{html.escape(change_text)}</strong>
              <i class="financial-assessment financial-{assessment_tone}">{html.escape(assessment)}</i>
              <em>{html.escape(label)} · five years</em>
            </div>
            """,
            unsafe_allow_html=True,
        )

    metric_sections = (
        ("01", "Is the business growing?", "Revenue", "revenue", "#2E6FE5", True,
         format_usd(history[0].revenue)),
        ("02", "Is growth becoming profit?", "Net income", "net_income", "#0A8F6A", True,
         format_usd(history[0].net_income)),
        ("03", "Are shareholders benefiting?", "Earnings per share", "eps", "#D17A22", False,
         format_eps(latest_eps)),
        ("04", "Is the profit backed by cash?", "Free cash flow", "free_cash_flow", "#7A5AF8", True,
         format_usd(latest_fcf) if latest_fcf is not None else "N/A"),
    )
    for number, question, title, field, color, money, latest in metric_sections:
        values_available = any(
            financial_history_value(row, field) is not None for row in history
        )
        if not values_available:
            continue
        change_text, context_text = describe_financial_trend(history, field)
        takeaway = build_financial_takeaway(history, field)
        assessment, assessment_tone = assess_financial_signal(history, field)
        with st.container(border=True):
            st.markdown(
                f'<div class="financial-section-heading"><span>{number}</span><div>'
                f'<small>{html.escape(question)}</small><h3>{html.escape(title)}</h3>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
            chart_column, guide_column = st.columns([1.65, 1], gap="large")
            with chart_column:
                st.plotly_chart(
                    build_metric_trend_figure(history, field, color, money),
                    width="stretch",
                    config={"displayModeBar": False},
                    key=f"financial-trend-{field}",
                )
            with guide_column:
                st.markdown(
                    f"""
                    <div class="financial-answer">
                      <small>LATEST ANNUAL VALUE</small>
                      <strong>{html.escape(latest)}</strong>
                      <div class="financial-trend-label">{html.escape(change_text)}</div>
                      <div class="financial-assessment financial-{assessment_tone}">{html.escape(assessment)} evidence</div>
                      <p>{html.escape(context_text)}</p>
                      <hr>
                      <p class="financial-takeaway">{html.escape(takeaway)}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

    st.caption(
        "Free cash flow = operating cash flow − capital expenditures. Values are "
        "based on annual SEC filings and may use company-reported GAAP tags. "
        "Evidence labels reflect the company's five-year direction and financial "
        "relationships; they do not assess valuation or provide a buy/sell view."
    )


def show_filings(summary: DashboardSummary) -> None:
    """Turn filing sections into a concise briefing with source text on demand."""
    annual = summary.annual_sections
    quarterly = summary.quarterly_sections

    display_name = re.sub(
        r",?\s+(?:Inc\.?|Incorporated|Corporation|Corp\.?|Ltd\.?)$",
        "",
        summary.company_name,
        flags=re.IGNORECASE,
    )

    def readable_date(value: str) -> str:
        try:
            return time.strftime("%b %d, %Y", time.strptime(value, "%Y-%m-%d"))
        except ValueError:
            return value

    def has_any(text: str, terms: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(term in lowered for term in terms)

    def short_evidence(text: str, terms: tuple[str, ...] = ()) -> str:
        normalized = re.sub(r"\$(\d+)\s+\.(\d+)", r"$\1.\2", text)
        sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", normalized).strip())
        useful = [
            sentence for sentence in sentences
            if 55 <= len(sentence) <= 420
            and not has_any(sentence, ("can be found", "not included", "part ii", "item 7"))
        ]
        if terms and useful:
            useful.sort(
                key=lambda sentence: (
                    has_any(sentence, ("increase", "decrease", "higher", "lower", "grew", "declined")),
                    sum(term in sentence.lower() for term in terms),
                    bool(re.search(r"[$%]|\b\d+(?:\.\d+)?\b", sentence)),
                ),
                reverse=True,
            )
        if not useful:
            return build_filing_preview(text, max_sentences=1)
        sentence = useful[0]
        return sentence if len(sentence) <= 280 else sentence[:277].rsplit(" ", 1)[0] + "…"

    def evidence_excerpt(text: str, label: str) -> str:
        term_map = {
            "Business overview": ("revenue", "customer", "product", "service", "advertising", "sales"),
            "Risk factors": ("could", "may", "depend", "adverse", "risk"),
            "Management analysis": ("revenue", "sales", "margin", "operating income", "cash flow"),
            "Quarterly performance": ("revenue", "sales", "margin", "operating income", "cash flow"),
            "Quarterly risk update": ("could", "may", "depend", "adverse", "risk"),
        }
        normalized = re.sub(r"\$(\d+)\s+\.(\d+)", r"$\1.\2", text)
        sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", normalized).strip())
        excluded = (
            "forward-looking statement",
            "this item and other sections",
            "the following summarizes",
            "can be found in",
            "not included",
        )
        useful = [
            sentence for sentence in sentences
            if 55 <= len(sentence) <= 420 and not has_any(sentence, excluded)
        ]
        terms = term_map.get(label, ())
        if useful:
            prioritize_change = label in {"Management analysis", "Quarterly performance"}
            useful.sort(
                key=lambda sentence: (
                    has_any(sentence, ("increase", "decrease", "higher", "lower", "grew", "declined"))
                    if prioritize_change else False,
                    sum(term in sentence.lower() for term in terms),
                    bool(re.search(r"[$%]|\b\d+(?:\.\d+)?\b", sentence)),
                ),
                reverse=True,
            )
            excerpt = useful[0]
        else:
            excerpt = short_evidence(text, terms)
        excerpt = re.sub(
            r"^(?:Business\s+Company Background\s+|Risk Factors\s+|Management['’]s Discussion and Analysis(?: of Financial Condition and Results of Operations)?\s+)",
            "",
            excerpt,
            flags=re.IGNORECASE,
        )
        words = excerpt.split()
        for prefix_size in range(min(5, len(words) // 2), 1, -1):
            if [word.lower() for word in words[:prefix_size]] == [
                word.lower() for word in words[prefix_size : prefix_size * 2]
            ]:
                excerpt = " ".join(words[prefix_size:])
                break
        return excerpt if len(excerpt) <= 260 else excerpt[:257].rsplit(" ", 1)[0] + "…"

    def explain_evidence(label: str, excerpt: str) -> str:
        lowered = excerpt.lower()
        if label == "Business overview":
            if business_categories:
                return "The company operates across " + ", ".join(
                    category.lower() for category in business_categories
                ) + "."
            return "This describes the products and services at the center of the business."
        if "gross margin" in lowered and has_any(lowered, ("increase", "higher")):
            return "Profitability improved in this area, helped by stronger sales or a more favorable mix."
        if "operating income" in lowered and has_any(lowered, ("increase", "higher")):
            return "The company generated more profit from its core operations than in the comparison period."
        if has_any(lowered, ("revenue", "net sales")) and has_any(
            lowered, ("increase", "higher", "grew")
        ):
            return "The company reported stronger sales than in the comparison period."
        if "cash flow" in lowered and has_any(lowered, ("increase", "higher")):
            return "The business generated more cash from its operations than in the comparison period."
        if has_any(lowered, ("investigation", "litigation", "fine")):
            return "Legal or regulatory cases could lead to fines and force changes to the business."
        if has_any(lowered, ("defect", "reputation")):
            return "Product problems could hurt customer trust, the brand, and financial results."
        if has_any(lowered, ("component", "computing resources", "supplier")):
            return "The company depends on reliable access to components and computing capacity."
        if label in {"Risk factors", "Quarterly risk update"}:
            return "This is one way the company says its operations or financial results could be harmed."
        return "This provides management’s explanation of the company’s reported performance."

    business_text = annual.business or ""
    business_catalog = (
        ("Digital advertising", ("advertising", "advertisements", "ad impressions")),
        ("Cloud services", ("cloud services", "cloud computing", "cloud platform", "amazon web services", "aws")),
        ("Software & subscriptions", ("software", "subscription", "license")),
        ("Online retail", ("e-commerce", "online store", "online stores", "retail", "marketplace")),
        ("Automotive", ("vehicle", "automotive", "electric car")),
        ("Energy", ("energy generation", "energy storage", "solar")),
        ("Consumer devices", ("smartphone", "personal computer", "tablet", "device")),
        ("Digital services", ("digital service", "payment service", "streaming service")),
        ("AR/VR hardware", ("virtual reality", "augmented reality", "wearable")),
    )
    lowered_business = business_text.lower()
    ranked_business = sorted(
        (
            (sum(lowered_business.count(term) for term in terms), label)
            for label, terms in business_catalog
        ),
        reverse=True,
    )
    business_categories = [label for count, label in ranked_business if count > 0][:3]
    business_takeaway = (
        " · ".join(business_categories)
        if business_categories
        else short_evidence(business_text)
    )

    risk_catalog = (
        ("Regulation & legal", ("regulat", "legal proceeding", "antitrust", "government")),
        ("Supply chain", ("supply chain", "supplier", "manufactur", "component")),
        ("Competition", ("competition", "competitive")),
        ("Cybersecurity", ("cyber", "security breach", "data security")),
        ("Geographic exposure", ("china", "international", "foreign exchange", "geopolitical")),
        ("Product dependence", ("depend", "concentration", "significant portion")),
    )
    detected_risks = [label for label, terms in risk_catalog if has_any(annual.risk_factors, terms)]
    risk_takeaway = (
        " · ".join(detected_risks[:4])
        if detected_risks
        else "No risk themes were classified from the extracted section."
    )
    management_evidence = short_evidence(
        quarterly.mda or annual.mda,
        ("net sales", "revenue", "margin", "operating income", "liquidity", "cash flow"),
    )

    def split_operating_signal(value: str) -> tuple[str, str]:
        cleaned = re.sub(r"\s+", " ", value).strip()
        words = cleaned.split()
        for prefix_size in range(min(6, len(words) // 2), 1, -1):
            prefix = " ".join(words[:prefix_size])
            repeated = " ".join(words[prefix_size : prefix_size * 2])
            if prefix.lower() == repeated.lower():
                cleaned = " ".join(words[prefix_size:])
                break
        driven_change = re.search(
            r"^The (increase|decrease) in (.+?) was (?:primarily |mainly )?driven by (.+)",
            cleaned,
            flags=re.IGNORECASE,
        )
        if driven_change:
            direction, metric, drivers = driven_change.groups()
            headline = f"{metric.capitalize()} {'increased' if direction.lower() == 'increase' else 'decreased'}"
            detail = "Driven by " + drivers.rstrip(".… ") + "."
            if len(detail) > 190:
                detail = detail[:187].rsplit(" ", 1)[0] + "…"
            return headline, detail
        simple_change = re.search(
            r"^The (increase|decrease) in (.+?)(?=\s+(?:for|during|compared|was)\b)",
            cleaned,
            flags=re.IGNORECASE,
        )
        if simple_change:
            direction, metric = simple_change.groups()
            headline = f"{metric.capitalize()} {'increased' if direction.lower() == 'increase' else 'decreased'}"
            driver = re.search(r"(?:primarily|mainly) due to (.+)", cleaned, flags=re.IGNORECASE)
            detail = "Driven mainly by " + driver.group(1).rstrip(".… ") + "." if driver else ""
            if len(detail) > 190:
                detail = detail[:187].rsplit(" ", 1)[0] + "…"
            return headline, detail
        direction = re.search(
            r"^(.+?\b(?:increased|decreased|grew|declined|was higher|was lower)\b)",
            cleaned,
            flags=re.IGNORECASE,
        )
        headline = direction.group(1).strip() if direction else cleaned.split(".", 1)[0]
        driver = re.search(r"(?:primarily|mainly) due to (.+)", cleaned, flags=re.IGNORECASE)
        if driver:
            detail = "Driven mainly by " + driver.group(1).rstrip(".… ") + "."
        else:
            remainder = cleaned[len(headline):].lstrip(" ,;:-")
            detail = remainder
        if not detail.strip(" .,…;:-"):
            detail = ""
        if len(headline) > 105:
            headline = headline[:102].rsplit(" ", 1)[0] + "…"
        if len(detail) > 190:
            detail = detail[:187].rsplit(" ", 1)[0] + "…"
        return headline, detail

    operating_headline, operating_detail = split_operating_signal(management_evidence)
    normalized_management = re.sub(
        r"\$(\d+)\s+\.(\d+)", r"$\1.\2", management_evidence
    )

    def canonical_operating_headline(text: str, fallback: str) -> str:
        lowered = text.lower()
        year_over_year = re.search(
            r"year-over-year (increase|decrease) in ([^,.;]+)",
            text,
            flags=re.IGNORECASE,
        )
        if year_over_year:
            direction, subject = year_over_year.groups()
            subject = re.sub(r"\bthe\b\s*$", "", subject, flags=re.IGNORECASE).strip()
            if len(subject) <= 70:
                return f"{subject.capitalize()} {'increased' if direction.lower() == 'increase' else 'decreased'} year over year"

        metric_catalog = (
            ("Operating cash flow", ("operating cash flow", "cash provided by operating activities")),
            ("Operating income", ("operating income",)),
            ("Gross margin", ("gross margin",)),
            ("Revenue", ("total revenues", "revenue", "net sales")),
            ("Net income", ("net income",)),
        )
        positive_terms = ("increased", "increase", "grew", "higher")
        negative_terms = ("decreased", "decrease", "declined", "lower")
        for label, terms in metric_catalog:
            if any(term in lowered for term in terms):
                if any(term in lowered for term in positive_terms):
                    return f"{label} increased"
                if any(term in lowered for term in negative_terms):
                    return f"{label} decreased"

        clean_fallback = fallback.rstrip(".… ")
        if "…" in fallback or len(clean_fallback) > 90:
            return "Latest quarterly operating update"
        return clean_fallback

    operating_headline = canonical_operating_headline(
        normalized_management, operating_headline
    )
    if not operating_detail.strip(" .,…;:-"):
        operating_detail = "Reported in the latest quarterly management discussion."

    st.markdown("### Filing summary")
    st.caption(f"Official SEC disclosures · {display_name} · CIK {summary.cik}")
    risk_tags = "".join(f"<em>{html.escape(risk)}</em>" for risk in detected_risks)
    business_tags = "".join(
        f"<em>{html.escape(category)}</em>" for category in business_categories
    )
    business_content = business_tags or (
        f'<strong>{html.escape(business_takeaway)}</strong>'
    )
    annual_baseline_groups = (
        '<div class="change-log-groups annual-baseline-groups">'
        '<div class="change-log-tags"><small>BUSINESS LINES</small>'
        f'<div class="point-tags">{business_content}</div></div>'
        '<div class="change-log-tags"><small>DISCLOSED RISK AREAS</small>'
        f'<div class="point-tags">{risk_tags or html.escape(risk_takeaway)}</div></div>'
        '</div>'
    )
    annual_entry = (
        '<div class="change-log-entry filing-baseline-entry">'
        '<div class="change-log-marker filing-form-marker">10-K</div>'
        '<div class="change-log-content">'
        f'<div class="standout-label">ANNUAL BASELINE · {html.escape(readable_date(summary.latest_10k_date).upper())}</div>'
        f'<strong>{len(business_categories)} business lines · {len(detected_risks)} disclosed risk areas</strong>'
        f'{annual_baseline_groups}'
        '</div></div>'
    )

    if quarterly.mda or quarterly.risk_factors:
        quarterly_risks = [
            label for label, terms in risk_catalog
            if has_any(quarterly.risk_factors, terms)
        ]
        carried_risks = [risk for risk in quarterly_risks if risk in detected_risks]
        new_mentions = [risk for risk in quarterly_risks if risk not in detected_risks]
        annual_only_risks = [risk for risk in detected_risks if risk not in quarterly_risks]
        additional_tags = "".join(f"<em>{html.escape(risk)}</em>" for risk in new_mentions)
        annual_only_tags = "".join(f"<em>{html.escape(risk)}</em>" for risk in annual_only_risks)
        quarterly_signal = (
            operating_headline
            if quarterly.mda
            else "No quarterly operating discussion was extracted"
        )
        quarterly_signal_detail = (
            operating_detail
            if quarterly.mda
            else "Only the available quarterly risk text is shown below."
        )
        if new_mentions:
            risk_change_headline = (
                f"{len(new_mentions)} additional risk "
                f"{'theme appears' if len(new_mentions) == 1 else 'themes appear'} in the 10-Q"
            )
            risk_change_detail = "Only the categories that differ from the annual filing are shown below."
        elif annual_only_risks:
            risk_change_headline = (
                f"{len(annual_only_risks)} annual risk "
                f"{'theme was' if len(annual_only_risks) == 1 else 'themes were'} not repeated in the 10-Q"
            )
            risk_change_detail = "Not being repeated does not necessarily mean the underlying risk disappeared."
        elif quarterly.risk_factors:
            risk_change_headline = "Risk categories were unchanged"
            risk_change_detail = (
                f"All {len(carried_risks)} annual risk categories were repeated in the 10-Q."
            )
        else:
            risk_change_headline = "No separate quarterly risk section was extracted"
            risk_change_detail = "A category comparison could not be completed."
        additional_group = (
            '<div class="change-log-tags"><small>ADDITIONAL 10-Q THEMES</small>'
            f'<div class="point-tags">{additional_tags}</div></div>'
            if additional_tags else ""
        )
        annual_only_group = (
            '<div class="change-log-tags"><small>NOT REPEATED IN THE 10-Q</small>'
            f'<div class="point-tags muted-tags">{annual_only_tags}</div></div>'
            if annual_only_tags else ""
        )
        risk_groups_html = additional_group + annual_only_group
        risk_groups_block = (
            f'<div class="change-log-groups">{risk_groups_html}</div>'
            if risk_groups_html
            else ""
        )
        quarterly_entry = (
            '<div class="change-log-entry">'
            '<div class="change-log-marker filing-form-marker">10-Q</div>'
            '<div class="change-log-content">'
            f'<div class="standout-label">LATEST QUARTERLY UPDATE · {html.escape(readable_date(summary.latest_10q_date).upper())}</div>'
            f'<strong>{html.escape(quarterly_signal)}</strong>'
            f'<p>{html.escape(quarterly_signal_detail)}</p>'
            '<div class="quarterly-risk-result">'
            '<div class="standout-label">RISK UPDATE</div>'
            f'<strong>{html.escape(risk_change_headline)}</strong>'
            f'<p>{html.escape(risk_change_detail)}</p>'
            f'{risk_groups_block}'
            '</div></div></div>'
        )
    else:
        quarterly_entry = (
            '<div class="change-log-entry">'
            '<div class="change-log-marker filing-form-marker">10-Q</div>'
            '<div class="change-log-content">'
            f'<div class="standout-label">LATEST QUARTERLY UPDATE · {html.escape(readable_date(summary.latest_10q_date).upper())}</div>'
            '<strong>No reliably extracted quarterly sections are available</strong>'
            '<p>The annual baseline remains available above.</p>'
            '</div></div>'
        )

    recent_events = tuple(summary.recent_events)
    if recent_events:
        latest_event = recent_events[0]
        event_items = [
            (filing, item)
            for filing in recent_events
            for item in filing.items
        ]
        latest_items = tuple(latest_event.items)
        lead_item = latest_items[0] if latest_items else None
        lead_event_title = (
            lead_item.title or explain_8k_item(lead_item.item_number)
            if lead_item
            else "Latest current report filed"
        )
        earlier_filing_count = max(0, len(recent_events) - 1)
        earlier_item_count = max(0, len(event_items) - len(latest_items))
        activity_detail = (
            f'{len(latest_items)} disclosed item{"" if len(latest_items) == 1 else "s"} in the latest filing'
        )
        if earlier_filing_count:
            activity_detail += (
                f' · {earlier_item_count} more across {earlier_filing_count} earlier '
                f'filing{"" if earlier_filing_count == 1 else "s"}'
            )
        events_entry = (
            '<div class="change-log-entry filing-current-entry">'
            '<div class="change-log-marker filing-form-marker">8-K</div>'
            '<div class="change-log-content">'
            f'<div class="standout-label">LATEST CURRENT REPORT · {html.escape(readable_date(latest_event.filing_date).upper())}</div>'
            f'<strong>{html.escape(lead_event_title.rstrip("."))}</strong>'
            '<p>An official company disclosure filed between regular annual and quarterly reports.</p>'
            '<div class="quarterly-risk-result current-report-result">'
            '<div class="standout-label">RECENT 8-K ACTIVITY</div>'
            f'<strong>{len(recent_events)} recent filing{"" if len(recent_events) == 1 else "s"}</strong>'
            f'<p>{html.escape(activity_detail)}. Provided as filing context, not sentiment.</p>'
            '</div>'
            '</div></div>'
        )
    else:
        events_entry = (
            '<div class="change-log-entry filing-current-entry">'
            '<div class="change-log-marker filing-form-marker">8-K</div>'
            '<div class="change-log-content">'
            '<div class="standout-label">CURRENT REPORTS</div>'
            '<strong>No recent extractable 8-K events</strong>'
            '<p>No separate current-report disclosure is available for this company.</p>'
            '</div></div>'
        )
    st.markdown(
        f'<div class="filing-change-log filing-story">{annual_entry}{quarterly_entry}{events_entry}</div>',
        unsafe_allow_html=True,
    )

    def cached_filing_url(form: str) -> str:
        filing_root = Path("data") / "processed" / "sec" / summary.cik
        if not filing_root.exists():
            return ""
        candidates = []
        for directory in filing_root.iterdir():
            if not directory.is_dir() or not re.fullmatch(r"\d{18}", directory.name):
                continue
            has_business = (directory / "sections" / "business.txt").exists()
            has_mda = (directory / "sections" / "mda.txt").exists()
            if (form == "10-K" and has_business) or (
                form == "10-Q" and has_mda and not has_business
            ):
                candidates.append(directory.name)
        if not candidates:
            return ""
        accession = sorted(candidates)[-1]
        dashed_accession = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
        return (
            f"https://www.sec.gov/Archives/edgar/data/{int(summary.cik)}/"
            f"{accession}/{dashed_accession}-index.html"
        )

    annual_filing_url = getattr(summary, "latest_10k_url", "") or cached_filing_url("10-K")
    quarterly_filing_url = getattr(summary, "latest_10q_url", "") or cached_filing_url("10-Q")
    annual_badge = (
        f'<a href="{html.escape(annual_filing_url, quote=True)}" target="_blank"><b>Open annual filing</b><small>10-K ↗</small></a>'
        if annual_filing_url
        else '<span><b>Annual report</b><small>10-K</small></span>'
    )
    quarterly_badge = (
        f'<a href="{html.escape(quarterly_filing_url, quote=True)}" target="_blank"><b>Open quarterly filing</b><small>10-Q ↗</small></a>'
        if quarterly_filing_url
        else '<span><b>Quarterly update</b><small>10-Q</small></span>'
    )
    current_filing_url = recent_events[0].document_url if recent_events else ""
    current_badge = (
        f'<a href="{html.escape(current_filing_url, quote=True)}" target="_blank"><b>Open current report</b><small>8-K ↗</small></a>'
        if current_filing_url
        else '<span><b>Current report</b><small>8-K</small></span>'
    )
    evidence_sections = (
        ("Annual report", "10-K", "Item 1", "Business overview", summary.annual_sections.business),
        ("Annual report", "10-K", "Item 1A", "Risk factors", summary.annual_sections.risk_factors),
        ("Annual report", "10-K", "Item 7", "Management analysis", summary.annual_sections.mda),
        ("Quarterly update", "10-Q", "Item 2", "Quarterly performance", summary.quarterly_sections.mda),
        ("Quarterly update", "10-Q", "Item 1A", "Quarterly risk update", summary.quarterly_sections.risk_factors),
    )
    available_evidence = tuple(section for section in evidence_sections if section[4])
    st.markdown(
        f"""
        <div class="evidence-hero">
          <div class="evidence-hero-copy">
            <div class="standout-label">OFFICIAL SOURCE EVIDENCE</div>
            <h3>Evidence behind the summary</h3>
            <p>{html.escape(display_name)} · SEC CIK {html.escape(summary.cik)}</p>
          </div>
          <div class="evidence-filing-badges">
            {annual_badge}
            {quarterly_badge}
            {current_badge}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if available_evidence:
        evidence_row_parts = [
            '<div class="evidence-reader-heading">'
            '<span>SECTION</span><span>COMPANY EVIDENCE</span><span>EQUITY COMPASS READ</span>'
            '</div>'
        ]
        for report_name, form, item, label, content in available_evidence:
            excerpt = evidence_excerpt(content, label)
            explanation = explain_evidence(label, excerpt)
            evidence_row_parts.append(
                '<div class="evidence-row">'
                '<div class="evidence-row-source">'
                f'<small>{html.escape(report_name)}</small>'
                f'<strong>{html.escape(label)}</strong>'
                f'<span>{html.escape(form)} · {html.escape(item)}</span>'
                '</div>'
                f'<blockquote>{html.escape(excerpt)}</blockquote>'
                '<div class="evidence-explanation">'
                f'<p>{html.escape(explanation)}</p>'
                '</div>'
                '</div>'
            )
        evidence_rows = "".join(evidence_row_parts)
        st.markdown(
            f'<div class="evidence-reader">{evidence_rows}</div>',
            unsafe_allow_html=True,
        )
        evidence_options = {
            f"{form} · {item} · {label}": (form, item, label, content)
            for _report_name, form, item, label, content in available_evidence
        }
        reader_shell = st.container(border=False, key="source-reader-shell")
        with reader_shell:
            reader_identity, reader_selector = st.columns(
                [0.75, 2.25], vertical_alignment="center"
            )
            with reader_identity:
                st.markdown(
                    '<div class="source-reader-intro"><small>SOURCE READER</small><strong>Browse filing context</strong></div>',
                    unsafe_allow_html=True,
                )
            with reader_selector:
                selected_evidence = st.selectbox(
                    "Choose a filing section",
                    tuple(evidence_options),
                    key=f"evidence-section-{summary.ticker}",
                    label_visibility="collapsed",
                )
        selected_form, selected_item, selected_label, selected_content = (
            evidence_options[selected_evidence]
        )
        selected_context = re.sub(r"\s+", " ", selected_content).strip()
        focused_evidence = evidence_excerpt(selected_content, selected_label)
        search_phrase = focused_evidence.rstrip("…")
        match_index = selected_context.lower().find(search_phrase.lower())
        if match_index < 0:
            search_phrase = " ".join(search_phrase.split()[:10])
            match_index = selected_context.lower().find(search_phrase.lower())
        if match_index >= 0:
            passage_start = max(0, match_index - 320)
            previous_stop = selected_context.rfind(". ", 0, passage_start)
            if previous_stop >= 0:
                passage_start = previous_stop + 2
            passage_end = min(
                len(selected_context), match_index + len(search_phrase) + 720
            )
            next_stop = selected_context.find(". ", passage_end)
            if next_stop >= 0:
                passage_end = next_stop + 1
            passage = selected_context[passage_start:passage_end]
            local_match = passage.lower().find(search_phrase.lower())
            passage_html = (
                html.escape(passage[:local_match])
                + f"<mark>{html.escape(passage[local_match:local_match + len(search_phrase)])}</mark>"
                + html.escape(passage[local_match + len(search_phrase):])
            )
        else:
            passage = selected_context[:1100]
            passage_html = (
                f"<mark>{html.escape(focused_evidence)}</mark> "
                + html.escape(passage)
            )
        source_reader_html = (
            '<div class="source-reader-document">'
            '<header>'
            f'<div><small>{html.escape(selected_form)} · {html.escape(selected_item)}</small><strong>{html.escape(selected_label)}</strong></div>'
            '<span>SEC FILING EXCERPT</span>'
            '</header>'
            f'<div class="source-reader-text"><p>{passage_html}</p></div>'
            '</div>'
        )
        reader_shell.markdown(source_reader_html, unsafe_allow_html=True)
    else:
        st.info("No reliably extracted filing evidence is available.")


def show_news_and_events(summary: DashboardSummary) -> None:
    """Display recent headlines and official events as short-term context."""
    score_company_name = re.sub(
        r"\s+(inc\.?|incorporated|corp\.?|corporation|company|ltd\.?)$",
        "", summary.company_name, flags=re.IGNORECASE,
    )
    news_score = calculate_news_score(
        summary.recent_news,
        company_terms=(summary.ticker, score_company_name),
    )
    try:
        short_term_market = load_market_overview(summary.ticker)
    except MarketDataError:
        short_term_market = None
    short_term_score = calculate_short_term_score(short_term_market, news_score)
    score_position = max(0, min(100, (short_term_score.value + 10) * 5)) if short_term_score.available else 50
    score_text = (
        f"{short_term_score.value:+.1f}" if short_term_score.value
        else "0.0" if short_term_score.available
        else "—"
    )
    score_tone = (
        "unavailable" if not short_term_score.available
        else "positive" if short_term_score.value >= 2
        else "negative" if short_term_score.value <= -2
        else "neutral"
    )
    component_by_key = {component.key: component for component in short_term_score.components}

    def factor_tone(value: float | None) -> str:
        if value is None:
            return "unavailable"
        if value >= 0.5:
            return "supportive"
        if value <= -0.5:
            return "pressuring"
        return "neutral"

    price_component = component_by_key["price"]
    relative_component = component_by_key["relative"]
    catalyst_component = component_by_key["catalyst"]
    volume_component = component_by_key["volume"]
    catalyst_positive_label = (
        f"{news_score.positive_count} positive headline"
        f"{'s' if news_score.positive_count != 1 else ''}"
    )
    catalyst_negative_label = (
        f"{news_score.negative_count} negative headline"
        f"{'s' if news_score.negative_count != 1 else ''}"
    )
    factor_cards = (
        ("PRICE MOVEMENT", price_component, "Leaning upward", "Mostly steady", "Leaning downward"),
        ("VERSUS THE MARKET", relative_component, "Ahead of the market", "Moving with the market", "Behind the market"),
        ("RECENT COMPANY NEWS", catalyst_component, catalyst_positive_label, "No clear direction", catalyst_negative_label),
        ("TRADING ACTIVITY", volume_component, "Confirms the move", "Not confirming", "Confirms weakness"),
    )
    factor_cards_html = "".join(
        f'<div class="simple-factor-card factor-{factor_tone(component.value)}">'
        f'<small>{heading}</small><strong>{"Unavailable" if component.value is None else positive_label if component.value >= 0.5 else negative_label if component.value <= -0.5 else neutral_label}</strong></div>'
        for heading, component, positive_label, neutral_label, negative_label in factor_cards
    )
    st.markdown(
        f"""
        <section class="news-hero news-score-{score_tone}">
          <div class="news-hero-intro">
            <div class="news-context-copy"><span class="news-eyebrow">SHORT-TERM CONTEXT</span><h2>Short-term outlook for {html.escape(summary.ticker)}</h2>
            <p>A quick reading of what is happening now. It stays separate from long-term business quality.</p></div>
            <aside class="news-score-preview news-score-preview-{score_tone}">
              <div class="news-score-preview-top"><span>SHORT-TERM SCORE</span></div>
              <div class="news-score-preview-reading"><strong>{score_text}</strong><div><b>{html.escape(short_term_score.label)}</b></div></div>
            </aside>
          </div>
          <div class="news-score-integrated-detail">
            <div class="news-score-scale" aria-label="Short-Term Score {score_text} on a scale from negative 10 to positive 10">
              <div class="news-score-track"><span class="news-score-center"></span><i style="left:{score_position:.1f}%"></i></div>
              <div class="news-score-scale-labels"><span>−10 Negative</span><span>0 Neutral</span><span>+10 Positive</span></div>
            </div>
            <div class="simple-factor-heading"><strong>What is shaping the score?</strong></div>
            <div class="simple-factor-grid">{factor_cards_html}</div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )
    with st.expander("How the Short-Term Score works"):
        component_rows = "\n".join(
            f"| {component.label} | {component.weight:.0%} | "
            f"{'Unavailable' if component.value is None else f'{component.value:+.1f}'} | {component.detail} |"
            for component in short_term_score.components
        )
        st.markdown(
            f"""
            The Short-Term Score summarizes the company's **current market setup** from −10 to +10. It is context—not a buy or sell recommendation.

            - **Price trend (35%)** combines the latest 1-day and 5-day moves, adjusted for the stock's usual volatility.
            - **Relative strength (25%)** asks whether the stock is outperforming or lagging the S&P 500 over those same periods.
            - **Recent catalysts (25%)** evaluates fresh, company-specific news after relevance and duplicate controls.
            - **Volume confirmation (15%)** strengthens an existing move only when trading activity is meaningfully above its recent average; volume never creates direction by itself.
            - Missing inputs contribute zero rather than making the remaining inputs look more important. Evidence strength is shown separately from direction.

            The score can change frequently and may be noisy. It never changes or enters the long-term Equity Score.

            **Current inputs**

            | Factor | Weight | Reading | Recent evidence |
            |---|---:|---:|---|
            {component_rows}
            """
        )
    news_topics = detect_news_topics(summary.recent_news)
    topic_summary = "".join(
        f'<span><strong>{html.escape(topic.label)}</strong> {topic.article_count}</span>'
        for topic in news_topics[:4]
    ) or '<span><strong>No repeated topics</strong> yet</span>'
    st.markdown(
        f'''<section class="news-feed-heading news-feed-heading-combined">
          <div class="news-feed-title"><span>LATEST COVERAGE</span><h3>Recent company news</h3><p>Review the full news feed or focus on potentially meaningful headlines.</p></div>
          <aside class="news-feed-summary">
            <div><small>COVERAGE</small><strong>{len(summary.recent_news)} articles</strong></div>
            <div class="news-topic-summary"><small>HEADLINE THEMES</small><section>{topic_summary}</section></div>
            <div><small>ANALYSIS</small><strong>Market + catalysts</strong></div>
          </aside>
        </section>''',
        unsafe_allow_html=True,
    )
    view_column, result_column = st.columns([3.2, 1], gap="large")
    with view_column:
        selected_view = st.segmented_control(
            "Choose a news view",
            ["All coverage", "Focused coverage"],
            default="All coverage",
            key=f"news-view-{summary.ticker}",
            label_visibility="collapsed",
        )

    visible_articles = tuple(summary.recent_news)
    if selected_view == "Focused coverage":
        visible_articles = tuple(
            article for article in visible_articles
            if describe_news_relevance(article)[0]
            in {"Earnings", "Company event", "Regulation", "Analyst view"}
        )
    with result_column:
        st.markdown(
            f'<div class="news-results-meta"><span>{len(visible_articles)} article(s)</span><span>Newest first</span></div>',
            unsafe_allow_html=True,
        )
    if not visible_articles:
        st.info("No articles match this view. Try All coverage.")
    articles_per_page = 5
    total_pages = max(1, (len(visible_articles) + articles_per_page - 1) // articles_per_page)
    page_key = f"news-page-{summary.ticker}-{(selected_view or 'all').lower().replace(' ', '-')}"
    current_page = min(max(int(st.session_state.get(page_key, 1)), 1), total_pages)
    page_start = (current_page - 1) * articles_per_page
    page_end = min(page_start + articles_per_page, len(visible_articles))
    page_articles = visible_articles[page_start:page_end]
    score_signals_by_title = {
        signal.title.strip().casefold(): signal for signal in news_score.signals
    }
    for index, article in enumerate(page_articles, start=page_start):
        show_news_article(
            article,
            key=f"news-{index}",
            score_signal=score_signals_by_title.get(article.title.strip().casefold()),
        )
    if len(visible_articles) > articles_per_page:
        st.markdown(
            f'<div class="news-pagination-summary">Showing {page_start + 1}–{page_end} of {len(visible_articles)} articles</div>',
            unsafe_allow_html=True,
        )
        st.segmented_control(
            "Article page",
            list(range(1, total_pages + 1)),
            default=current_page,
            key=page_key,
            label_visibility="collapsed",
        )
    st.caption("Used in score marks fresh, company-specific headlines included in the news component. Neutral adds evidence without pushing the score up or down. Unmarked articles were not used.")


def show_dashboard(summary: DashboardSummary) -> None:
    """Organize the collected results into four focused tabs."""
    st.markdown(
        f"""
        <header class="company-context">
          <div class="company-meta"><span class="company-ticker">{html.escape(summary.ticker)}</span><i>U.S. public company</i></div>
          <div class="company-name">{html.escape(summary.company_name)}</div>
        </header>
        """,
        unsafe_allow_html=True,
    )
    if summary.data_warnings:
        with st.expander(f"Source availability notes ({len(summary.data_warnings)})"):
            for warning in summary.data_warnings:
                st.write(f"• {warning}")
    requested_section = str(st.query_params.get("section", "overview")).lower()
    default_section = DASHBOARD_SECTIONS.get(requested_section, "Overview")
    section_state_key = f"dashboard-section-{summary.ticker}"
    overview_tab, financials_tab, filings_tab, news_tab = st.tabs(
        list(DASHBOARD_SECTIONS.values()),
        default=default_section,
        key=section_state_key,
        on_change=remember_dashboard_section,
        args=(section_state_key,),
    )
    with overview_tab:
        show_overview(summary)
    with financials_tab:
        show_financials(summary)
    with filings_tab:
        show_filings(summary)
    with news_tab:
        show_news_and_events(summary)


st.set_page_config(page_title="Equity Compass", page_icon=str(FAVICON_PATH))

st.markdown(
    """
    <style>
    [data-testid="stHeader"] { display: none !important; }
    [data-testid="stHeaderActionElements"] { display: none !important; }
    .stApp { background: #FFFFFF; color: #102A43; }
    .block-container { max-width: 1120px; padding-top: 2.2rem; }
    .brand-lockup {
        display: flex;
        align-items: center;
        gap: 11px;
        margin-bottom: 1.65rem;
        color: #102A43;
        font-size: 1.05rem;
        font-weight: 780;
        letter-spacing: -0.02em;
        text-decoration: none !important;
        width: fit-content;
        cursor: pointer;
    }
    a.brand-lockup,
    a.brand-lockup:link,
    a.brand-lockup:visited,
    a.brand-lockup:hover,
    a.brand-lockup:active {
        color: #102A43 !important;
        text-decoration: none !important;
    }
    a.brand-lockup span { text-decoration: none !important; }
    .brand-lockup:focus-visible {
        outline: 3px solid rgba(10, 143, 106, 0.22);
        outline-offset: 5px;
        border-radius: 8px;
    }
    .brand-lockup img {
        width: 40px;
        height: 40px;
        object-fit: contain;
        mix-blend-mode: multiply;
        pointer-events: none;
        user-select: none;
        -webkit-user-drag: none;
    }
    .landing-backdrop {
        position: absolute;
        z-index: 0;
        top: 0;
        right: calc((100vw - min(1120px, 100vw)) / -2);
        width: min(680px, 54vw);
        height: 640px;
        overflow: hidden;
        pointer-events: none;
        opacity: 0.8;
        background:
            linear-gradient(90deg, rgba(255,255,255,0.04), #FFFFFF 96%),
            linear-gradient(rgba(10,143,106,0.055) 1px, transparent 1px),
            linear-gradient(90deg, rgba(10,143,106,0.055) 1px, transparent 1px);
        background-size: auto, 56px 56px, 56px 56px;
        mask-image: linear-gradient(to left, #000 12%, transparent 92%);
    }
    .landing-backdrop svg { width: 100%; height: 100%; }
    .landing-hero {
        position: relative;
        z-index: 1;
        max-width: 880px;
        margin: -1rem auto 0;
        text-align: center;
    }
    .hero-approved-logo {
        width: 300px;
        height: 286px;
        margin: -12px auto -4px;
        overflow: hidden;
    }
    .hero-approved-logo img {
        width: 100%;
        height: 100%;
        object-fit: contain;
        mix-blend-mode: multiply;
        pointer-events: none;
        user-select: none;
        -webkit-user-drag: none;
    }
    .landing-eyebrow {
        color: #08785A;
        font-size: 0.75rem;
        font-weight: 800;
        letter-spacing: 0.11em;
        text-transform: uppercase;
    }
    .landing-hero h1 {
        max-width: 760px;
        margin: 22px auto 8px;
        color: #102A43;
        font-size: clamp(1.55rem, 3.2vw, 2.25rem);
        font-weight: 780;
        letter-spacing: -0.035em;
        line-height: 1.15;
    }
    .landing-hero h1 em { color: #08785A; font-style: normal; }
    .landing-hero > p {
        max-width: 680px;
        margin: 0 auto 24px;
        color: #5E6C7B;
        font-size: 1.05rem;
        line-height: 1.65;
    }
    [data-testid="stForm"] {
        position: relative;
        z-index: 2;
        max-width: 960px;
        margin: 0 auto;
        padding: 24px 26px 20px;
        border: 1px solid rgba(16, 42, 67, 0.14);
        border-radius: 17px;
        background: rgba(255,255,255,0.94);
        box-shadow: 0 4px 16px rgba(16, 42, 67, 0.045);
        backdrop-filter: blur(8px);
    }
    .search-panel-heading { display: flex; align-items: center; gap: 14px; margin-bottom: 15px; }
    .search-panel-icon {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 48px;
        height: 48px;
        flex: 0 0 48px;
        border-radius: 50%;
        color: #08785A;
        background: #E8F6F1;
        font-size: 1.5rem;
    }
    .search-panel-heading strong { display: block; color: #102A43; font-size: 1.05rem; }
    .search-panel-heading span { display: block; margin-top: 2px; color: #657586; font-size: 0.78rem; }
    [data-testid="stForm"] [data-testid="stHorizontalBlock"] { align-items: end; }
    [data-testid="stForm"] [data-testid="stTextInput"] label { display: none; }
    [data-testid="stForm"] [data-baseweb="input"] {
        border: 0;
        background: transparent;
        box-shadow: none;
    }
    [data-testid="stForm"] input { font-size: 1rem; }
    [data-testid="stForm"] [data-testid="stFormSubmitButton"] button {
        min-height: 46px;
        border: 0;
        border-radius: 11px;
        padding: 0 22px;
        background: #0A8F6A;
        font-weight: 750;
    }
    [data-testid="stForm"] [data-testid="stFormSubmitButton"] button:hover {
        background: #08785A;
        color: #FFFFFF;
    }
    .popular-label { margin: 15px 0 4px; color: #687789; font-size: 0.78rem; text-align: center; }
    div[data-testid="stHorizontalBlock"] .stButton button[kind="secondary"] {
        border-color: rgba(16, 42, 67, 0.12);
        border-radius: 999px;
        color: #31465A;
        background: #F8FAFB;
        font-size: 0.78rem;
        font-weight: 700;
    }
    .research-preview {
        position: relative;
        z-index: 1;
        margin: 2.2rem auto 0;
        padding-top: 0;
    }
    .research-preview-intro { margin-bottom: 14px; color: #687789; font-size: 0.78rem; font-weight: 700; }
    .preview-card {
        display: grid;
        grid-template-columns: 48px 1fr;
        column-gap: 13px;
        min-height: 128px;
        padding: 20px 17px;
        border: 1px solid rgba(16, 42, 67, 0.09);
        border-radius: 14px;
        background: rgba(255,255,255,0.92);
        box-shadow: 0 8px 24px rgba(16,42,67,0.055);
    }
    .preview-icon {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 48px;
        height: 48px;
        grid-row: 1 / span 2;
        border-radius: 50%;
        color: #08785A;
        background: #E9F6F1;
        font-size: 1.35rem;
    }
    .preview-card strong { display: block; align-self: end; color: #17324B; font-size: 0.91rem; }
    .preview-card p { margin: 4px 0 0; color: #6E7C89; font-size: 0.74rem; line-height: 1.5; }
    .landing-disclaimer {
        margin: 2rem 0 0;
        padding: 16px;
        border: 1px solid rgba(10,143,106,0.12);
        border-radius: 13px;
        color: #637383;
        background: linear-gradient(90deg, rgba(232,248,243,.62), rgba(243,249,252,.82));
        font-size: 0.76rem;
        text-align: center;
    }
    .landing-disclaimer strong { color: #08785A; }
    [data-testid="stStatusWidget"] {
        border: 1px solid rgba(10, 143, 106, 0.16);
        border-radius: 14px;
        background: linear-gradient(135deg, #FBFEFD, #F4FAF8);
        box-shadow: none;
    }
    [data-testid="stStatusWidget"] summary { color: #17324B; font-weight: 750; }
    [data-testid="stProgressBar"] > div > div > div { background-color: #0A8F6A; }
    .research-loader {
        position: relative;
        overflow: hidden;
        margin-top: 24px;
        padding: 24px 26px 22px;
        border: 1px solid rgba(16, 42, 67, 0.10);
        border-radius: 16px;
        background: linear-gradient(145deg, #FFFFFF 0%, #F7FBF9 100%);
        box-shadow: 0 8px 28px rgba(16, 42, 67, 0.055);
    }
    .research-loader::after {
        content: "";
        position: absolute;
        inset: 0;
        pointer-events: none;
        background: linear-gradient(110deg, transparent 30%, rgba(255,255,255,.68) 47%, transparent 64%);
        transform: translateX(-100%);
        animation: research-shimmer 2.8s ease-in-out infinite;
    }
    @keyframes research-shimmer { 55%, 100% { transform: translateX(100%); } }
    .research-loader.is-complete {
        border-color: rgba(10, 143, 106, 0.24);
        background: linear-gradient(145deg, #FFFFFF 0%, #F0FAF6 100%);
        animation: loader-ready 480ms cubic-bezier(.2,.75,.3,1) both;
    }
    .research-loader.is-complete::after { animation: none; opacity: 0; }
    @keyframes loader-ready {
        0% { transform: scale(1); }
        52% { transform: scale(1.008); box-shadow: 0 12px 32px rgba(10,143,106,.10); }
        100% { transform: scale(1); box-shadow: 0 8px 28px rgba(16,42,67,.045); }
    }
    .loader-heading { display: flex; align-items: center; justify-content: space-between; gap: 18px; }
    .loader-brand { display: flex; align-items: center; gap: 13px; }
    .loader-compass {
        width: 42px;
        height: 42px;
        flex: 0 0 42px;
        pointer-events: none;
        user-select: none;
        filter: drop-shadow(0 3px 7px rgba(16, 42, 67, 0.10));
    }
    .loader-compass .compass-needle {
        transform-box: fill-box;
        transform-origin: center;
        animation: compass-needle-search 2.35s cubic-bezier(.45, 0, .22, 1) infinite;
    }
    .research-loader.is-complete .compass-needle {
        animation: none;
        transform: rotate(360deg);
    }
    @keyframes compass-needle-search {
        0% { transform: rotate(0deg); }
        55% { transform: rotate(310deg); }
        72% { transform: rotate(368deg); }
        84% { transform: rotate(356deg); }
        93% { transform: rotate(362deg); }
        100% { transform: rotate(360deg); }
    }
    .loader-kicker {
        display: block;
        color: #08785A;
        font-size: 0.64rem;
        font-weight: 820;
        letter-spacing: 0.10em;
    }
    .loader-heading h3 { margin: 3px 0 0; color: #17324B; font-size: 1.12rem; }
    .loader-percent { color: #08785A; font-size: 1.35rem; font-weight: 800; font-variant-numeric: tabular-nums; }
    .loader-progress {
        height: 7px;
        margin: 18px 0 20px;
        overflow: hidden;
        border-radius: 999px;
        background: #E6EEEB;
    }
    .loader-progress span {
        display: block;
        height: 100%;
        border-radius: inherit;
        background: linear-gradient(90deg, #08785A, #34B78B);
        transition: width 350ms ease;
    }
    .research-loader.is-complete .loader-progress span {
        box-shadow: 0 0 12px rgba(52,183,139,.32);
    }
    .loader-stages { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
    .loader-stage {
        min-height: 60px;
        padding: 10px 11px;
        border: 1px solid rgba(16,42,67,.07);
        border-radius: 10px;
        color: #8A969F;
        background: rgba(255,255,255,.58);
        font-size: 0.72rem;
        font-weight: 680;
    }
    .loader-stage i {
        display: block;
        width: 8px;
        height: 8px;
        margin-bottom: 7px;
        border-radius: 50%;
        background: #CCD5D2;
    }
    .loader-stage.done { color: #45675D; background: #F0F8F5; }
    .loader-stage.done i { background: #34B78B; }
    .loader-stage.active { border-color: rgba(10,143,106,.28); color: #08785A; background: #E8F5F0; }
    .loader-stage.active i { background: #08785A; box-shadow: 0 0 0 5px rgba(10,143,106,.10); animation: loader-pulse 1.4s ease-in-out infinite; }
    @keyframes loader-pulse { 50% { box-shadow: 0 0 0 8px rgba(10,143,106,0); } }
    .loader-task { margin: 16px 0 0; color: #657586; font-size: 0.76rem; }
    .loader-task strong { color: #344C5F; }
    .research-loader.is-complete .loader-kicker,
    .research-loader.is-complete .loader-task strong { color: #08785A; }

    .stApp:has(.dashboard-mode) [data-testid="stMainBlockContainer"] {
        animation: workspace-enter 520ms cubic-bezier(.2,.72,.28,1) both;
    }
    @keyframes workspace-enter {
        from { opacity: 0; transform: translateY(12px); }
        to { opacity: 1; transform: translateY(0); }
    }
    @media (prefers-reduced-motion: reduce) {
        .loader-compass .compass-needle,
        .research-loader::after,
        .loader-stage.active i,
        .research-loader.is-complete,
        .stApp:has(.dashboard-mode) [data-testid="stMainBlockContainer"] { animation: none; }
    }
    .stTabs [role="tablist"] {
        width: 100%;
        gap: 0;
        padding: 0;
        border: 0;
        border-bottom: 1px solid rgba(16, 42, 67, 0.12);
        border-radius: 0;
        background: transparent;
        box-shadow: none;
    }
    .stTabs [data-testid="stTab"] {
        flex: 0 0 auto;
        justify-content: center;
        height: 44px;
        padding: 0 20px;
        border-bottom: 2px solid transparent;
        border-radius: 0;
        color: #657586;
        font-size: 0.82rem;
        font-weight: 680;
        transition: color 160ms ease, background 160ms ease, border-color 160ms ease;
    }
    .stTabs [data-testid="stTab"]:hover { color: #08785A; background: #F2F8F6; }
    .stTabs [data-testid="stTab"][aria-selected="true"] {
        color: #08785A;
        border-bottom-color: #0A8F6A;
        background: transparent;
        box-shadow: none;
    }
    .stTabs .react-aria-SelectionIndicator { display: none; }
    .stTabs { margin-top: 10px; }
    .stTabs [data-testid="stTabPanel"] {
        padding-top: 28px;
        animation: tab-panel-enter 230ms cubic-bezier(.2,.7,.3,1) both;
    }
    @keyframes tab-panel-enter {
        from { opacity: 0; transform: translateX(10px); }
        to { opacity: 1; transform: translateX(0); }
    }
    .result-search-heading { margin: 0 0 12px; }
    .result-search-heading small {
        display: block;
        margin-bottom: 3px;
        color: #08785A;
        font-size: 0.68rem;
        font-weight: 800;
        letter-spacing: 0.09em;
        text-transform: uppercase;
    }
    .result-search-heading strong {
        color: #17324B;
        font-size: 1.35rem;
        letter-spacing: -0.025em;
    }
    .stApp:has(.dashboard-mode) [data-testid="stForm"] {
        display: flex;
        align-items: center;
        gap: 14px;
        max-width: none;
        width: 100%;
        margin: 0 0 30px;
        padding: 7px 8px 7px 13px;
        border-radius: 12px;
        background: #FFFFFF;
        box-shadow: none;
    }
    .company-switcher-label {
        display: flex;
        align-items: center;
        gap: 8px;
        height: 38px;
        flex: 0 0 auto;
        padding-right: 14px;
        border-right: 1px solid rgba(16,42,67,.10);
        color: #385166;
        font-size: 0.74rem;
        font-weight: 760;
        white-space: nowrap;
    }
    .stApp:has(.dashboard-mode) [data-testid="stForm"] [data-testid="stElementContainer"]:has(.company-switcher-label) {
        flex: 0 0 auto;
        width: auto;
        align-self: center;
        transform: translateY(-7px);
    }
    .company-switcher-icon {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 28px;
        border-radius: 8px;
        color: #08785A;
        background: #E8F5F0;
    }
    .company-switcher-icon svg { width: 15px; height: 15px; }
    .stApp:has(.dashboard-mode) [data-testid="stForm"] > [data-testid="stHorizontalBlock"] {
        flex: 1 1 auto;
    }
    .stApp:has(.dashboard-mode) [data-testid="stForm"] > [data-testid="stVerticalBlock"] {
        display: flex;
        flex-direction: row;
        align-items: center;
        gap: 14px;
    }
    .stApp:has(.dashboard-mode) [data-testid="stForm"] > [data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"] {
        flex: 1 1 auto;
    }
    .stApp:has(.dashboard-mode) [data-testid="stForm"] [data-baseweb="input"],
    .stApp:has(.dashboard-mode) [data-testid="stForm"] [data-testid="stFormSubmitButton"] button {
        min-height: 38px;
        height: 38px;
    }
    .company-context { margin: 6px 0 4px; }
    .company-meta { display: flex; align-items: center; gap: 9px; }
    .company-ticker {
        color: #08785A;
        font-size: 0.72rem;
        font-weight: 820;
        letter-spacing: 0.06em;
    }
    .company-meta i {
        padding-left: 9px;
        border-left: 1px solid rgba(16,42,67,.14);
        color: #7A8996;
        font-size: 0.72rem;
        font-style: normal;
    }
    .company-name {
        margin: 8px 0 0;
        color: #102A43;
        font-size: 2.15rem;
        font-weight: 760;
        letter-spacing: -0.045em;
        line-height: 1.15;
    }
    .profile-panel {
        position: relative;
        border: 1px solid rgba(39, 117, 98, 0.20);
        border-radius: 16px;
        overflow: hidden;
        margin-bottom: 2rem;
        background: linear-gradient(135deg, rgba(232, 248, 243, 0.24) 0%, rgba(255, 255, 255, 0.99) 48%);
        box-shadow: 0 10px 30px rgba(32, 67, 59, 0.07);
    }
    .profile-panel::before {
        content: "";
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 3px;
        background: linear-gradient(90deg, #07815F, #38B28E 48%, rgba(56, 178, 142, 0));
    }
    .profile-summary {
        display: flex;
        align-items: flex-start;
        gap: 16px;
        padding: 25px 26px 23px;
    }
    .profile-mark {
        position: relative;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 42px;
        height: 42px;
        flex: 0 0 42px;
        border-radius: 12px;
        color: #087A5A;
        overflow: hidden;
        background: rgba(10, 143, 106, 0.07);
        border: 1px solid rgba(10, 143, 106, 0.10);
        font-size: 0.72rem;
        font-weight: 800;
        letter-spacing: -0.02em;
    }
    .profile-mark span { position: absolute; z-index: 0; }
    .profile-mark img {
        position: relative;
        z-index: 1;
        width: 27px;
        height: 27px;
        object-fit: contain;
    }
    .profile-copy { min-width: 0; }
    .profile-description {
        max-width: 760px;
        margin-top: 6px;
        color: #525A68;
        font-size: 1rem;
        line-height: 1.55;
    }
    .profile-website {
        display: inline-flex;
        align-items: center;
        margin-top: 13px;
        padding: 6px 11px;
        border: 1px solid rgba(10, 143, 106, 0.22);
        border-radius: 8px;
        color: #087A5A;
        background: rgba(10, 143, 106, 0.07);
        font-size: 0.8rem;
        font-weight: 700;
        text-decoration: none;
    }
    .profile-website:hover {
        color: #05664A;
        background: rgba(10, 143, 106, 0.13);
        transform: translateY(-1px);
        text-decoration: none;
    }
    .profile-facts {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        border-top: 1px solid rgba(120, 130, 150, 0.20);
        background: rgba(255, 255, 255, 0.68);
    }
    .profile-fact {
        min-width: 0;
        padding: 16px 18px;
        border-right: 1px solid rgba(120, 130, 150, 0.18);
        transition: background 160ms ease;
    }
    .profile-fact:hover { background: rgba(10, 143, 106, 0.045); }
    .profile-fact:last-child { border-right: none; }
    .profile-fact-heading { display: flex; align-items: center; gap: 7px; }
    .profile-fact-heading i {
        color: #15916F;
        font-size: 0.72rem;
        font-style: normal;
    }
    .profile-fact-heading span {
        color: #858C98;
        font-size: 0.68rem;
        font-weight: 750;
        letter-spacing: 0.05em;
    }
    .profile-fact strong {
        display: block;
        margin-top: 5px;
        overflow-wrap: anywhere;
        line-height: 1.35;
    }
    .highlight-panel {
        border: 1px solid rgba(120, 130, 150, 0.28);
        border-radius: 14px;
        overflow: hidden;
        background: rgba(255, 255, 255, 0.015);
        margin-bottom: 2rem;
        box-shadow: 0 6px 22px rgba(20, 30, 50, 0.035);
    }
    .highlight-row {
        display: flex;
        align-items: flex-start;
        gap: 18px;
        padding: 23px 22px;
        border-bottom: 1px solid rgba(120, 130, 150, 0.20);
    }
    .highlight-row:last-child { border-bottom: none; }
    .highlight-icon {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 36px;
        height: 36px;
        border-radius: 50%;
        flex: 0 0 36px;
        font-weight: 700;
    }
    .highlight-positive { color: #087A5A; background: rgba(10, 143, 106, 0.13); }
    .highlight-official { color: #2E6FE5; background: rgba(46, 111, 229, 0.13); }
    .highlight-news { color: #7A5AF8; background: rgba(122, 90, 248, 0.13); }
    .highlight-risk { color: #B13D4C; background: rgba(208, 91, 104, 0.13); }
    .highlight-copy { flex: 1; min-width: 0; }
    .highlight-label {
        color: #7A8291;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.055em;
        margin-bottom: 3px;
    }
    .highlight-title { font-size: 1.05rem; font-weight: 700; line-height: 1.35; }
    .highlight-description { color: #5F6673; margin-top: 4px; line-height: 1.5; }
    .highlight-description:empty { display: none; }
    .highlight-meta { color: #858C98; font-size: 0.82rem; margin-top: 7px; }
    .highlight-badge {
        border-radius: 999px;
        padding: 6px 11px;
        color: #087A5A;
        background: rgba(10, 143, 106, 0.13);
        font-weight: 700;
        white-space: nowrap;
    }
    .highlight-separator { color: #A2A8B3; padding: 0 4px; }
    .highlight-link {
        display: inline-block;
        color: #2E6FE5;
        background: rgba(46, 111, 229, 0.08);
        border: 1px solid rgba(46, 111, 229, 0.22);
        border-radius: 7px;
        padding: 4px 9px;
        margin-left: 5px;
        text-decoration: none;
        font-size: 0.78rem;
        font-weight: 700;
        line-height: 1.2;
    }
    .highlight-link:hover {
        color: #245CC2;
        background: rgba(46, 111, 229, 0.14);
        border-color: rgba(46, 111, 229, 0.35);
        text-decoration: none;
    }
    .score-summary { padding: 20px 4px 8px 8px; }
    .score-summary-label {
        color: #7A8291;
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.055em;
    }
    .score-summary-value {
        font-size: 2.35rem;
        font-weight: 750;
        line-height: 1.1;
        margin: 6px 0 2px;
    }
    .score-summary-title { font-size: 1.05rem; font-weight: 700; }
    .score-summary-divider {
        height: 1px;
        background: rgba(120, 130, 150, 0.20);
        margin: 18px 0 15px;
    }
    .score-summary-item {
        display: flex;
        align-items: center;
        gap: 10px;
        margin: 12px 0;
    }
    .score-summary-item small {
        display: block;
        color: #8A919D;
        font-size: 0.67rem;
        font-weight: 700;
        letter-spacing: 0.045em;
    }
    .score-summary-item strong { display: block; margin-top: 1px; }
    .score-info {
        position: relative;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 15px;
        height: 15px;
        border: 1px solid #AAB0BA;
        border-radius: 50%;
        color: #7A8291;
        font-size: 0.65rem;
        font-weight: 750;
        cursor: help;
        vertical-align: 1px;
    }
    .score-popover {
        position: absolute;
        z-index: 1000;
        top: calc(100% + 9px);
        left: 50%;
        width: 315px;
        padding: 15px;
        border: 1px solid rgba(120, 130, 150, 0.25);
        border-radius: 12px;
        color: #3F4652;
        background: #FFFFFF;
        box-shadow: 0 8px 24px rgba(25, 35, 55, 0.14);
        font-size: 0.78rem;
        font-weight: 500;
        letter-spacing: 0;
        line-height: 1.5;
        text-align: left;
        text-transform: none;
        white-space: normal;
        opacity: 0;
        pointer-events: none;
        transform: translate(-50%, -4px);
        transition: opacity 120ms ease, transform 120ms ease;
    }
    .score-popover > strong {
        display: block;
        color: #292F3A;
        font-size: 0.92rem;
        font-weight: 750;
    }
    .score-popover > small {
        display: block;
        margin: 5px 0 0;
        color: #747C89;
        font-size: 0.74rem;
        line-height: 1.4;
    }
    .score-info:hover .score-popover,
    .score-info:focus .score-popover,
    .score-info:focus-within .score-popover {
        opacity: 1;
        transform: translate(-50%, 0);
    }
    .summary-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 9px; }
    .summary-strong { background: #0A8F6A; }
    .summary-watch { background: #D05B68; }
    .factor-table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        border: 1px solid rgba(120, 130, 150, 0.25);
        border-radius: 12px;
        overflow: visible;
    }
    .factor-table th:first-child { border-top-left-radius: 11px; }
    .factor-table th:last-child { border-top-right-radius: 11px; }
    .factor-table th {
        padding: 11px 16px;
        color: #7A8291;
        background: rgba(120, 130, 150, 0.06);
        border-bottom: 1px solid rgba(120, 130, 150, 0.22);
        font-size: 0.7rem;
        font-weight: 750;
        letter-spacing: 0.045em;
        text-align: left;
        text-transform: uppercase;
    }
    .factor-row td {
        padding: 15px 16px;
        border-bottom: 1px solid rgba(120, 130, 150, 0.18);
        vertical-align: middle;
    }
    .factor-row:last-child td { border-bottom: none; }
    .factor-name { width: 27%; font-weight: 650; }
    .factor-metric { width: 39%; color: #525A68; line-height: 1.4; }
    .factor-score-cell {
        width: 20%;
        color: #3B4250;
        font-weight: 750;
        font-variant-numeric: tabular-nums;
    }
    .inline-score { display: flex; align-items: center; gap: 7px; }
    .inline-score strong { min-width: 24px; text-align: right; font-size: 0.78rem; }
    .inline-track {
        width: 82px;
        height: 7px;
        overflow: hidden;
        border-radius: 999px;
        background: rgba(120, 130, 150, 0.13);
    }
    .inline-fill { display: block; height: 100%; border-radius: 999px; }
    .factor-table th:nth-child(3) { text-align: left; }
    .factor-assessment { width: 14%; text-align: center; }
    .factor-table th:nth-child(4) { text-align: center; }
    .factor-reading {
        border-radius: 999px;
        padding: 4px 8px;
        font-size: 0.75rem;
        font-weight: 700;
    }
    .factor-strong { color: #087A5A; background: rgba(10, 143, 106, 0.12); }
    .factor-middle { color: #95630B; background: rgba(217, 154, 38, 0.14); }
    .factor-watch { color: #B13D4C; background: rgba(208, 91, 104, 0.13); }
    .factor-neutral { color: #6F7784; background: rgba(120, 130, 150, 0.12); }
    .financial-story-card {
        display: grid;
        grid-template-rows: 22px 44px 28px 22px 28px;
        row-gap: 6px;
        height: 190px;
        margin-bottom: 20px;
        padding: 16px 15px 14px;
        box-sizing: border-box;
        border: 1px solid rgba(120, 130, 150, 0.2);
        border-radius: 11px;
        background: linear-gradient(145deg, #FFFFFF, #F8FAFD);
    }
    .financial-story-card > span {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 22px;
        height: 22px;
        border-radius: 7px;
        color: #17654F;
        background: #E6F4EF;
        font-size: 0.7rem;
        font-weight: 800;
        align-self: start;
    }
    .financial-story-card small {
        display: block;
        margin: 0;
        color: #626B78;
        font-size: 0.72rem;
        font-weight: 650;
        line-height: 1.35;
        align-self: center;
    }
    .financial-story-card strong {
        display: block;
        margin: 0;
        color: #2F3541;
        font-size: 1.02rem;
        line-height: 1.25;
        text-transform: capitalize;
        align-self: end;
    }
    .financial-story-card em {
        display: block;
        margin: 0;
        color: #8A919D;
        font-size: 0.68rem;
        font-style: normal;
        line-height: 1.35;
        align-self: start;
    }
    .financial-assessment {
        display: inline-flex;
        align-items: center;
        width: fit-content;
        padding: 3px 7px;
        border-radius: 999px;
        font-size: 0.66rem;
        font-style: normal;
        font-weight: 750;
        line-height: 1.25;
    }
    .financial-positive { color: #08785A; background: #E4F3EE; }
    .financial-mixed { color: #95630B; background: rgba(217, 154, 38, 0.14); }
    .financial-caution { color: #B13D4C; background: rgba(208, 91, 104, 0.13); }
    .financial-neutral { color: #68717E; background: rgba(120, 130, 150, 0.12); }
    .financial-section-heading {
        display: flex;
        align-items: center;
        gap: 12px;
        margin: 2px 0 5px;
    }
    .financial-section-heading > span {
        color: #0A8F6A;
        font-size: 0.75rem;
        font-weight: 800;
        letter-spacing: 0.06em;
    }
    .financial-section-heading small {
        display: block;
        color: #6F7784;
        font-size: 0.72rem;
        font-weight: 650;
    }
    .financial-section-heading h3 {
        margin: 1px 0 0;
        color: #303641;
        font-size: 1.25rem;
    }
    .filing-standouts {
        margin: 3px 0 28px;
        border: 1px solid rgba(16,42,67,.11);
        border-radius: 14px;
        overflow: hidden;
        background: #FFFFFF;
        box-shadow: 0 7px 22px rgba(24,55,70,.055);
    }
    .standout-primary {
        padding: 23px 28px 24px;
        border-bottom: 1px solid rgba(16,42,67,.09);
        background: linear-gradient(135deg, rgba(232,245,240,.62), #FFFFFF 68%);
    }
    .standout-primary-copy { min-width: 0; }
    .standout-label {
        color: #087A5A;
        font-size: .64rem;
        font-weight: 820;
        letter-spacing: .085em;
    }
    .standout-primary-copy > strong {
        display: block;
        max-width: 650px;
        margin-top: 10px;
        color: #17324B;
        font-size: 1.12rem;
        line-height: 1.42;
        letter-spacing: -.015em;
        overflow-wrap: normal;
        word-break: normal;
    }
    .standout-primary-copy p {
        max-width: 650px;
        margin: 9px 0 0;
        color: #596978;
        font-size: .82rem;
        line-height: 1.52;
    }
    .standout-bottom { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); }
    .standout-bottom section { min-height: 88px; padding: 18px 28px; }
    .standout-bottom section + section { border-left: 1px solid rgba(16,42,67,.085); }
    .standout-bottom strong {
        display: block;
        margin-top: 9px;
        color: #17324B;
        font-size: .84rem;
        line-height: 1.45;
    }
    .point-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
    .point-tags em {
        padding: 5px 8px;
        border-radius: 6px;
        color: #596978;
        background: #F3F6F7;
        font-size: .72rem;
        font-style: normal;
        font-weight: 650;
    }
    .evidence-hero {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 24px;
        margin: 8px 0 14px;
        padding: 22px 24px;
        border: 1px solid rgba(10,143,106,.16);
        border-left: 4px solid #0A8F6A;
        border-radius: 11px;
        background: #F4F9F7;
    }
    .evidence-hero h3 {
        margin: 7px 0 4px;
        color: #17324B;
        font-size: 1.25rem;
        letter-spacing: -.025em;
    }
    .evidence-hero p { margin: 0; color: #7A8996; font-size: .68rem; }
    .evidence-filing-badges { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
    .evidence-filing-badges span,
    .evidence-filing-badges a {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        padding: 8px 10px;
        border: 1px solid rgba(16,42,67,.09);
        border-radius: 8px;
        color: #596978;
        background: #FFFFFF;
        font-size: .7rem;
        text-decoration: none;
    }
    .evidence-filing-badges a:hover { border-color: rgba(10,143,106,.30); background: #FBFEFD; }
    .evidence-filing-badges a { cursor: pointer; }
    .evidence-filing-badges b { color: #17324B; }
    .evidence-filing-badges small {
        padding-left: 7px;
        border-left: 1px solid rgba(16,42,67,.12);
        color: #087A5A;
        font-size: .62rem;
        font-weight: 790;
    }
    .evidence-reader {
        margin-bottom: 10px;
        border: 1px solid rgba(16,42,67,.10);
        border-radius: 11px;
        overflow: hidden;
        background: #FFFFFF;
    }
    .evidence-reader-heading {
        display: grid;
        grid-template-columns: 170px minmax(0,1.25fr) minmax(240px,.85fr);
        gap: 22px;
        padding: 10px 20px;
        border-bottom: 1px solid rgba(16,42,67,.09);
        color: #7A8996;
        background: #FAFBFB;
        font-size: .58rem;
        font-weight: 800;
        letter-spacing: .065em;
    }
    .evidence-row {
        display: grid;
        grid-template-columns: 170px minmax(0,1.25fr) minmax(240px,.85fr);
        gap: 22px;
        align-items: stretch;
        padding: 0 20px;
        border-bottom: 1px solid rgba(16,42,67,.08);
    }
    .evidence-row:last-child { border-bottom: 0; }
    .evidence-row-source small {
        display: block;
        color: #087A5A;
        font-size: .61rem;
        font-weight: 800;
        letter-spacing: .05em;
    }
    .evidence-row-source strong {
        display: block;
        margin-top: 4px;
        color: #17324B;
        font-size: .8rem;
    }
    .evidence-row-source { padding: 18px 0; }
    .evidence-row-source a,
    .evidence-row-source span {
        display: inline-block;
        margin-top: 6px;
        color: #087A5A;
        font-size: .65rem;
        font-weight: 700;
        text-decoration: none;
    }
    .evidence-row-source a:hover { text-decoration: underline; }
    .evidence-row blockquote {
        align-self: center;
        margin: 16px 0;
        padding: 0 0 0 14px;
        border-left: 2px solid rgba(10,143,106,.28);
        color: #2F4659 !important;
        font-size: .82rem;
        font-weight: 500;
        line-height: 1.5;
        font-style: normal;
    }
    .evidence-explanation {
        display: flex;
        align-items: center;
        margin-right: -20px;
        padding: 17px 18px;
        background: #F3F8F6;
    }
    .evidence-explanation p {
        margin: 0;
        color: #294256;
        font-size: .78rem;
        font-weight: 560;
        line-height: 1.48;
    }
    .source-reader-intro {
        position: relative;
        display: flex;
        flex-direction: column;
        justify-content: center;
        gap: 3px;
        min-height: 40px;
        margin: 0;
        padding-left: 39px;
        transform: translateY(-2px);
    }
    .source-reader-intro::before {
        content: "▤";
        position: absolute;
        top: 50%;
        left: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 29px;
        height: 29px;
        border: 1px solid rgba(10,143,106,.16);
        border-radius: 8px;
        color: #087A5A;
        background: #E8F5F0;
        font-size: .8rem;
        transform: translateY(-50%);
    }
    .source-reader-intro small {
        color: #087A5A;
        font-size: .58rem;
        font-weight: 820;
        letter-spacing: .06em;
        line-height: 1;
    }
    .source-reader-intro strong {
        color: #17324B;
        font-size: .88rem;
        letter-spacing: -.01em;
        line-height: 1.2;
    }
    div[data-testid="stHorizontalBlock"]:has(.source-reader-intro) {
        align-items: center;
        margin-top: 24px;
        padding: 15px 16px;
        border: 1px solid rgba(16,42,67,.11);
        border-bottom: 0;
        border-left: 3px solid #0A8F6A;
        border-radius: 12px 12px 0 0;
        background: linear-gradient(90deg, #F3F9F6 0%, #F8FBFA 68%, #FFFFFF 100%);
    }
    div[data-testid="stHorizontalBlock"]:has(.source-reader-intro) [data-baseweb="select"] > div {
        min-height: 40px;
        border-color: rgba(16,42,67,.12);
        border-radius: 8px;
        background: #FFFFFF;
        box-shadow: none;
    }
    .source-reader-document {
        width: 100%;
        max-width: 100%;
        box-sizing: border-box;
        margin-top: -16px;
        border: 1px solid rgba(16,42,67,.11);
        border-top: 1px solid rgba(16,42,67,.09);
        border-radius: 0 0 12px 12px;
        overflow: hidden;
        background: #FFFFFF;
        box-shadow: 0 12px 30px rgba(24,55,70,.07);
    }
    .source-reader-document::after {
        content: "";
        display: block;
        height: 12px;
        border-top: 1px solid rgba(16,42,67,.06);
        background: #FFFFFF;
    }
    .source-reader-document header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 18px;
        padding: 14px 17px;
        border-bottom: 1px solid rgba(16,42,67,.09);
        background: #FCFDFD;
    }
    .source-reader-document header small {
        display: block;
        color: #087A5A;
        font-size: .6rem;
        font-weight: 800;
        letter-spacing: .055em;
    }
    .source-reader-document header strong {
        display: block;
        margin-top: 3px;
        color: #17324B;
        font-size: .82rem;
    }
    .source-reader-document header > span {
        color: #8A96A0;
        font-size: .57rem;
        font-weight: 780;
        letter-spacing: .06em;
    }
    .source-reader-text {
        max-height: 260px;
        overflow-y: auto;
        padding: 18px 20px;
        scrollbar-color: rgba(10,143,106,.28) transparent;
    }
    .source-reader-text p {
        margin: 0;
        color: #294256;
        font-family: Georgia, "Times New Roman", serif;
        font-size: .9rem;
        line-height: 1.78;
        letter-spacing: .002em;
    }
    .source-reader-text mark {
        padding: 2px 1px;
        color: #17324B;
        background: linear-gradient(180deg, transparent 5%, #DDF2E9 5%, #DDF2E9 94%, transparent 94%);
        box-shadow: 0 0 0 1px rgba(10,143,106,.04);
    }
    .filing-change-log {
        position: relative;
        margin: 4px 0 34px;
    }
    .filing-change-log::before {
        content: "";
        position: absolute;
        top: 19px;
        bottom: 21px;
        left: 18px;
        width: 1px;
        background: rgba(10,143,106,.24);
    }
    .change-log-entry {
        position: relative;
        display: grid;
        grid-template-columns: 38px minmax(0,1fr);
        gap: 19px;
    }
    .change-log-entry + .change-log-entry { margin-top: 4px; }
    .change-log-marker {
        position: relative;
        z-index: 1;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 37px;
        height: 37px;
        border: 1px solid rgba(10,143,106,.25);
        border-radius: 50%;
        color: #087A5A;
        background: #FFFFFF;
        font-size: .62rem;
        font-weight: 820;
    }
    .filing-form-marker {
        width: 41px;
        height: 41px;
        font-size: .58rem;
        letter-spacing: -.01em;
    }
    .change-log-content {
        padding: 4px 0 24px;
        border-bottom: 1px solid rgba(16,42,67,.10);
    }
    .change-log-entry:last-child .change-log-content { border-bottom: 0; }
    .change-log-content > strong {
        display: block;
        margin-top: 8px;
        color: #17324B;
        font-size: .98rem;
        line-height: 1.45;
    }
    .change-log-content > p {
        max-width: 820px;
        margin: 6px 0 0;
        color: #596978;
        font-size: .8rem;
        line-height: 1.55;
    }
    .change-log-groups {
        display: grid;
        grid-template-columns: repeat(3, minmax(0,1fr));
        gap: 20px;
        margin-top: 16px;
    }
    .annual-baseline-groups {
        grid-template-columns: repeat(2, minmax(0,1fr));
        max-width: 900px;
    }
    .quarterly-risk-result {
        max-width: 900px;
        margin-top: 18px;
        padding: 15px 17px;
        border-left: 3px solid rgba(10,143,106,.55);
        border-radius: 3px 9px 9px 3px;
        background: #F5F9F7;
    }
    .quarterly-risk-result > strong {
        display: block;
        margin-top: 7px;
        color: #17324B;
        font-size: .84rem;
    }
    .quarterly-risk-result > p {
        margin: 5px 0 0;
        color: #657586;
        font-size: .75rem;
    }
    .change-log-tags > small {
        color: #7A8996;
        font-size: .6rem;
        font-weight: 760;
        letter-spacing: .055em;
    }
    .muted-tags em { color: #7E8992; background: #F6F7F8; }
    .no-risk-change { color: #87939D; font-size: .72rem; }
    @media (max-width: 700px) {
        .standout-bottom { grid-template-columns: 1fr; }
        .standout-bottom section + section { border-left: 0; border-top: 1px solid rgba(16,42,67,.085); }
        .evidence-hero { align-items: flex-start; flex-direction: column; }
        .evidence-filing-badges { justify-content: flex-start; }
        .evidence-reader-heading { display: none; }
        .evidence-row { grid-template-columns: 1fr; gap: 0; padding: 0 16px; }
        .evidence-row blockquote { margin: 0 0 14px; }
        .evidence-explanation { margin: 0 -16px; }
        .change-log-groups { grid-template-columns: 1fr; gap: 13px; }
        .annual-baseline-groups { grid-template-columns: 1fr; }
    }
    .financial-answer { padding: 13px 2px 5px; }
    .financial-answer > small {
        color: #858D99;
        font-size: 0.67rem;
        font-weight: 750;
        letter-spacing: 0.045em;
    }
    .financial-answer > strong {
        display: block;
        margin: 2px 0 7px;
        color: #303641;
        font-size: 1.8rem;
        line-height: 1.08;
    }
    .financial-trend-label {
        display: inline-block;
        padding: 4px 8px;
        border-radius: 999px;
        color: #08785A;
        background: #E4F3EE;
        font-size: 0.72rem;
        font-weight: 750;
        text-transform: capitalize;
    }
    .financial-answer .financial-assessment {
        margin-left: 5px;
        vertical-align: middle;
    }
    .financial-answer p {
        margin: 6px 0 12px;
        color: #626B78;
        font-size: 0.78rem;
        line-height: 1.5;
    }
    .financial-answer .financial-takeaway {
        margin: 0 0 14px;
        padding: 10px 11px;
        border-radius: 8px;
        color: #31584D;
        background: rgba(10, 143, 106, 0.06);
        font-weight: 600;
    }
    .financial-answer hr {
        margin: 15px 0;
        border: 0;
        border-top: 1px solid rgba(120, 130, 150, 0.18);
    }
    .financial-answer b { color: #3D4551; font-size: 0.76rem; }
    .news-hero {
        margin: 12px 0 22px;
        padding: 27px 30px 20px;
        border: 1px solid rgba(16,42,67,.10);
        border-radius: 16px;
        background: linear-gradient(135deg, #F7FBFA 0%, #FFFFFF 68%);
        box-shadow: 0 8px 26px rgba(16,42,67,.045);
    }
    .news-hero-intro { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(320px, .82fr); gap: 28px; align-items: center; }
    .news-context-copy { display: flex; align-self: center; flex-direction: column; justify-content: center; max-width: 720px; }
    .news-eyebrow { color: #087A5A; font-size: .68rem; font-weight: 820; letter-spacing: .09em; }
    .news-hero .news-context-copy h2 { margin: 12px 0 0; color: #17324B; font-size: 1.55rem; letter-spacing: -.025em; line-height: 1.18; }
    .news-hero .news-context-copy p { max-width: 690px; margin: 17px 0 0; color: #5F7080; font-size: .84rem; line-height: 1.55; }
    .score-separation-note { display: flex; gap: 12px; padding: 16px; border: 1px solid rgba(10,143,106,.17); border-radius: 12px; background: #FFFFFF; }
    .score-separation-note > span { display: grid; place-items: center; width: 32px; height: 32px; flex: 0 0 32px; border-radius: 50%; color: #087A5A; background: #E7F5F0; }
    .score-separation-note strong, .score-separation-note small { display: block; }
    .score-separation-note strong { color: #17324B; font-size: .8rem; }
    .score-separation-note small { margin-top: 4px; color: #657586; font-size: .69rem; line-height: 1.45; }
    .news-score-preview { display: flex; min-height: 118px; padding: 15px 17px 15px 28px; border: 0; border-left: 1px solid rgba(16,42,67,.10); border-radius: 0; background: transparent; box-shadow: none; flex-direction: column; justify-content: center; }
    .news-score-preview-top { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .news-score-preview-top span { color: #087A5A; font-size: .59rem; font-weight: 820; letter-spacing: .075em; }
    .news-score-preview-top em { color: #8A96A0; font-size: .55rem; font-style: normal; font-weight: 750; letter-spacing: .055em; }
    .news-score-preview-reading { display: flex; align-items: center; gap: 20px; margin-top: 13px; }
    .news-score-preview-reading > strong { flex: 0 0 auto; min-width: 112px; color: #17324B; font-size: 2.8rem; line-height: 1; letter-spacing: -.055em; white-space: nowrap; }
    .news-score-preview-positive .news-score-preview-reading > strong { color: #087A5A; }
    .news-score-preview-negative .news-score-preview-reading > strong { color: #B84C4C; }
    .news-score-preview-unavailable .news-score-preview-reading > strong { color: #87919C; }
    .news-score-preview-reading > div { min-width: 0; }
    .news-score-preview-reading b, .news-score-preview-reading small { display: block; }
    .news-score-preview-reading b { color: #17324B; font-size: .96rem; }
    .news-score-preview-reading small { max-width: 270px; margin-top: 5px; color: #748390; font-size: .61rem; line-height: 1.38; }
    .news-score-integrated-detail { margin-top: 22px; padding-top: 17px; border-top: 1px solid rgba(16,42,67,.085); }
    .beginner-score-summary { display: grid; grid-template-columns: 1.35fr 1fr 1fr; gap: 10px; margin-top: 16px; }
    .beginner-score-summary > div { padding: 13px 14px; border-radius: 10px; background: rgba(245,248,247,.88); }
    .beginner-score-summary small, .beginner-score-summary strong, .beginner-score-summary span { display: block; }
    .beginner-score-summary small { color: #7E8B96; font-size: .53rem; font-weight: 800; letter-spacing: .065em; }
    .beginner-score-summary strong { margin-top: 6px; color: #17324B; font-size: .75rem; line-height: 1.42; }
    .beginner-score-summary span { margin-top: 6px; color: #596B7A; font-size: .65rem; line-height: 1.42; }
    .simple-factor-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-top: 16px; }
    .simple-factor-heading strong { color: #294256; font-size: .73rem; }
    .simple-factor-heading span { color: #7E8B96; font-size: .59rem; font-weight: 720; }
    .simple-factor-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 9px; margin-top: 9px; }
    .simple-factor-card { min-width: 0; padding: 12px 14px 13px; border: 1px solid rgba(16,42,67,.065); border-top: 3px solid #C7D0D5; border-radius: 10px; background: rgba(245,248,247,.86); }
    .simple-factor-card small, .simple-factor-card strong, .simple-factor-card span { display: block; }
    .simple-factor-card small { color: #7E8B96; font-size: .5rem; font-weight: 820; letter-spacing: .06em; }
    .simple-factor-card strong { margin-top: 7px; color: #294256; font-size: .84rem; line-height: 1.2; }
    .simple-factor-card span { margin-top: 6px; color: #687987; font-size: .58rem; line-height: 1.4; }
    .simple-factor-card.factor-supportive { border-top-color: #77C8AE; background: rgba(238,248,244,.9); }
    .simple-factor-card.factor-supportive strong { color: #087A5A; }
    .simple-factor-card.factor-pressuring { border-top-color: #E1A2A2; background: rgba(252,244,244,.9); }
    .simple-factor-card.factor-pressuring strong { color: #A94747; }
    .simple-factor-card.factor-unavailable { opacity: .72; }
    .short-term-components { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 9px; margin-top: 14px; }
    .short-term-component { min-width: 0; padding: 12px 13px; border: 1px solid rgba(16,42,67,.065); border-radius: 10px; background: rgba(245,248,247,.82); }
    .short-term-component small, .short-term-component strong, .short-term-component span { display: block; }
    .short-term-component small { overflow: hidden; color: #7E8B96; font-size: .51rem; font-weight: 800; letter-spacing: .055em; text-overflow: ellipsis; white-space: nowrap; }
    .short-term-component strong { margin-top: 6px; color: #17324B; font-size: 1.05rem; line-height: 1; }
    .short-term-component span { overflow: hidden; margin-top: 6px; color: #71808D; font-size: .55rem; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
    .short-term-component strong:not(:first-child) { font-variant-numeric: tabular-nums; }
    .news-score-facts { display: flex; gap: 24px; margin: 10px 0 0; }
    .news-score-facts > span { display: flex; align-items: baseline; gap: 7px; }
    .news-score-facts small { color: #87919C; font-size: .56rem; font-weight: 800; letter-spacing: .06em; }
    .news-score-facts strong { color: #526473; font-size: .66rem; }
    .news-score-integrated-detail .news-score-breakdown { margin-top: 12px; }
    .news-score-card {
        display: grid;
        grid-template-columns: minmax(0, 1.45fr) minmax(280px, .75fr);
        gap: 18px 28px;
        margin: 0 0 10px;
        padding: 22px 24px 18px;
        border: 1px solid rgba(16,42,67,.11);
        border-radius: 15px;
        background: #FFFFFF;
        box-shadow: 0 7px 22px rgba(16,42,67,.035);
        animation: news-score-arrive 260ms cubic-bezier(.2,.72,.3,1) both;
    }
    @keyframes news-score-arrive { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
    .news-score-kicker { color: #087A5A; font-size: .65rem; font-weight: 820; letter-spacing: .08em; }
    .news-score-kicker span { margin-left: 8px; color: #8A96A0; font-weight: 720; letter-spacing: .045em; }
    .news-score-reading { display: flex; align-items: center; gap: 17px; margin-top: 11px; }
    .news-score-reading > strong { min-width: 78px; color: #17324B; font-size: 2.7rem; line-height: 1; letter-spacing: -.055em; }
    .news-score-positive .news-score-reading > strong { color: #087A5A; }
    .news-score-negative .news-score-reading > strong { color: #B84C4C; }
    .news-score-reading h3 { margin: 0 0 3px; color: #17324B; font-size: 1.08rem; }
    .news-score-reading p { margin: 0; color: #657586; font-size: .73rem; line-height: 1.48; }
    .news-score-evidence { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; }
    .news-score-evidence > div { padding: 12px 13px; border-radius: 10px; background: #F5F8F7; }
    .news-score-evidence small, .news-score-evidence strong, .news-score-evidence span { display: block; }
    .news-score-evidence small { color: #87919C; font-size: .57rem; font-weight: 800; letter-spacing: .06em; }
    .news-score-evidence strong { margin-top: 5px; color: #294256; font-size: .78rem; }
    .news-score-evidence span { margin-top: 2px; color: #788794; font-size: .62rem; line-height: 1.35; }
    .news-score-scale { grid-column: 1 / -1; }
    .news-score-track { position: relative; height: 7px; border-radius: 999px; background: linear-gradient(90deg, #E8BABA 0%, #EEF1F1 48%, #EEF1F1 52%, #A8D9CA 100%); }
    .news-score-track .news-score-center { position: absolute; top: -3px; bottom: -3px; left: 50%; width: 1px; background: rgba(23,50,75,.28); }
    .news-score-track i { position: absolute; top: 50%; width: 15px; height: 15px; border: 3px solid #FFFFFF; border-radius: 50%; background: #17324B; box-shadow: 0 1px 5px rgba(16,42,67,.28); transform: translate(-50%,-50%); }
    .news-score-positive .news-score-track i { background: #0A8F6A; }
    .news-score-negative .news-score-track i { background: #B84C4C; }
    .news-score-unavailable .news-score-track i { opacity: .35; background: #87919C; }
    .news-score-scale-labels { display: flex; justify-content: space-between; margin-top: 6px; color: #8B97A1; font-size: .58rem; }
    .news-score-breakdown { grid-column: 1 / -1; display: flex; align-items: center; gap: 7px; padding-top: 2px; }
    .news-score-breakdown > span { padding: 4px 8px; border-radius: 999px; font-size: .61rem; font-weight: 720; }
    .news-score-breakdown .positive { color: #087A5A; background: #E8F5F0; }
    .news-score-breakdown .neutral { color: #617180; background: #F0F3F4; }
    .news-score-breakdown .negative { color: #A94747; background: #FCEEEE; }
    .news-score-breakdown .evidence-pill { color: #526473; background: #EEF3F2; }
    .news-score-breakdown em { margin-left: auto; color: #7A8996; font-size: .63rem; font-style: normal; }
    .news-overview-strip { display: grid; grid-template-columns: .7fr 2fr .8fr; margin-bottom: 18px; overflow: hidden; border: 1px solid rgba(16,42,67,.10); border-radius: 12px; background: #FFFFFF; }
    .news-overview-strip > div { padding: 14px 17px; border-right: 1px solid rgba(16,42,67,.09); }
    .news-overview-strip > div:last-child { border-right: 0; }
    .news-overview-strip small { display: block; margin-bottom: 5px; color: #87919C; font-size: .61rem; font-weight: 780; letter-spacing: .06em; }
    .news-overview-strip strong { color: #294256; font-size: .82rem; }
    .news-topic-summary section { display: flex; flex-wrap: wrap; gap: 6px; }
    .news-topic-summary section span { padding: 4px 8px; border-radius: 999px; color: #526473; background: #F2F6F5; font-size: .67rem; }
    .news-feed-heading { position: relative; margin: 28px 2px 15px; padding: 2px 0 2px 18px; }
    .news-feed-heading::before { content: ""; position: absolute; top: 2px; bottom: 2px; left: 0; width: 3px; border-radius: 999px; background: #0A8F6A; }
    .news-feed-heading > div > span { color: #087A5A; font-size: .62rem; font-weight: 810; letter-spacing: .08em; }
    .news-feed-heading h3 { margin: 5px 0 5px; color: #17324B; font-size: 1.38rem; font-weight: 760; letter-spacing: -.025em; line-height: 1.2; }
    .news-feed-heading p { max-width: 690px; margin: 0; color: #657586; font-size: .78rem; line-height: 1.5; }
    .news-feed-heading-combined { display: block; margin: 25px 2px 14px; padding: 5px 0 3px 18px; }
    .news-feed-title { max-width: 760px; }
    .news-feed-summary { display: grid; grid-template-columns: .65fr 1.75fr .8fr; width: 100%; min-width: 0; margin-top: 19px; border-top: 1px solid rgba(16,42,67,.09); background: transparent; }
    .news-feed-summary > div { min-width: 0; padding: 13px 18px 3px; border-right: 1px solid rgba(16,42,67,.075); }
    .news-feed-summary > div:first-child { padding-left: 0; }
    .news-feed-summary > div:last-child { border-right: 0; }
    .news-feed-summary small { display: block; margin-bottom: 7px; color: #87919C; font-size: .53rem; font-weight: 800; letter-spacing: .06em; }
    .news-feed-summary strong { color: #294256; font-size: .74rem; white-space: nowrap; }
    .news-feed-summary .news-topic-summary section { gap: 6px; }
    .news-feed-summary .news-topic-summary section span { padding: 4px 8px; font-size: .62rem; white-space: nowrap; }
    [role="radiogroup"][aria-label="Choose a news view"] { display: inline-flex; align-items: center; gap: 2px; width: auto; padding: 3px; border: 0; border-radius: 9px; background: #F0F3F2; }
    [role="radiogroup"][aria-label="Choose a news view"] [role="radio"] { min-height: 30px; padding: 5px 13px; border: 0 !important; border-radius: 7px !important; color: #657586; background: transparent !important; box-shadow: none !important; font-size: .71rem; font-weight: 660; transition: color 160ms ease, background-color 160ms ease, box-shadow 180ms ease, transform 160ms ease; }
    [role="radiogroup"][aria-label="Choose a news view"] [role="radio"]:hover { transform: translateY(-1px); color: #17324B; background: rgba(255,255,255,.58) !important; }
    [role="radiogroup"][aria-label="Choose a news view"] [role="radio"][aria-checked="true"] { color: #087A5A; background: #FFFFFF !important; box-shadow: 0 1px 4px rgba(16,42,67,.10) !important; animation: news-view-settle 190ms cubic-bezier(.2,.75,.3,1); }
    @keyframes news-view-settle { 0% { transform: scale(.97); opacity: .78; } 100% { transform: scale(1); opacity: 1; } }
    .news-results-meta { display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-height: 38px; margin: 0; color: #87919C; font-size: .66rem; white-space: nowrap; }
    .news-results-meta span + span::before { content: "·"; margin-right: 8px; color: #B0B8BF; }
    .stApp [data-testid="stHorizontalBlock"]:has([role="radiogroup"][aria-label="Choose a news view"]) { align-items: center; margin-bottom: 13px; }
    .news-card { margin-bottom: 11px; padding: 14px 15px 14px 20px; border: 1px solid rgba(16,42,67,.11); border-radius: 13px; background: #FFFFFF; animation: news-card-arrive 230ms cubic-bezier(.2,.72,.3,1) both; transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease; }
    .news-card:nth-of-type(2) { animation-delay: 25ms; }
    .news-card:nth-of-type(3) { animation-delay: 45ms; }
    @keyframes news-card-arrive { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
    .news-card:hover { transform: translateY(-1px); border-color: rgba(10,143,106,.25); box-shadow: 0 8px 22px rgba(16,42,67,.055); }
    .news-pagination-summary { margin: 19px 0 9px; color: #7A8996; font-size: .68rem; text-align: center; }
    [role="radiogroup"][aria-label="Article page"] { display: flex; justify-content: center; gap: 6px; width: fit-content; margin: 0 auto; padding: 0; border: 0; background: transparent; }
    [role="radiogroup"][aria-label="Article page"] [role="radio"] { min-width: 31px; min-height: 31px; padding: 4px 8px; border: 1px solid rgba(16,42,67,.11) !important; border-radius: 7px !important; color: #657586; background: #FFFFFF !important; box-shadow: none !important; font-size: .7rem; font-weight: 700; transition: color 150ms ease, border-color 150ms ease, background-color 150ms ease, transform 150ms ease; }
    [role="radiogroup"][aria-label="Article page"] [role="radio"]:hover { transform: translateY(-1px); border-color: rgba(10,143,106,.28) !important; color: #087A5A; }
    [role="radiogroup"][aria-label="Article page"] [role="radio"][aria-checked="true"] { border-color: #0A8F6A !important; color: #FFFFFF; background: #0A8F6A !important; }
    [data-testid="stElementContainer"]:has([role="radiogroup"][aria-label="Article page"]) { margin-right: auto; margin-bottom: .45rem; margin-left: auto; }
    .stTabs [data-testid="stTab"] { transition: color 160ms ease, background-color 160ms ease, transform 160ms ease; }
    .stTabs [data-testid="stTab"]:hover { transform: translateY(-1px); }
    .stTabs [role="tabpanel"] { animation: workspace-panel-arrive 220ms cubic-bezier(.2,.72,.3,1) both; }
    [role="radiogroup"] [role="radio"] { transition: color 160ms ease, background-color 160ms ease, border-color 160ms ease, box-shadow 180ms ease, transform 160ms ease; }
    @keyframes workspace-panel-arrive { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: translateY(0); } }
    @media (prefers-reduced-motion: reduce) {
        .stTabs [data-testid="stTab"],
        .stTabs [role="tabpanel"],
        [role="radiogroup"] [role="radio"],
        [role="radiogroup"][aria-label="Choose a news view"] [role="radio"],
        [role="radiogroup"][aria-label="Article page"] [role="radio"],
        .news-card, .news-score-card { animation: none; transition: none; }
    }
    .news-card-meta { display: flex; align-items: center; gap: 8px; color: #8A96A0; font-size: .68rem; }
    .news-card-layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; min-height: 126px; }
    .news-card-layout.has-image { grid-template-columns: minmax(0, 1fr) 188px; }
    .news-card-content { display: flex; min-width: 0; flex-direction: column; }
    .news-card-image { position: relative; display: grid; place-items: center; min-height: 126px; overflow: hidden; border-radius: 10px; color: #087A5A; background: linear-gradient(135deg, #E8F5F1, #F4F7F6); font-size: 1.15rem; font-weight: 800; letter-spacing: .04em; }
    .news-card-image img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; transition: transform 300ms ease; }
    .news-card:hover .news-card-image img { transform: scale(1.025); }
    .news-source { color: #087A5A; font-weight: 760; }
    .news-source::after { content: "·"; margin-left: 8px; color: #B0B8BF; }
    .news-card h3 { max-width: 900px; margin: 8px 0 6px; color: #17324B; font-size: 1.01rem; line-height: 1.4; }
    .news-card-content > p { max-width: 850px; margin: 0; color: #617180; font-size: .77rem; line-height: 1.55; }
    .news-card-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 14px; padding-top: 13px; border-top: 1px solid rgba(16,42,67,.075); }
    .news-cues { display: flex; flex-wrap: wrap; gap: 7px; }
    .news-cues > span { padding: 4px 8px; border-radius: 999px; font-size: .64rem; font-weight: 720; }
    .news-topic { color: #4B5F70; background: #F1F4F6; }
    .news-relevance-high { color: #A05A13; background: #FFF3E2; }
    .news-relevance-medium { color: #486176; background: #EDF3F7; }
    .news-score-impact { border: 1px solid transparent; }
    .news-score-impact.impact-positive { color: #087A5A; border-color: rgba(10,143,106,.16); background: #E8F5F0; }
    .news-score-impact.impact-negative { color: #A94747; border-color: rgba(184,76,76,.15); background: #FCEEEE; }
    .news-score-impact.impact-neutral { color: #617180; border-color: rgba(97,113,128,.12); background: #F0F3F4; }
    .news-read-link { color: #087A5A !important; font-size: .72rem; font-weight: 760; text-decoration: none !important; white-space: nowrap; }
    .news-read-link:hover { color: #05664A !important; }
    .event-filing-label { display: flex; align-items: center; gap: 13px; margin: 14px 0 8px; padding: 14px 16px; border: 1px solid rgba(16,42,67,.10); border-radius: 11px; background: #F8FAFA; }
    .event-filing-label > span { display: grid; place-items: center; width: 38px; height: 38px; border-radius: 9px; color: #FFFFFF; background: #17324B; font-size: .68rem; font-weight: 800; }
    .event-filing-label strong, .event-filing-label small { display: block; }
    .event-filing-label strong { color: #294256; font-size: .8rem; }
    .event-filing-label small { color: #87919C; font-size: .65rem; }
    .event-filing-label a { margin-left: auto; color: #087A5A !important; font-size: .72rem; font-weight: 760; text-decoration: none !important; }
    @media (max-width: 640px) {
        .news-score-card { grid-template-columns: 1fr; padding: 18px; }
        .news-score-evidence { grid-template-columns: 1fr 1fr; }
        .news-score-breakdown { flex-wrap: wrap; }
        .news-score-breakdown em { width: 100%; margin: 4px 0 0; }
        .block-container { padding-top: 1.2rem; }
        .brand-lockup { margin-bottom: 2.6rem; }
        .hero-approved-logo { width: 250px; height: 238px; }
        .landing-hero h1 { margin-top: 18px; font-size: 1.65rem; }
        .landing-hero > p { font-size: 0.95rem; }
        [data-testid="stForm"] { padding: 18px 15px 16px; }
        .search-panel-heading { margin-bottom: 10px; }
        .research-preview { margin-top: 2rem; }
        .landing-backdrop { width: 88vw; opacity: 0.48; }
        .research-loader { padding: 20px 17px; }
        .loader-stages { grid-template-columns: repeat(2, 1fr); }
        .stApp:has(.dashboard-mode) [data-testid="stForm"] {
            display: block;
            max-width: none;
            margin: 0 0 24px;
        }
        .stApp:has(.dashboard-mode) [data-testid="stForm"] > [data-testid="stVerticalBlock"] {
            display: block;
        }
        .company-switcher-label {
            height: auto;
            margin-bottom: 9px;
            padding: 0 0 9px;
            border-right: 0;
            border-bottom: 1px solid rgba(16,42,67,.10);
        }
        .stApp:has(.dashboard-mode) [data-testid="stForm"] [data-testid="stElementContainer"]:has(.company-switcher-label) {
            transform: none;
        }
        .company-name { font-size: 1.8rem; }
        .stTabs [data-testid="stTab"] { padding: 0 11px; font-size: 0.76rem; }
        .profile-summary { padding: 21px 18px; gap: 12px; }
        .profile-mark { width: 38px; height: 38px; flex-basis: 38px; }
        .profile-facts { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .profile-fact:nth-child(2) { border-right: none; }
        .profile-fact:nth-child(-n+2) {
            border-bottom: 1px solid rgba(120, 130, 150, 0.18);
        }
        .highlight-row { padding: 17px 16px; gap: 12px; }
        .highlight-badge { font-size: 0.82rem; }
        .factor-table { font-size: 0.82rem; }
        .factor-table th, .factor-row td { padding-left: 9px; padding-right: 9px; }
        .factor-reading { padding: 3px 6px; }
        .score-summary { padding: 4px 2px 12px; }
        .financial-story-card { height: 186px; }
        .news-hero { padding: 21px 18px; }
        .news-hero-intro { grid-template-columns: 1fr; gap: 17px; }
        .news-score-preview { padding: 15px 0 0; border-top: 1px solid rgba(16,42,67,.085); border-left: 0; }
        .beginner-score-summary { grid-template-columns: 1fr; }
        .simple-factor-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
        .short-term-components { grid-template-columns: repeat(2, minmax(0,1fr)); }
        .news-score-facts { align-items: flex-start; flex-direction: column; gap: 6px; }
        .news-overview-strip { grid-template-columns: 1fr; }
        .news-overview-strip > div { border-right: 0; border-bottom: 1px solid rgba(16,42,67,.09); }
        .news-overview-strip > div:last-child { border-bottom: 0; }
        .news-card-footer { align-items: flex-start; flex-direction: column; }
        .news-card { padding: 14px; }
        .news-card-layout { grid-template-columns: 1fr; }
        .news-card-image { min-height: 145px; order: -1; }
        .news-feed-heading-combined { grid-template-columns: 1fr; gap: 14px; }
        .news-feed-summary { grid-template-columns: 1fr; }
        .news-feed-summary > div { border-right: 0; border-bottom: 1px solid rgba(16,42,67,.075); }
        .news-feed-summary > div:last-child { border-bottom: 0; }
        .news-feed-heading { padding-left: 14px; }
        .news-feed-heading h3 { font-size: 1.2rem; }
        [role="radiogroup"][aria-label="Choose a news view"] { max-width: 100%; overflow-x: auto; }
        [role="radiogroup"][aria-label="Choose a news view"] [role="radio"] { white-space: nowrap; }
        .news-results-meta { justify-content: flex-start; min-height: 26px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if (
    "dashboard_summary" in st.session_state
    and st.session_state.get("financials_schema_version")
    != FINANCIALS_SCHEMA_VERSION
):
    del st.session_state["dashboard_summary"]

home_requested = "home" in st.query_params
if home_requested:
    st.session_state.pop("dashboard_summary", None)
    st.session_state.pop("company_search", None)
    st.session_state.pop("popular_search_requested", None)
    for state_key in list(st.session_state):
        if state_key.startswith("dashboard-section-"):
            del st.session_state[state_key]
    st.query_params.clear()
    st.rerun()

query_ticker_value = st.query_params.get("ticker", "")
if isinstance(query_ticker_value, list):
    query_ticker_value = query_ticker_value[0] if query_ticker_value else ""
query_ticker = str(query_ticker_value).strip().upper()

has_dashboard = "dashboard_summary" in st.session_state
restore_error = None
if query_ticker and not has_dashboard:
    try:
        restored_summary = load_dashboard_analysis(query_ticker)
    except DashboardError as error:
        restore_error = error
    else:
        st.session_state["dashboard_summary"] = restored_summary
        st.session_state["financials_schema_version"] = FINANCIALS_SCHEMA_VERSION
        st.session_state["company_search"] = restored_summary.ticker
        has_dashboard = True

restore_from_url = bool(query_ticker and not has_dashboard and restore_error is None)
if restore_from_url:
    st.session_state["company_search"] = query_ticker

if not has_dashboard:
    st.markdown(
        f"""
        <div class="landing-backdrop" aria-hidden="true">
          <svg viewBox="0 0 680 640" preserveAspectRatio="none">
            <path d="M0 570 C90 535 105 552 170 475 S280 415 315 380 S390 330 430 280 S525 235 565 180 S630 126 680 64" fill="none" stroke="#8BD5B8" stroke-width="3" opacity=".42"/>
            <g fill="#71CDA5" opacity=".34">
              <rect x="170" y="454" width="10" height="45"/><rect x="205" y="430" width="10" height="56"/>
              <rect x="240" y="405" width="10" height="48"/><rect x="275" y="370" width="10" height="64"/>
              <rect x="310" y="350" width="10" height="58"/><rect x="345" y="315" width="10" height="70"/>
              <rect x="380" y="285" width="10" height="61"/><rect x="415" y="248" width="10" height="74"/>
              <rect x="450" y="220" width="10" height="66"/><rect x="485" y="184" width="10" height="77"/>
              <rect x="520" y="151" width="10" height="70"/><rect x="555" y="112" width="10" height="82"/>
              <rect x="590" y="86" width="10" height="73"/><rect x="625" y="42" width="10" height="94"/>
            </g>
          </svg>
        </div>
        <section class="landing-hero">
          <div class="hero-approved-logo"><img src="{LOGO_DATA_URI}" alt="Equity Compass" draggable="false"></div>
          <h1>Navigate markets. <em>Research with confidence.</em></h1>
          <p>Financial data, SEC filings and credible news—organized into clear, evidence-first research.</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown('<div class="dashboard-mode"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<a class="brand-lockup" href="?home=1" target="_self" aria-label="Return to Equity Compass home"><img src="{FAVICON_DATA_URI}" alt="" draggable="false"><span>Equity Compass</span></a>',
        unsafe_allow_html=True,
    )


def select_popular_ticker(ticker_symbol: str) -> None:
    st.session_state["company_search"] = ticker_symbol
    st.session_state["popular_search_requested"] = True


with st.form("company-search", clear_on_submit=False):
    if not has_dashboard:
        st.markdown(
            """
            <div class="search-panel-heading">
              <div class="search-panel-icon">⌕</div>
              <div><strong>Search a U.S. public company</strong><span>Enter a company name or ticker to begin your research</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div class="company-switcher-label">
              <span class="company-switcher-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
                  <circle cx="11" cy="11" r="6.5"></circle><path d="m16 16 4 4"></path>
                </svg>
              </span>
              <span>Company search</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    search_column, action_column = st.columns([6, 1.35], gap="small")
    with search_column:
        search_query = st.text_input(
            "Company name or ticker",
            key="company_search",
            placeholder="Search Apple, NVIDIA, Microsoft or a ticker…",
        )
    with action_column:
        search_submitted = st.form_submit_button(
            "Analyze  →", type="primary", use_container_width=True
        )

if not has_dashboard:
    st.markdown('<div class="popular-label">Popular companies</div>', unsafe_allow_html=True)
    chip_spacer_left, *chip_columns, chip_spacer_right = st.columns(
        [0.65, 1, 1, 1, 1, 1, 1, 1, 0.65], gap="small"
    )
    for chip_column, popular_ticker in zip(chip_columns, POPULAR_TICKERS):
        with chip_column:
            st.button(
                popular_ticker,
                key=f"popular-{popular_ticker}",
                use_container_width=True,
                on_click=select_popular_ticker,
                args=(popular_ticker,),
            )

popular_search_requested = st.session_state.pop("popular_search_requested", False)
if search_submitted or popular_search_requested or restore_from_url:
    cleaned_query = st.session_state.get("company_search", "").strip()
    if not cleaned_query:
        st.warning("Enter a company name or ticker to begin.")
    else:
        progress_message = st.empty()
        try:
            company = resolve_search_query(cleaned_query)
        except CompanyLookupError as error:
            st.warning(str(error))
        else:
            ticker = company.ticker
            eligibility = load_ticker_eligibility(ticker)
            if not eligibility.supported:
                st.warning(eligibility.message)
            else:
                stage_ranges = {
                    "Annual data": (8, 45, "Annual report"),
                    "Quarterly data": (46, 66, "Quarterly update"),
                    "News": (67, 82, "Company news"),
                    "8-K events": (83, 96, "Recent company events"),
                }
                stage_labels = [item[2] for item in stage_ranges.values()]
                research_loader = st.empty()
                loader_state = {
                    "percent": 5,
                    "stage_index": 0,
                    "detail": "Confirming company and SEC coverage",
                }

                def render_research_loader(
                    percent: int, stage_index: int, detail: str
                ) -> None:
                    loader_state.update(
                        percent=percent, stage_index=stage_index, detail=detail
                    )
                    stages_html = "".join(
                        (
                            f'<div class="loader-stage '
                            f'{"done" if index < stage_index else "active" if index == stage_index else ""}">'
                            f'<i></i>{html.escape(label)}</div>'
                        )
                        for index, label in enumerate(stage_labels)
                    )
                    is_complete = percent >= 100
                    loader_class = "research-loader is-complete" if is_complete else "research-loader"
                    loader_kicker = (
                        "RESEARCH WORKSPACE READY"
                        if is_complete
                        else "BUILDING RESEARCH WORKSPACE"
                    )
                    task_label = "Ready:" if is_complete else "Now working:"
                    research_loader.markdown(
                        f"""
                        <div class="{loader_class}">
                          <div class="loader-heading">
                            <div class="loader-brand">
                              <svg class="loader-compass" viewBox="0 0 64 64" role="img" aria-label="Compass searching">
                                <circle cx="32" cy="32" r="28" fill="#F7FCFA" stroke="#17324B" stroke-width="4"/>
                                <circle cx="32" cy="32" r="21.5" fill="none" stroke="#B8DED2" stroke-width="1.8"/>
                                <path d="M32 1.5l3.2 8h-6.4z M62.5 32l-8 3.2v-6.4z M32 62.5l-3.2-8h6.4z M1.5 32l8-3.2v6.4z" fill="#17324B"/>
                                <g class="compass-needle">
                                  <path d="M32 9.5l5.2 22.5H32z" fill="#0A8F6A"/>
                                  <path d="M32 54.5L26.8 32H32z" fill="#17324B"/>
                                  <path d="M32 9.5L26.8 32H32z" fill="#35B88C" opacity=".58"/>
                                  <path d="M32 54.5L37.2 32H32z" fill="#274A65" opacity=".55"/>
                                </g>
                                <circle cx="32" cy="32" r="5.2" fill="#FFFFFF" stroke="#17324B" stroke-width="2.5"/>
                                <circle cx="32" cy="32" r="1.8" fill="#0A8F6A"/>
                              </svg>
                              <div><span class="loader-kicker">{loader_kicker}</span><h3>{html.escape(eligibility.company_name)}</h3></div>
                            </div>
                            <div class="loader-percent">{percent}%</div>
                          </div>
                          <div class="loader-progress"><span style="width:{percent}%"></span></div>
                          <div class="loader-stages">{stages_html}</div>
                          <p class="loader-task"><strong>{task_label}</strong> {html.escape(detail)}</p>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                render_research_loader(5, 0, "Confirming company and SEC coverage")

                try:
                    def show_research_progress(message: str) -> None:
                        """Show one branded overall indicator for the research pipeline."""
                        lower, upper, stage_index = 5, 96, 0
                        detail = message
                        for index, (prefix, (start, end, _label)) in enumerate(
                            stage_ranges.items()
                        ):
                            if message.startswith(prefix):
                                lower, upper, stage_index = start, end, index
                                detail = message.split(":", 1)[-1].strip()
                                break

                        starting_stage = {
                            "Starting annual": 0,
                            "Starting quarterly": 1,
                            "Starting recent news": 2,
                            "Starting recent 8-K": 3,
                        }
                        for prefix, index in starting_stage.items():
                            if message.startswith(prefix):
                                stage_index = index
                                lower, upper, _ = list(stage_ranges.values())[index]
                                detail = message.removeprefix("Starting ").strip()
                                break

                        fraction = re.search(r"(\d+)/(\d+)", detail)
                        if fraction:
                            current, total = map(int, fraction.groups())
                            percent = lower + round((upper - lower) * current / total)
                            detail = re.sub(r"^\d+/\d+\s*", "", detail)
                        else:
                            percent = lower

                        render_research_loader(
                            min(percent, 96), stage_index, detail
                        )

                    dashboard_summary = load_dashboard_analysis(
                        ticker, progress=show_research_progress
                    )
                except DashboardError as error:
                    progress_message.empty()
                    research_loader.empty()
                    st.error(f"Equity Compass could not complete the analysis: {error}")
                else:
                    progress_message.empty()
                    final_start = int(loader_state["percent"])
                    for percent in range(final_start + 1, 101, 3):
                        render_research_loader(
                            min(percent, 100),
                            min(len(stage_labels), int(loader_state["stage_index"]) + 1),
                            "Finalizing the research workspace",
                        )
                        time.sleep(0.035)
                    render_research_loader(
                        100, len(stage_labels), "Research workspace ready"
                    )
                    time.sleep(0.58)
                    st.session_state[f"price-range-{dashboard_summary.ticker}"] = "1D"
                    st.session_state["dashboard_summary"] = dashboard_summary
                    st.session_state["financials_schema_version"] = FINANCIALS_SCHEMA_VERSION
                    st.query_params["ticker"] = dashboard_summary.ticker
                    st.rerun()

if not has_dashboard:
    st.markdown(
        '<section class="research-preview"><div class="research-preview-intro">What your research includes</div></section>',
        unsafe_allow_html=True,
    )
    preview_columns = st.columns(4, gap="small")
    preview_cards = (
        ("▥", "Financials", "Revenue, profitability, cash flow and key ratios."),
        ("▤", "SEC Filings", "10-K, 10-Q and 8-K information from official filings."),
        ("◎", "Company News", "Recent developments from credible sources."),
        ("⌖", "Clear Analysis", "Complex financial information explained plainly."),
    )
    for column, (icon, title, description) in zip(preview_columns, preview_cards):
        column.markdown(
            f'<div class="preview-card"><span class="preview-icon">{icon}</span><strong>{title}</strong><p>{description}</p></div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        '<div class="landing-disclaimer">◈&nbsp;&nbsp; Equity Compass is for <strong>education and research purposes only</strong>, not financial advice.</div>',
        unsafe_allow_html=True,
    )

if "dashboard_summary" in st.session_state:
    show_dashboard(st.session_state["dashboard_summary"])

if has_dashboard:
    st.caption("Equity Compass is for education and research, not financial advice.")

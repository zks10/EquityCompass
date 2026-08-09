"""Equity Compass Streamlit dashboard."""

import base64
import html
import re
import time
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
from finance_news.sec_companies import CompanyLookupError, resolve_company_query

FINANCIALS_SCHEMA_VERSION = 2
POPULAR_TICKERS = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
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


def show_news_article(article: RecentNewsArticle, key: str) -> None:
    """Display one normalized news article."""
    st.markdown(f"**{article.title}**")
    st.caption(f"{article.publisher} · {article.published_at}")
    st.link_button("Read article", article.url, key=key)


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
    """Display annual and quarterly filing guidance and source text."""
    st.subheader("SEC Filing Overview")
    st.caption(f"SEC CIK: {summary.cik}")
    ten_k, ten_q = st.columns(2)
    ten_k.metric("Latest 10-K", summary.latest_10k_date)
    ten_q.metric("Latest 10-Q", summary.latest_10q_date)
    st.divider()
    st.subheader("Latest 10-K Sections")
    st.write(
        "A 10-K is the company's detailed annual report to the SEC. It explains the "
        "business, major risks, and management's view of annual performance."
    )
    st.caption(
        "Extracted directly from the latest annual SEC filing; not summarized or analyzed."
    )
    annual_sections = (
        ("Business", "how the company makes money", summary.annual_sections.business),
        ("Risk Factors", "what could hurt the business", summary.annual_sections.risk_factors),
        ("MD&A", "management's explanation of performance", summary.annual_sections.mda),
    )
    available_annual = tuple(section for section in annual_sections if section[2])
    if available_annual:
        st.markdown("**Quick previews from the filing**")
        for label, _description, content in available_annual:
            st.markdown(f"**{label}:** " + build_filing_preview(content))
        st.caption(
            "These previews select sentences from the filing. They are incomplete; "
            "open each section for full context."
        )
        for label, description, content in available_annual:
            with st.expander(f"{label} — {description}"):
                st.write(content)
    else:
        st.info("The 10-K is available, but its main text sections could not be separated reliably.")

    st.divider()
    st.subheader("Latest 10-Q Sections")
    st.write(
        "A 10-Q is a shorter quarterly update. Use it to spot recent changes in "
        "performance, liquidity, and risks since the annual report."
    )
    st.caption(
        "Extracted directly from the latest quarterly SEC filing; not summarized or analyzed."
    )
    quarterly_sections = (
        ("Quarterly MD&A", "what changed this quarter", summary.quarterly_sections.mda),
        ("Quarterly Risk Factors", "updated risks", summary.quarterly_sections.risk_factors),
    )
    available_quarterly = tuple(
        section for section in quarterly_sections if section[2]
    )
    if available_quarterly:
        st.markdown("**Quick previews from the filing**")
        for label, _description, content in available_quarterly:
            st.markdown(f"**{label}:** " + build_filing_preview(content))
        st.caption(
            "These previews select sentences from the filing. They are incomplete; "
            "open each section for full context."
        )
        for label, description, content in available_quarterly:
            with st.expander(f"{label} — {description}"):
                st.write(content)
    else:
        st.info("No reliably extracted quarterly filing sections are available.")


def show_news_and_events(summary: DashboardSummary) -> None:
    """Display recent headlines and material 8-K events."""
    st.subheader("Recent News")
    st.write(
        "News can explain recent price movement, opportunities, or risks. Headlines "
        "are not proof that a company is a good or bad investment."
    )
    news_topics = detect_news_topics(summary.recent_news)
    if news_topics:
        st.markdown("**Topics appearing in recent headlines**")
        st.write(
            " · ".join(
                f"{topic.label}: {topic.article_count}" for topic in news_topics
            )
        )
        st.caption(
            "Detected from headline keywords. One article can appear in multiple "
            "topics; this is not sentiment analysis."
        )
    if not summary.recent_news:
        st.info("No recent articles were found for this company.")
    for index, article in enumerate(summary.recent_news[:5]):
        show_news_article(article, key=f"news-{index}")
    remaining_news = summary.recent_news[5:]
    if remaining_news:
        with st.expander(f"Show {len(remaining_news)} more recent articles"):
            for index, article in enumerate(remaining_news, start=5):
                show_news_article(article, key=f"news-{index}")

    st.divider()
    st.subheader("Recent 8-K Events")
    st.write(
        "An 8-K reports an important event between regular quarterly and annual "
        "reports. The item number identifies the event category."
    )
    st.caption(
        "Extracted from the three most recent 8-K filings; not ranked or analyzed."
    )
    if not summary.recent_events:
        st.info("No recent extractable 8-K events were found for this company.")
    for filing in summary.recent_events:
        st.write(
            f"8-K filed {filing.filing_date} · {len(filing.items)} extracted item(s)"
        )
        st.link_button(
            "Open SEC filing",
            filing.document_url,
            key=f"8k-{filing.accession_number}",
        )
        for item in filing.items:
            item_title = f" — {item.title}" if item.title else ""
            with st.expander(
                f"{filing.filing_date} · Item {item.item_number}{item_title}"
            ):
                st.info(explain_8k_item(item.item_number))
                st.write(item.text)


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
    overview_tab, financials_tab, filings_tab, news_tab = st.tabs(
        ["Overview", "Financials", "Filings", "News & Events"]
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
        padding-bottom: 16px;
        border-bottom: 1px solid rgba(16, 42, 67, 0.07);
        color: #102A43;
        font-size: 1.05rem;
        font-weight: 780;
        letter-spacing: -0.02em;
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
    @media (max-width: 640px) {
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

has_dashboard = "dashboard_summary" in st.session_state
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
        f'<div class="brand-lockup"><img src="{FAVICON_DATA_URI}" alt="" draggable="false"><span>Equity Compass</span></div>',
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
if search_submitted or popular_search_requested:
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

                    dashboard_summary = analyze_ticker(
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

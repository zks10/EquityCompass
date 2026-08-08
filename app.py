"""Equity Compass Streamlit dashboard."""

import html

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
    build_financial_insights,
    build_financial_snapshot_score,
    detect_news_topics,
    explain_8k_item,
)
from finance_news.market_data import (
    MarketDataError,
    MarketOverview,
    fetch_market_overview,
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


def short_signal_help(component_name: str, value: float | None) -> str:
    """Return one short, beginner-friendly explanation for a signal card."""
    if value is None:
        return "There is not enough data to calculate this signal."
    if component_name == "Revenue growth":
        return f"Revenue changed {value:.1f}% from the previous year."
    if component_name == "Net profit margin":
        return f"About ${value:.0f} of net profit came from each $100 of revenue."
    if component_name == "Liabilities / assets":
        return f"Liabilities equal {value:.1f}% of assets; industry context matters."
    return f"Operations produced about ${value:.0f} of cash per $100 of revenue."


def show_overview(summary: DashboardSummary) -> None:
    """Display the market story first, then curated cross-section takeaways."""
    financials = summary.financials
    snapshot_score = build_financial_snapshot_score(financials)
    st.caption(f"{summary.ticker} · U.S. public company")
    st.header(summary.company_name)

    try:
        market = load_market_overview(summary.ticker)
    except MarketDataError:
        market = None

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

    st.subheader("Company highlights")
    latest_event = summary.recent_events[0] if summary.recent_events else None
    event_item = latest_event.items[0] if latest_event and latest_event.items else None
    event_headlines = {
        "2.02": "New financial results",
        "5.02": "Leadership change",
        "8.01": "Important company update",
        "9.01": "Supporting filing documents",
    }
    event_name = (
        event_headlines.get(
            event_item.item_number,
            event_item.title or "New official company update",
        )
        if event_item
        else "No recent official update"
    )
    event_description = (
        "Updated operating performance was reported."
        if event_item
        else "No recent 8-K event was collected."
    )
    event_meta = f"8-K · {latest_event.filing_date}" if latest_event else "SEC filing"
    event_link = (
        f'<a class="highlight-link" href="{html.escape(latest_event.document_url, quote=True)}" '
        'target="_blank">View filing</a>'
        if latest_event
        else ""
    )

    article = summary.recent_news[0] if summary.recent_news else None
    news_title = article.title if article else "No recent headline"
    if article:
        news_title = news_title.removesuffix(f" - {article.publisher}")
    news_description = (
        ""
        if article
        else "No recent company news was collected."
    )
    news_meta = (
        f"{article.publisher} · {article.published_at[:10]}" if article else "Recent news"
    )
    news_link = (
        f'<a class="highlight-link" href="{html.escape(article.url, quote=True)}" '
        'target="_blank">Read article</a>'
        if article
        else ""
    )
    component_names = {
        "Revenue growth": "Revenue growth",
        "Net profit margin": "Profitability",
        "Liabilities / assets": "Balance sheet",
        "Operating cash flow margin": "Cash generation",
    }
    available_highlight_components = [
        component for component in snapshot_score.components if component.score is not None
    ]
    if available_highlight_components:
        highest_score = max(component.score or 0 for component in available_highlight_components)
        lowest_component = min(
            available_highlight_components, key=lambda component: component.score or 0
        )
        strongest_names = [
            component_names[component.name]
            for component in available_highlight_components
            if component.score == highest_score
        ]
        strongest_text = " and ".join(strongest_names)
        watch_text = component_names[lowest_component.name]
    st.markdown(
        f"""
        <div class="highlight-panel">
          <div class="highlight-row">
            <div class="highlight-icon highlight-positive">↗</div>
            <div class="highlight-copy">
              <div class="highlight-label">FUNDAMENTAL TAKEAWAY</div>
              <div class="takeaway-grid">
                <div class="takeaway-item"><span class="takeaway-dot takeaway-strength"></span><div><small>STRENGTH</small><strong>{html.escape(strongest_text if available_highlight_components else 'Unavailable')}</strong></div></div>
                <div class="takeaway-item"><span class="takeaway-dot takeaway-risk"></span><div><small>WATCH AREA</small><strong>{html.escape(watch_text if available_highlight_components else 'Unavailable')}</strong></div></div>
              </div>
            </div>
          </div>
          <div class="highlight-row">
            <div class="highlight-icon highlight-official">▤</div>
            <div class="highlight-copy">
              <div class="highlight-label">OFFICIAL COMPANY UPDATE</div>
              <div class="highlight-title">{html.escape(event_name)}</div>
              <div class="highlight-description">{html.escape(event_description)}</div>
              <div class="highlight-meta">{html.escape(event_meta)} · {event_link}</div>
            </div>
          </div>
          <div class="highlight-row">
            <div class="highlight-icon highlight-news">●</div>
            <div class="highlight-copy">
              <div class="highlight-label">LATEST COVERAGE</div>
              <div class="highlight-title">{html.escape(news_title)}</div>
              <div class="highlight-description">{html.escape(news_description)}</div>
              <div class="highlight-meta">{html.escape(news_meta)} · {news_link}</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.subheader("Financial signal breakdown")
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
        score_help = (
            "Overall score: equal-weight average. Revenue growth: -10%=0, 0%=50, +10%=100. "
            "Net and cash margins: 0%=0, 25%=100. Liabilities/assets: 40%=100, 100%=0."
        )
        st.markdown(
            f'<table class="factor-table"><thead><tr><th>Factor</th><th>Current result</th><th>Score <span class="score-info" title="{html.escape(score_help, quote=True)}">?</span></th><th>Assessment</th></tr></thead><tbody>{"".join(factor_rows)}</tbody></table>',
            unsafe_allow_html=True,
        )
        st.caption(
            "Latest annual SEC figures. Excludes valuation and forward estimates."
        )


def show_financials(summary: DashboardSummary) -> None:
    """Display current annual metrics and five-year history."""
    financials = summary.financials
    st.subheader("Financial Overview")
    st.caption(
        f"Fiscal year {financials.fiscal_year}, ended {financials.period_end} · "
        "Annual SEC figures"
    )

    revenue, income, assets = st.columns(3)
    revenue.metric("Revenue", format_usd(financials.revenue))
    income.metric("Net income", format_usd(financials.net_income))
    assets.metric("Total assets", format_usd(financials.assets))

    liabilities, cash_flow = st.columns(2)
    liabilities.metric("Total liabilities", format_usd(financials.liabilities))
    cash_flow.metric(
        "Operating cash flow", format_usd(financials.operating_cash_flow)
    )

    with st.expander("What do these financial numbers mean?"):
        st.markdown(
            "- **Revenue:** money earned from selling products or services before expenses.\n"
            "- **Net income:** profit left after expenses and taxes.\n"
            "- **Total assets:** resources the company owns or controls.\n"
            "- **Total liabilities:** amounts the company owes or must pay.\n"
            "- **Operating cash flow:** cash generated by normal business operations."
        )

    st.subheader("Financial Ratios")
    growth, margin, debt, cash_margin = st.columns(4)
    growth.metric("Revenue growth", format_percent(financials.revenue_growth_percent))
    margin.metric(
        "Net profit margin", format_percent(financials.net_profit_margin_percent)
    )
    debt.metric(
        "Liabilities / assets",
        format_percent(financials.liabilities_to_assets_percent),
    )
    cash_margin.metric(
        "Operating cash flow margin",
        format_percent(financials.operating_cash_flow_margin_percent),
    )

    with st.expander("What do these percentages mean?"):
        st.markdown(
            "- **Revenue growth:** change in revenue from the previous fiscal year.\n"
            "- **Net profit margin:** net profit earned from each $100 of revenue.\n"
            "- **Liabilities / assets:** the share of assets matched by liabilities.\n"
            "- **Operating cash flow margin:** operating cash produced from each $100 of revenue."
        )

    st.subheader("Five-Year Financial History")
    history = pd.DataFrame(
        [
            {
                "Fiscal year": row.fiscal_year,
                "Period end": row.period_end,
                "Revenue": row.revenue,
                "Net income": row.net_income,
                "Total assets": row.assets,
                "Total liabilities": row.liabilities,
                "Operating cash flow": row.operating_cash_flow,
            }
            for row in summary.financial_history
        ]
    )
    displayed_history = history.copy()
    money_columns = [
        "Revenue",
        "Net income",
        "Total assets",
        "Total liabilities",
        "Operating cash flow",
    ]
    for column in money_columns:
        displayed_history[column] = displayed_history[column].map(format_usd)
    st.dataframe(displayed_history, hide_index=True, width="stretch")

    chart_history = history.sort_values("Fiscal year").set_index("Fiscal year")
    st.write("Revenue and net income")
    st.line_chart(chart_history[["Revenue", "Net income"]])
    st.write("Assets and liabilities")
    st.line_chart(chart_history[["Total assets", "Total liabilities"]])


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
    st.markdown("**Quick previews from the filing**")
    st.markdown(
        "**Business:** " + build_filing_preview(summary.annual_sections.business)
    )
    st.markdown(
        "**Risk Factors:** "
        + build_filing_preview(summary.annual_sections.risk_factors)
    )
    st.markdown("**MD&A:** " + build_filing_preview(summary.annual_sections.mda))
    st.caption(
        "These previews select sentences from the filing. They are incomplete; open "
        "each section for full context."
    )
    with st.expander("Business — how the company makes money"):
        st.write(summary.annual_sections.business)
    with st.expander("Risk Factors — what could hurt the business"):
        st.write(summary.annual_sections.risk_factors)
    with st.expander("MD&A — management's explanation of performance"):
        st.write(summary.annual_sections.mda)

    st.divider()
    st.subheader("Latest 10-Q Sections")
    st.write(
        "A 10-Q is a shorter quarterly update. Use it to spot recent changes in "
        "performance, liquidity, and risks since the annual report."
    )
    st.caption(
        "Extracted directly from the latest quarterly SEC filing; not summarized or analyzed."
    )
    st.markdown("**Quick previews from the filing**")
    st.markdown(
        "**Quarterly MD&A:** "
        + build_filing_preview(summary.quarterly_sections.mda)
    )
    st.markdown(
        "**Quarterly risks:** "
        + build_filing_preview(summary.quarterly_sections.risk_factors)
    )
    st.caption(
        "These previews select sentences from the filing. They are incomplete; open "
        "each section for full context."
    )
    with st.expander("Quarterly MD&A — what changed this quarter"):
        st.write(summary.quarterly_sections.mda)
    with st.expander("Quarterly Risk Factors — updated risks"):
        st.write(summary.quarterly_sections.risk_factors)


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


st.set_page_config(page_title="Equity Compass", page_icon="📈")

st.markdown(
    """
    <style>
    [data-testid="stHeaderActionElements"] { display: none !important; }
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
    .takeaway-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 14px;
        margin-top: 8px;
    }
    .takeaway-item {
        display: flex;
        align-items: center;
        gap: 9px;
        padding: 11px 13px;
        border: 1px solid rgba(120, 130, 150, 0.18);
        border-radius: 9px;
        background: rgba(120, 130, 150, 0.035);
    }
    .takeaway-item small {
        display: block;
        color: #8A919D;
        font-size: 0.66rem;
        font-weight: 750;
        letter-spacing: 0.045em;
    }
    .takeaway-item strong { display: block; margin-top: 1px; }
    .takeaway-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 9px; }
    .takeaway-strength { background: #0A8F6A; }
    .takeaway-risk { background: #D05B68; }
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
    .summary-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 9px; }
    .summary-strong { background: #0A8F6A; }
    .summary-watch { background: #D05B68; }
    .factor-table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        border: 1px solid rgba(120, 130, 150, 0.25);
        border-radius: 12px;
        overflow: hidden;
    }
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
    @media (max-width: 640px) {
        .highlight-row { padding: 17px 16px; gap: 12px; }
        .highlight-badge { font-size: 0.82rem; }
        .takeaway-grid { grid-template-columns: 1fr; }
        .factor-table { font-size: 0.82rem; }
        .factor-table th, .factor-row td { padding-left: 9px; padding-right: 9px; }
        .factor-reading { padding: 3px 6px; }
        .score-summary { padding: 4px 2px 12px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📈 Equity Compass")
st.write("Enter a U.S. public-company ticker to collect its latest research data.")

ticker = st.text_input(
    "Ticker",
    value="AAPL",
    placeholder="Examples: AAPL, MSFT, NVDA",
).strip().upper()

if st.button("Analyze", type="primary"):
    if not ticker:
        st.warning("Please enter a ticker symbol.")
    else:
        progress_message = st.empty()
        try:
            with st.spinner(f"Analyzing {ticker}..."):
                dashboard_summary = analyze_ticker(
                    ticker, progress=progress_message.write
                )
        except DashboardError as error:
            progress_message.empty()
            st.error(f"Equity Compass could not complete the analysis: {error}")
        else:
            progress_message.empty()
            st.session_state[f"price-range-{dashboard_summary.ticker}"] = "1D"
            st.session_state["dashboard_summary"] = dashboard_summary

if "dashboard_summary" in st.session_state:
    show_dashboard(st.session_state["dashboard_summary"])

st.caption("Equity Compass is for education and research, not financial advice.")

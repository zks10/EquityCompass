"""Equity Compass Streamlit dashboard."""

import streamlit as st

from finance_news.dashboard import DashboardError, analyze_ticker


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


st.set_page_config(page_title="Equity Compass", page_icon="📈")

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
                summary = analyze_ticker(ticker, progress=progress_message.write)
        except DashboardError as error:
            progress_message.empty()
            st.error(f"Equity Compass could not complete the analysis: {error}")
        else:
            progress_message.empty()
            st.subheader(summary.company_name)
            st.write(f"SEC CIK: {summary.cik}")

            ten_k, ten_q, news = st.columns(3)
            ten_k.metric("Latest 10-K", summary.latest_10k_date)
            ten_q.metric("Latest 10-Q", summary.latest_10q_date)
            news.metric("Recent news articles", summary.news_article_count)

            financials = summary.financials
            st.subheader("Financial Overview")
            st.caption(
                f"Fiscal year {financials.fiscal_year}, ended "
                f"{financials.period_end} · Annual SEC figures"
            )

            revenue, income, assets = st.columns(3)
            revenue.metric("Revenue", format_usd(financials.revenue))
            income.metric("Net income", format_usd(financials.net_income))
            assets.metric("Total assets", format_usd(financials.assets))

            liabilities, cash_flow = st.columns(2)
            liabilities.metric(
                "Total liabilities", format_usd(financials.liabilities)
            )
            cash_flow.metric(
                "Operating cash flow", format_usd(financials.operating_cash_flow)
            )

            st.subheader("Financial Ratios")
            growth, margin, debt, cash_margin = st.columns(4)
            growth.metric(
                "Revenue growth",
                format_percent(financials.revenue_growth_percent),
            )
            margin.metric(
                "Net profit margin",
                format_percent(financials.net_profit_margin_percent),
            )
            debt.metric(
                "Liabilities / assets",
                format_percent(financials.liabilities_to_assets_percent),
            )
            cash_margin.metric(
                "Operating cash flow margin",
                format_percent(financials.operating_cash_flow_margin_percent),
            )

st.caption("Equity Compass is for education and research, not financial advice.")

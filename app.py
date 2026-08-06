import pandas as pd
import plotly.express as px
import streamlit as st
import yfinance as yf


st.set_page_config(page_title="StockLens", page_icon="📈", layout="wide")

st.title("📈 StockLens")
st.write("Explore a stock's recent price history in a beginner-friendly way.")

ticker = st.text_input(
    "Enter a stock ticker",
    value="AAPL",
    placeholder="Examples: AAPL, MSFT, SHOP.TO",
).strip().upper()

analyze = st.button("Analyze stock", type="primary")

if analyze:
    if not ticker:
        st.warning("Please enter a ticker symbol.")
    else:
        with st.spinner(f"Loading market data for {ticker}..."):
            try:
                history = yf.download(
                    ticker,
                    period="1mo",
                    interval="1d",
                    progress=False,
                    auto_adjust=True,
                )
            except Exception as error:
                st.error("StockLens could not connect to the market-data service.")
                st.caption(f"Technical details: {error}")
                history = pd.DataFrame()

        if history.empty:
            st.error(
                f"No price data was found for {ticker}. "
                "Check the ticker and try again."
            )
        else:
            close_prices = history["Close"].squeeze()
            current_price = float(close_prices.iloc[-1])
            starting_price = float(close_prices.iloc[0])
            price_change = current_price - starting_price
            percent_change = (price_change / starting_price) * 100

            price_column, change_column = st.columns(2)
            price_column.metric("Latest closing price", f"${current_price:,.2f}")
            change_column.metric(
                "Change over the last month",
                f"{percent_change:+.2f}%",
                f"${price_change:+,.2f}",
            )

            chart_data = close_prices.rename("Closing price").reset_index()
            date_column = chart_data.columns[0]
            chart = px.line(
                chart_data,
                x=date_column,
                y="Closing price",
                title=f"{ticker} closing price — last month",
                labels={date_column: "Date", "Closing price": "Price (USD)"},
            )
            chart.update_traces(line_color="#2E7DFF", line_width=3)
            chart.update_layout(hovermode="x unified")
            st.plotly_chart(chart, use_container_width=True)

            st.info(
                "The latest closing price is the price recorded at the end of the "
                "most recent trading day. Past performance does not predict future results."
            )

st.caption("StockLens is for education and research, not financial advice.")

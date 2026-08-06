# StockLens

StockLens is a beginner-friendly stock research project. Phase 1 includes a small
command-line tool that resolves a ticker such as `AAPL` to the company's official
name and SEC Central Index Key (CIK), using the SEC company tickers dataset.

The existing Streamlit price-chart prototype remains available separately. The
SEC resolver does not collect financial statements, news, or perform AI analysis.

## Set up

```bash
cd /Users/kevinzhu/Desktop/StockLens
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

## Resolve a company ticker

The SEC asks automated clients to identify themselves. Set your application name
and contact email, then run the resolver:

```bash
export SEC_USER_AGENT="StockLens your-email@example.com"
python3 -m finance_news.cli AAPL
```

Expected output:

```text
Ticker: AAPL
Company: Apple Inc.
SEC CIK: 0000320193
```

After installation, the equivalent shorter command is:

```bash
resolve-company AAPL
```

Ticker input is case-insensitive. Invalid tickers, connection problems, and
unexpected SEC responses produce a readable error and a non-zero exit status.

## Run the automated tests

The tests use simulated SEC responses, so they do not require internet access or
send repeated requests to the SEC:

```bash
python3 -m unittest discover -s tests -v
```

## Run the existing Streamlit prototype

```bash
streamlit run app.py
```

## Project structure

```text
StockLens/
├── finance_news/
│   ├── __init__.py
│   ├── cli.py
│   └── sec_companies.py
├── tests/
│   └── test_sec_companies.py
├── app.py
├── pyproject.toml
├── README.md
└── requirements.txt
```

StockLens is intended for education and research, not financial advice.

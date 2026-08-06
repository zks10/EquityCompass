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

## List recent SEC filings

Resolve a ticker and list its latest 10-K, 10-Q, and 8-K filing metadata:

```bash
python3 -m finance_news.filings_cli AAPL --limit 5
```

Each result includes the filing type, filing date, accession number, and a direct
link to the primary filing document. This step retrieves metadata only; it does
not download or parse the filing contents.

After installation, the equivalent shorter command is:

```bash
recent-filings AAPL --limit 5
```

## Download a raw SEC filing

Download the newest filing of a selected type to local raw-data storage:

```bash
download-filing AAPL --form 10-K
```

The default output path is organized by CIK and accession number under
`data/raw/sec/`. Existing nonempty files are reused; add `--force` to download
the document again. Downloaded filings are local source data and are excluded
from Git.

This step saves the SEC's original HTML only. It does not parse or analyze the
filing.

## Convert a filing to clean text

Process an already-downloaded filing without making another SEC request:

```bash
process-filing data/raw/sec/0000320193/000032019325000079/aapl-20250927.htm
```

The cleaned UTF-8 text is saved under the matching path:

```text
data/processed/sec/0000320193/000032019325000079/filing.txt
```

The processor removes scripts, styles, comments, hidden XBRL metadata, and excess
whitespace while retaining readable text and inline financial values. Processed
files are generated local data and are excluded from Git.

## Extract major 10-K sections

Split a processed 10-K into Business, Risk Factors, and MD&A:

```bash
extract-sections data/processed/sec/0000320193/000032019325000079/filing.txt
```

The extracted files are saved beside the processed filing:

```text
sections/
├── business.txt
├── risk_factors.txt
└── mda.txt
```

The extractor recognizes SEC item headings and avoids selecting their short
table-of-contents entries. This step performs structural extraction only; it does
not summarize or analyze the sections.

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
│   ├── download_cli.py
│   ├── filing_downloader.py
│   ├── filing_processor.py
│   ├── filings_cli.py
│   ├── process_cli.py
│   ├── section_extractor.py
│   ├── sections_cli.py
│   ├── sec_filings.py
│   └── sec_companies.py
├── tests/
│   ├── test_filing_downloader.py
│   ├── test_filing_processor.py
│   ├── test_section_extractor.py
│   ├── test_sec_filings.py
│   └── test_sec_companies.py
├── app.py
├── pyproject.toml
├── README.md
└── requirements.txt
```

StockLens is intended for education and research, not financial advice.

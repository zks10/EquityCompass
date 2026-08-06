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

## Retrieve annual financial facts

Fetch the latest annual US-GAAP values reported on Form 10-K:

```bash
financial-facts AAPL
```

The collector retrieves revenue, net income, total assets, total liabilities,
and operating cash flow. It saves both the original SEC Company Facts response
and a normalized file:

```text
data/raw/sec/{CIK}/companyfacts.json
data/processed/sec/{CIK}/financial_facts.json
```

Every normalized value retains its SEC concept tag, unit, fiscal year, period
end, filing date, accession number, and form. This step collects reported facts
only; it does not calculate ratios or provide financial analysis.

## Retrieve multi-year financial history

Collect several annual 10-K periods for the same five metrics:

```bash
financial-history AAPL --years 5
```

The normalized history is saved to:

```text
data/processed/sec/{CIK}/financial_history.json
```

The collector deduplicates comparative facts repeated in later 10-K filings by
period end and retains the latest filing provenance. It records historical
values only; it does not calculate growth rates, trends, or conclusions.

## Calculate deterministic financial metrics

Calculate ratios from an already-stored financial history without another SEC
request:

```bash
calculate-metrics data/processed/sec/0000320193/financial_history.json
```

The calculator produces annual revenue growth, net profit margin,
liabilities-to-assets, and operating-cash-flow margin. Results and the exact
source values used are saved to:

```text
data/processed/sec/{CIK}/derived_metrics.json
```

The formulas are included in the output JSON. A zero denominator is stored as
`null`. These are mechanical calculations only, not investment conclusions or
AI analysis.

## Run the complete Phase 1 pipeline

Run all existing stages in order for any supported ticker:

```bash
stocklens-pipeline AAPL --years 5
```

The pipeline resolves the company, finds and downloads or reuses its latest
10-K, creates clean text, extracts Business/Risk Factors/MD&A, retrieves SEC XBRL
facts, saves annual history, and calculates deterministic metrics. Use
`--force-download` only when the raw 10-K should be downloaded again.

Each stage is displayed as it runs. If a stage fails, processing stops with that
stage's name; later outputs are not presented as complete.

## Collect the latest quarterly filing

Download and process the latest 10-Q for a supported ticker:

```bash
quarterly-pipeline AAPL
```

This five-stage collector resolves the company, finds and downloads or reuses
the latest 10-Q, creates clean text, and extracts quarterly MD&A and Risk Factors.
Use `--force-download` only to replace an existing raw quarterly filing.

This step collects quarterly filing text only. Quarterly XBRL calculations and
AI analysis are not included.

## Collect recent material-event filings

Collect and process the three most recent 8-K filings:

```bash
events-pipeline AAPL --limit 3
```

For each filing, the collector downloads or reuses the raw document, creates
clean text, detects and saves its `Item x.xx` sections, and records filing/item
provenance in:

```text
data/processed/sec/{CIK}/eight_k_events.json
```

Use `--force-download` only to replace existing raw 8-K documents. The collector
preserves all detected items, including exhibit metadata, without judging
materiality or performing AI analysis.

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
│   ├── derived_metrics.py
│   ├── event_extractor.py
│   ├── events_cli.py
│   ├── events_pipeline.py
│   ├── filing_downloader.py
│   ├── filing_processor.py
│   ├── filings_cli.py
│   ├── financial_facts.py
│   ├── facts_cli.py
│   ├── history_cli.py
│   ├── metrics_cli.py
│   ├── pipeline.py
│   ├── pipeline_cli.py
│   ├── quarterly_cli.py
│   ├── quarterly_pipeline.py
│   ├── process_cli.py
│   ├── section_extractor.py
│   ├── sections_cli.py
│   ├── sec_filings.py
│   └── sec_companies.py
├── tests/
│   ├── test_filing_downloader.py
│   ├── test_filing_processor.py
│   ├── test_derived_metrics.py
│   ├── test_event_extractor.py
│   ├── test_events_pipeline.py
│   ├── test_pipeline.py
│   ├── test_quarterly_pipeline.py
│   ├── test_financial_facts.py
│   ├── test_section_extractor.py
│   ├── test_sec_filings.py
│   └── test_sec_companies.py
├── app.py
├── pyproject.toml
├── README.md
└── requirements.txt
```

StockLens is intended for education and research, not financial advice.

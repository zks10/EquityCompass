# Phase 2: Equity Score Validation Plan

## The goal

Phase 1 built Equity Compass. Phase 2 will test whether its Equity Score is
actually useful.

The main question is:

> When a company received a higher Equity Score, did it usually have better
> results afterward than companies with lower scores?

This first experiment is a small pilot. It will help us confirm that the data and
testing process work before we expand to hundreds of companies.

## What stays unchanged

We will test the current Equity Score before changing it.

| Score part | Current rule |
| --- | --- |
| Revenue growth | −10% growth = 0 points, 0% = 50, +10% = 100 |
| Net profit margin | 0% margin = 0 points, 25% = 100 |
| Liabilities / assets | 40% = 100 points, 100% = 0 |
| Operating cash-flow margin | 0% margin = 0 points, 25% = 100 |

Each available part has the same weight. The final score is their average and is
kept between 0 and 100.

If fewer than three parts are available, the result is marked **Limited data** and
is not used in the main comparison.

## The 20-company pilot

The pilot uses four companies from five different areas of the economy.

| Area | Companies |
| --- | --- |
| Technology | AAPL, MSFT, NVDA, INTC |
| Banking | JPM, BAC, GS, C |
| Energy | XOM, CVX, COP, SLB |
| Healthcare | JNJ, PFE, UNH, MDT |
| Consumer | WMT, TGT, KO, NKE |

This is a learning sample, not final proof that the score works across the whole
market.

## Time period

For each company, we will try to recreate five historical annual scores.

The score becomes active on the date the related 10-K was filed—not on the last
day of the company’s fiscal year. This prevents us from using financial information
before investors could have seen it.

Example:

```text
10-K becomes public
        ↓
Calculate the Equity Score using only that filing
        ↓
Start measuring from the next market close
        ↓
Check the result 6, 12, and 24 months later
```

## What we will measure

The main measurement is the stock’s **12-month return after the filing**.

We will also record:

- 6-month return
- 24-month return
- S&P 500 return over the same dates
- Return above or below the S&P 500
- Largest price decline during the following 12 months

The S&P 500 comparison uses SPY as a simple market benchmark.

## How scores will be compared

Historical results will be placed into three groups:

| Group | Equity Score |
| --- | ---: |
| Higher | 75–100 |
| Middle | 50–74 |
| Lower | 0–49 |

We will compare each group’s typical return, market-beating rate, and largest
decline. We will report both the average and the middle result so one unusual
company does not control the conclusion.

## Rules for an honest test

1. Use only information available on or before the historical filing date.
2. Never use today’s financial values to calculate an older score.
3. Do not change the score formula during the first test.
4. Keep missing values missing; do not guess them.
5. Keep companies with poor results in the dataset.
6. Record every failed or incomplete company instead of silently removing it.
7. Publish the result even if the score performs poorly.

## What the pilot can tell us

The pilot can show whether:

- historical data can be recreated reliably;
- higher scores generally led to stronger results;
- one score component appears more or less useful;
- the score behaves very differently across industries; and
- a larger test is worth building.

It cannot prove that the score predicts future stock prices or that a stock should
be bought or sold.

## Build order

1. Save historical filing and financial records with their real publication dates.
2. Calculate the four score parts for every historical company-year.
3. Save one clear score snapshot for each filing date.
4. Add historical company and SPY prices.
5. Calculate the 6-, 12-, and 24-month results.
6. Produce a simple comparison report.
7. Review the evidence before changing the Equity Score.

## Pilot completion checklist

The pilot is complete when:

- all 20 companies were attempted;
- five historical years were attempted for each company;
- incomplete records are listed with a reason;
- every score can be traced back to its SEC filing;
- future returns use prices after the filing date;
- results are compared with SPY; and
- the baseline findings are saved before the score formula changes.

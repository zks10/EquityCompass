# Phase 2 Pilot: Frozen Baseline Conclusion

**Frozen:** August 15, 2026
**Scope:** 20 companies, five historical 10-K filings attempted per company

## Decision

Keep the current Equity Score unchanged in the application, but do not describe it
as validated for predicting future stock performance.

The pilot successfully proved that Equity Compass can reconstruct point-in-time
scores, connect them to later market results, and audit every source filing. It did
not find consistent evidence that higher scores led to better 12-month results after
industry effects were reduced.

## What was completed

- 20 pilot companies attempted across Technology, Banking, Energy, Healthcare, and Consumer
- 100 historical company-year score snapshots completed
- Every score tied to one specific 10-K accession and filing date
- Company and SPY returns measured from the first common market close after filing
- 6-, 12-, and 24-month horizons recorded only when they had elapsed
- Largest subsequent 12-month price decline recorded
- Baseline, component, industry, and within-industry comparisons completed

At the freeze date, 80 observations had a complete 12-month result and 20 newer
observations remained pending.

## Baseline 12-month result

| Score group | Records | Median return | Median vs. SPY | Beat SPY | Average max decline |
| --- | ---: | ---: | ---: | ---: | ---: |
| Higher (75–100) | 27 | 4.40% | +2.04% | 51.85% | −24.64% |
| Middle (50–74) | 35 | 10.63% | −2.74% | 45.71% | −22.82% |
| Lower (0–49) | 18 | 13.87% | −2.94% | 44.44% | −28.39% |

Higher scores beat SPY somewhat more often and had a positive median excess return,
but they did not have the highest raw returns. The pattern was not monotonic across
the three score groups.

## Industry finding

Industry differences were much larger than the relationships observed for individual
score components.

| Industry | Median return | Median vs. SPY | Beat SPY |
| --- | ---: | ---: | ---: |
| Technology | 23.55% | +7.34% | 75.00% |
| Banking | 17.68% | +5.55% | 62.50% |
| Energy | 13.01% | −2.70% | 50.00% |
| Consumer | 6.65% | −5.49% | 31.25% |
| Healthcare | 0.57% | −18.57% | 18.75% |

## Component finding

All component relationships with 12-month SPY-relative returns were weak.

| Component | Rank correlation with return vs. SPY |
| --- | ---: |
| Net profit margin | +0.085 |
| Revenue growth | +0.064 |
| Operating cash-flow margin | +0.001 |
| Liabilities / assets | −0.067 |

These descriptive correlations are too small and unstable to justify changing
component weights from this pilot alone.

## Industry-adjusted finding

Within the same economic area and fiscal year:

- the highest-scored company beat the lowest-scored company in 50% of cohorts;
- the median highest-minus-lowest excess-return difference was −1.32%; and
- the average within-cohort score/return rank correlation was −0.036.

This is the strongest reason not to claim that the current score predicts later
performance. The positive unadjusted result appears sensitive to sector composition
and a small number of large outcomes.

## Product recommendation

1. Keep the Equity Score as a transparent summary of current reported financial signals.
2. Do not market it as a forecast, expected return, buy/sell rating, or validated predictor.
3. Preserve the current formula until a larger preregistered test is designed.
4. Continue showing the component breakdown so users can inspect what drives the score.
5. Keep Phase 2 calculations offline so dashboard performance remains unchanged.

## Recommended next research phase

Before testing a revised score:

1. Expand the sample beyond 20 hand-selected large companies.
2. Predefine the expanded universe, exclusions, metrics, and evaluation rules.
3. Use industry-relative financial metrics rather than identical thresholds across sectors.
4. Separate development data from a final untouched holdout sample.
5. Account for repeated observations from the same company.
6. Re-run the frozen baseline on the expanded sample before testing alternatives.

Any experimental formula should live outside the production dashboard until it
outperforms this frozen baseline on unseen data.

## Limitations

- The pilot contains only four companies per economic area.
- The companies were selected deliberately and are not a random market sample.
- Multiple years from the same company are not independent observations.
- Twenty 12-month outcomes and forty 24-month outcomes were still pending.
- Adjusted Yahoo Finance prices were used for company and SPY comparisons.
- The findings describe this pilot and are not investment advice.

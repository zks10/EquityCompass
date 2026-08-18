# Phase 2 Expanded Study Preregistration

**Protocol version:** 1.0
**Registered:** August 15, 2026
**Status:** Design frozen before expanded-data results are collected

## Research question

Does an Equity Compass score calculated only from information available when a 10-K
was filed help distinguish stronger subsequent 12-month SPY-relative returns among
comparable U.S. public companies?

The primary goal is to test the frozen Phase 1 score. Experimental formulas may be
developed only on the development sample and must be finalized before the holdout is
opened.

## Primary outcome and estimand

The primary outcome is the adjusted-price return from the first common company/SPY
market close after the filing through the first common close on or after 12 calendar
months, minus the SPY return over those exact dates.

The primary estimand is the Spearman rank correlation between the frozen Equity Score
and 12-month SPY-relative return after removing the mean result for the same industry
and filing-calendar year.

Primary supporting comparisons are:

- median industry-adjusted excess return by score quintile;
- top-quintile minus bottom-quintile median excess return;
- percentage of industry-year cohorts in which the highest-scored company beats the lowest-scored company; and
- bootstrap confidence intervals clustered by company.

Six- and 24-month returns and 12-month maximum drawdown are secondary outcomes.

## Universe selection

The candidate universe is formed without looking at future returns.

A company-filing is eligible when all of the following are true:

1. The issuer filed Form 10-K with the SEC during the registered date windows.
2. The security is an exchange-listed U.S. common stock—not an ETF, fund, preferred share, warrant, or OTC-only security.
3. A permanent issuer identifier, ticker history, CIK, accession number, and filing date can be retained.
4. The filing contains at least three of the four frozen score components.
5. A company and SPY adjusted close exist on a common market date after filing.
6. The full primary 12-month outcome has elapsed by the registered outcome cutoff.

Companies that merge, delist, fail, or enter bankruptcy remain eligible. Their records
must not be silently removed. If a complete return cannot be constructed, the attempt
is retained with a predefined missing-data reason.

### Sampling rule

Attempt at least **300 distinct companies** and at least **1,500 completed 12-month
company-filing observations**. Include at least 20 companies in every broad industry
division used in the primary comparison.

If the eligible universe exceeds processing capacity, select companies using a frozen
deterministic random seed (`20260815`) after stratifying by industry division. Do not
select companies based on score, return, name recognition, survival, or data quality
beyond the eligibility rules above.

The exact attempted-company list and every exclusion reason must be frozen before
returns are joined to scores.

## Time split and holdout

Splits use the 10-K filing date, not fiscal-year labels.

| Partition | Filing dates | Permitted use |
| --- | --- | --- |
| Development | 2017-01-01 through 2021-12-31 | Diagnose and develop experimental formulas |
| Validation | 2022-01-01 through 2023-12-31 | Choose among fully specified development candidates once |
| Holdout | 2024-01-01 through 2025-08-15 | Final untouched evaluation only |

The outcome-data cutoff is **August 15, 2026**. A filing is included in the primary
analysis only if its 12-month horizon has elapsed by that date.

Rules for the holdout:

1. Do not calculate or inspect holdout returns during formula development.
2. Freeze code, component definitions, weights, thresholds, exclusions, and success criteria first.
3. Generate a checksum of the frozen model specification before opening the holdout.
4. Run the final holdout analysis once.
5. Report the result regardless of direction.
6. Any change after opening the holdout creates a new exploratory version and cannot replace the registered result.

## Frozen baseline score

The production score remains the equally weighted average of available components:

- revenue growth;
- net profit margin;
- liabilities divided by assets; and
- operating cash-flow margin.

The existing transformations and 0–100 bounds remain unchanged. Records with fewer
than three available components are excluded from the primary comparison and retained
in the attempt log.

## Industry-relative experimental metrics

Industry-relative metrics are experimental challengers, not production changes.

For every filing-calendar year:

1. Assign the issuer using its SEC SIC code as known at filing time.
2. Prefer the two-digit SIC major group when it contains at least 20 eligible observations.
3. Otherwise use the broader SIC division.
4. Calculate peer distributions using development-partition records only when developing a formula.
5. Convert each raw component to a percentile rank within its eligible peer group.
6. Preserve missing components as missing; do not replace them with an industry mean or zero.
7. For liabilities/assets, reverse the percentile direction so a lower ratio receives a higher relative score.
8. Average available percentiles with equal weights unless a different weighting rule was frozen before validation.

Peer statistics for validation and holdout must be estimated without using future
filings. A filing may use only peer records filed on or before its own filing date.

## Candidate models

The registered comparison allows these models:

1. **Frozen baseline:** current production score; primary benchmark.
2. **Industry-relative equal weight:** the four peer percentiles averaged equally.
3. **Development-only candidate:** at most one alternative weighting chosen entirely from development data and frozen before validation.

No additional candidate may be invented after validation results are viewed.

## Success criteria

An experimental model is eligible for further study only if all conditions hold:

1. Positive primary rank correlation in both validation and holdout.
2. Holdout top-quintile median industry-adjusted excess return exceeds the bottom quintile.
3. Holdout highest-score company beats the lowest-score company in more than 50% of eligible industry-year cohorts.
4. Direction is not dependent on one industry division or one company.
5. Results remain directionally consistent when each company and each industry are removed one at a time.
6. Data coverage is not materially worse than the frozen baseline.

These conditions indicate whether a model merits additional research. They do not
establish investability, causal validity, or a production change.

## Missing data and corporate events

- Keep every attempted filing in an audit manifest.
- Use predefined reasons: missing filing facts, fewer than three components, missing identifier history, missing initial price, incomplete horizon, or unrecoverable corporate action.
- Never substitute current ticker data for an unresolved historical security.
- Use adjusted prices for splits and distributions when available.
- For mergers, delistings, and bankruptcies, use a source capable of complete historical returns; otherwise retain the record as incomplete.
- Report coverage by partition, industry, score group, and missing-data reason.

## Statistical reporting

Report observation counts, company counts, means, medians, rank correlations, win
rates, and 95% company-clustered bootstrap confidence intervals. Show unadjusted and
industry-adjusted results. Do not rely on a single p-value or optimize thresholds to
obtain statistical significance.

Sensitivity checks are:

- leave-one-company-out;
- leave-one-industry-out;
- equal-weight one observation per company;
- winsorized returns at the registered 1st and 99th percentiles; and
- results using median rather than mean industry adjustment.

All sensitivity results are supporting analyses; the registered primary result remains
unchanged.

## Reproducibility and freeze requirements

Before collecting expanded outcomes, save:

- this protocol and its machine-readable configuration;
- the exact company and filing attempt manifest;
- the source identifier and filing-date mapping;
- code and dependency versions;
- deterministic sampling seed;
- score specification checksums; and
- a timestamped checksum manifest.

The original 20-company pilot remains a frozen exploratory baseline and is not reused
as the final holdout.

## Product boundary

This study runs offline. It must not execute during dashboard searches, change Phase 1
collection behavior, add user-facing latency, or alter the production Equity Score.
Any future production proposal requires a separate review after the registered holdout
result is published.

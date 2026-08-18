# Phase 2 Final Holdout Specification

This specification was frozen before any expanded-study holdout score or return was
calculated. The holdout contains filings dated January 1, 2024 through August 15,
2025.

## Final model

Use only the existing production financial snapshot score. It is the equally
weighted average of the available revenue-growth, profit-margin,
liabilities-to-assets, and operating-cash-flow-margin component scores. A filing
requires at least three available components. No alternative weighting or formula
will be tested in the final holdout.

## Outcome

The primary outcome is the 12-month adjusted-close return relative to SPY, measured
from the first common trading day after the 10-K filing date. The outcome-data
cutoff is August 15, 2026.

## Missing data

Use only the historically verified common-stock ticker recorded on the filing
cover. If the registered price source returns no adjusted daily close, classify the
record as `missing_initial_price_from_registered_source` and exclude it from the
return comparison. Do not substitute a current ticker or infer a successor
security. Report coverage overall and by score group, company, and industry.

## Final decision

Report the holdout result regardless of direction. No production score change is
allowed solely from this study. The score merits further research only if the
preregistered success criteria are met, including positive holdout rank correlation,
top-versus-bottom and within-industry consistency, leave-one-out robustness, and
acceptable coverage.

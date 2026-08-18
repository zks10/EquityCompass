# Phase 3 Plan: Score Context and Product Trust

## Goal

Turn the validated Financial Health Score into a clearer decision-support feature
without presenting it as a return forecast or changing its frozen formula.

## Step 1 — Make validation visible

- Add a compact, expandable **How this score was validated** panel.
- Show the final holdout sample, rank correlation, comparison win rate, and coverage.
- Explain in plain language what the findings support and what they do not.
- Load only small, bundled summary values so normal app performance is unchanged.

## Step 2 — Add company-specific score context

- Explain why the current company received its score.
- Show component completeness and the strongest and weakest contributors.
- Add industry context as a separate comparison, not as a replacement score.
- Clearly distinguish reported facts, calculated metrics, and research interpretation.

## Step 3 — Improve comparison and usability

- Let users compare two supported companies using the same four components.
- Keep comparison data cached and reuse the existing Phase 1 pipeline.
- Add accessible empty, loading, missing-data, and error states.
- Keep the interface concise for beginner investors.

## Step 4 — Quality and release review

- Test the experience with representative companies from each supported industry.
- Measure first-load and cached-load performance.
- Confirm no additional requests are made merely to display validation information.
- Run the complete automated suite and update user documentation.

## Guardrails

- Do not change the production score formula during Phase 3.
- Do not add buy, sell, price-target, or expected-return language.
- Do not load the full Phase 2 research dataset in the live dashboard.
- Keep experimental industry-relative models outside the production score.
- Treat missing data explicitly and never invent or silently substitute values.

## Completion criteria

Phase 3 is complete when validation evidence is visible, company-specific score
context is understandable, two-company comparison works reliably, app performance
does not materially regress, and all automated tests pass.

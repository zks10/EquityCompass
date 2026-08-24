# Phase 3.1 Engineering Specification

Phase 3.1 publishes up to five evidence-backed weekly opportunities from a
point-in-time S&P 500 universe. Providers retrieve facts, normalizers create
stable records, Event Intelligence relates evidence over time, detectors assess
opportunity patterns, deterministic gates and ranking select candidates, and the
Streamlit UI reads an immutable snapshot.

## Frozen detectors

1. `market_overreaction`
2. `negative_news_resolution`
3. `valuation_reset`
4. `fundamental_inflection`
5. `temporary_headwind`
6. `emerging_catalyst`

Positive detector scores run from 0 (investigated, no opportunity signal) to 100.
A non-applicable detector is N/A, represented by a null score. A failed analysis
is also null but has an explicit failure status; it is never converted to zero.

## Eligibility

Normal eligibility requires current financial, market, news/event, and detector
evidence; a valid production Financial Snapshot Score (the Phase 3.1 Equity
Score) of at least 65; and no unresolved critical risk. Critical risks include
severe liquidity distress, going-concern warnings, credible major accounting or
fraud issues, bankruptcy/restructuring risk, catastrophic regulatory threats,
and insufficient evidence around a known major risk. Companies below 65 remain
observe-only.

## Ranking

The highest applicable detector is the primary thesis. Other detectors scoring
at least 70 may support it. Independent families are market dislocation
(overreaction, valuation reset), event evolution (resolution, temporary
headwind), business improvement (fundamental inflection), and forward catalyst
(emerging catalyst). One, two, or three-plus independent supporting families add
3, 6, or 8 points.

The frozen formula is:

```text
R = 0.60P + 0.15EC + 0.10F + 0.15(100 - ORS) + SC + TM
```

`EC < 40` rejects a candidate. Risk runs from 0 (very low) to 100 (extreme).
Momentum is strengthening `+3`, stable `0`, or weakening `-5`; a first weekly
appearance is stable. The initial final-rank threshold is 70.

Publication selects up to five companies, normally no more than two per sector,
and avoids concentration in one systemic event. It never fills places with
sub-threshold candidates.

## Execution and storage

Each weekly run freezes its universe, market-data date, information cutoff,
methodology version, and configuration version. Mutable research state will use
SQLite; published JSON snapshots are self-contained, checksum-verified,
atomically published, and never overwritten. SEC CIK is the canonical company
identifier. Tickers and classifications are time-varying attributes.

The UI never launches the universe scan. Failed publication leaves the previous
valid snapshot live. Implement the vertical slice on a deterministic pilot
universe, beginning with Market Overreaction; production V1 remains the S&P 500.

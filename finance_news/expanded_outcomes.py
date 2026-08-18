"""Join expanded development/validation scores to point-in-time market outcomes."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from finance_news.validation_outcomes import ValidationOutcomeError, calculate_forward_outcomes


OUTCOME_CUTOFF_END = "2026-08-16"


def _load_snapshot(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _price_history(ticker: str, start: str, cache: Path) -> pd.Series:
    safe_ticker = ticker.replace("/", "-").replace(".", "-")
    destination = cache / f"{safe_ticker}.json"
    if destination.is_file():
        values = json.loads(destination.read_text(encoding="utf-8"))
        return pd.Series(
            {pd.Timestamp(date): close for date, close in values.items()}, dtype=float
        ).sort_index()
    history = yf.Ticker(ticker).history(
        start=start, end=OUTCOME_CUTOFF_END, interval="1d", auto_adjust=True
    )
    if history.empty or "Close" not in history:
        raise ValidationOutcomeError("No adjusted daily closes were returned.")
    closes = history["Close"].dropna().astype(float)
    normalized = {
        pd.Timestamp(index).tz_localize(None).strftime("%Y-%m-%d"): value
        for index, value in closes.items()
    }
    temporary = destination.with_suffix(".json.part")
    temporary.write_text(json.dumps(normalized), encoding="utf-8")
    temporary.replace(destination)
    return pd.Series(
        {pd.Timestamp(date): close for date, close in normalized.items()}, dtype=float
    ).sort_index()


def build_expanded_market_outcomes(
    score_manifest_path: Path = Path("data/validation/expanded/scores/manifest.json"),
    output_path: Path = Path("data/validation/expanded/market_outcomes.json"),
    price_cache: Path = Path("data/validation/expanded/prices"),
    max_workers: int = 5,
    expected_holdout_opened: bool = False,
) -> Path:
    """Calculate outcomes for qualified scores without loading holdout records."""
    score_manifest = json.loads(Path(score_manifest_path).read_text(encoding="utf-8"))
    if score_manifest.get("holdout_opened") is not expected_holdout_opened:
        raise ValueError("Score manifest holdout state does not match this run.")
    qualified = []
    for attempt in score_manifest["attempts"]:
        if attempt["status"] != "completed":
            continue
        snapshot = _load_snapshot(attempt["snapshot_path"])
        if snapshot["score"]["eligible_for_main_comparison"]:
            qualified.append((attempt, snapshot))
    cache = Path(price_cache)
    cache.mkdir(parents=True, exist_ok=True)
    earliest = min(snapshot["filing"]["filed"] for _, snapshot in qualified)
    tickers = sorted({attempt["ticker"] for attempt, _ in qualified})
    histories: dict[str, pd.Series] = {}
    failures: dict[str, str] = {}

    def fetch(ticker: str) -> tuple[str, pd.Series | None, str | None]:
        try:
            return ticker, _price_history(ticker, earliest, cache), None
        except Exception as exc:
            return ticker, None, str(exc)

    spy = _price_history("SPY", earliest, cache)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for ticker, history, error in executor.map(fetch, tickers):
            if history is not None:
                histories[ticker] = history
            else:
                failures[ticker] = error or "Unknown price-history error."

    outcomes = []
    for attempt, snapshot in qualified:
        base = {
            "cik": attempt["cik"],
            "ticker": attempt["ticker"],
            "accession_number": attempt["accession_number"],
            "partition": attempt["partition"],
            "industry_division": attempt["industry_division"],
            "score": snapshot["score"]["value"],
            "available_components": snapshot["score"]["available_components"],
        }
        if attempt["ticker"] in failures:
            outcomes.append({**base, "status": "incomplete", "reason": failures[attempt["ticker"]]})
            continue
        try:
            result = calculate_forward_outcomes(
                histories[attempt["ticker"]], spy, snapshot["filing"]["filed"]
            )
            outcomes.append({**base, "status": "completed", **result})
        except ValidationOutcomeError as exc:
            outcomes.append({**base, "status": "incomplete", "reason": str(exc)})

    payload = {
        "schema_version": 1,
        "status": (
            "holdout_outcomes_complete"
            if expected_holdout_opened
            else "development_validation_outcomes_complete"
        ),
        "holdout_opened": expected_holdout_opened,
        "outcome_data_cutoff": "2026-08-15",
        "price_basis": "Adjusted daily close",
        "benchmark": "SPY",
        "attempted": len(outcomes),
        "completed": sum(row["status"] == "completed" for row in outcomes),
        "incomplete": sum(row["status"] == "incomplete" for row in outcomes),
        "outcomes": outcomes,
    }
    destination = Path(output_path)
    temporary = destination.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


__all__ = ["build_expanded_market_outcomes"]

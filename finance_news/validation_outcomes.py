"""Offline market outcomes for Phase 2 score validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf


HORIZONS = (6, 12, 24)


class ValidationOutcomeError(Exception):
    """Raised when historical validation outcomes cannot be calculated."""


def calculate_forward_outcomes(
    stock_prices: pd.Series,
    spy_prices: pd.Series,
    filing_date: str,
) -> dict[str, Any]:
    """Calculate point-in-time returns on common market dates after a filing."""
    stock = stock_prices.dropna().astype(float)
    spy = spy_prices.dropna().astype(float)
    stock.index = pd.to_datetime(stock.index).tz_localize(None).normalize()
    spy.index = pd.to_datetime(spy.index).tz_localize(None).normalize()
    common = stock.to_frame("stock").join(spy.to_frame("spy"), how="inner")
    common = common[~common.index.duplicated(keep="last")].sort_index()
    filing = pd.Timestamp(filing_date)
    available = common[common.index > filing]
    if available.empty:
        raise ValidationOutcomeError("No common company and SPY close after filing.")

    start_date = available.index[0]
    start_stock = float(available.iloc[0]["stock"])
    start_spy = float(available.iloc[0]["spy"])
    horizons: dict[str, Any] = {}
    for months in HORIZONS:
        target = start_date + pd.DateOffset(months=months)
        eligible = available[available.index >= target]
        if eligible.empty:
            horizons[f"{months}_months"] = {
                "status": "pending",
                "target_date": target.strftime("%Y-%m-%d"),
            }
            continue
        end_date = eligible.index[0]
        end_stock = float(eligible.iloc[0]["stock"])
        end_spy = float(eligible.iloc[0]["spy"])
        stock_return = (end_stock / start_stock - 1) * 100
        spy_return = (end_spy / start_spy - 1) * 100
        horizons[f"{months}_months"] = {
            "status": "completed",
            "target_date": target.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "company_return_percent": round(stock_return, 4),
            "spy_return_percent": round(spy_return, 4),
            "excess_return_percent": round(stock_return - spy_return, 4),
        }

    twelve_month_target = start_date + pd.DateOffset(months=12)
    twelve_month_prices = available[available.index <= twelve_month_target]["stock"]
    if common.index[-1] < twelve_month_target:
        max_drawdown = None
    else:
        drawdowns = twelve_month_prices / twelve_month_prices.cummax() - 1
        max_drawdown = round(float(drawdowns.min()) * 100, 4)
    return {
        "filing_date": filing.strftime("%Y-%m-%d"),
        "measurement_start_date": start_date.strftime("%Y-%m-%d"),
        "measurement_start_company_price": start_stock,
        "measurement_start_spy_price": start_spy,
        "horizons": horizons,
        "max_drawdown_12_months_percent": max_drawdown,
    }


def build_pilot_market_outcomes(
    snapshot_root: Path = Path("data/validation/sec"),
    output_root: Path = Path("data/validation/market"),
) -> Path:
    """Download adjusted daily closes and save outcomes for every pilot snapshot."""
    pilot_path = Path(snapshot_root) / "pilot_manifest.json"
    try:
        pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationOutcomeError(f"Could not read pilot manifest: {exc}") from exc

    snapshot_paths: list[Path] = []
    for company in pilot.get("companies", []):
        manifest_path = company.get("manifest_path")
        if not manifest_path:
            continue
        company_manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        snapshot_paths.extend(
            Path(filing["snapshot_path"])
            for filing in company_manifest.get("filings", [])
            if filing.get("status") == "completed"
        )
    snapshots = [json.loads(path.read_text(encoding="utf-8")) for path in snapshot_paths]
    if not snapshots:
        raise ValidationOutcomeError("Pilot manifest contains no completed snapshots.")

    earliest = min(snapshot["filing"]["filed"] for snapshot in snapshots)
    latest_needed = (pd.Timestamp.now().normalize() + pd.Timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )
    try:
        spy_history = yf.Ticker("SPY").history(
            start=earliest, end=latest_needed, interval="1d", auto_adjust=True
        )
    except Exception as exc:
        raise ValidationOutcomeError(f"Could not retrieve SPY prices: {exc}") from exc
    if spy_history.empty or "Close" not in spy_history:
        raise ValidationOutcomeError("No adjusted SPY closes were returned.")

    results = []
    for ticker in sorted({snapshot["ticker"] for snapshot in snapshots}):
        ticker_snapshots = [s for s in snapshots if s["ticker"] == ticker]
        try:
            history = yf.Ticker(ticker).history(
                start=earliest, end=latest_needed, interval="1d", auto_adjust=True
            )
            if history.empty or "Close" not in history:
                raise ValidationOutcomeError("No adjusted company closes were returned.")
            outcomes = [
                {
                    "accession_number": snapshot["filing"]["accession_number"],
                    "fiscal_year": snapshot["fiscal_year"],
                    "score": snapshot["score"]["value"],
                    **calculate_forward_outcomes(
                        history["Close"],
                        spy_history["Close"],
                        snapshot["filing"]["filed"],
                    ),
                }
                for snapshot in ticker_snapshots
            ]
            result = {"ticker": ticker, "status": "completed", "outcomes": outcomes}
        except (Exception, ValidationOutcomeError) as exc:
            result = {"ticker": ticker, "status": "incomplete", "reason": str(exc)}
        results.append(result)

    manifest = {
        "schema_version": 1,
        "price_basis": "Adjusted daily close",
        "benchmark": "SPY",
        "completed_companies": sum(r["status"] == "completed" for r in results),
        "incomplete_companies": sum(r["status"] == "incomplete" for r in results),
        "companies": results,
    }
    destination = Path(output_root) / "pilot_outcomes.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


__all__ = [
    "ValidationOutcomeError",
    "build_pilot_market_outcomes",
    "calculate_forward_outcomes",
]

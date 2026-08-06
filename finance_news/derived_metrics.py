"""Calculate deterministic metrics from stored annual financial history."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


REQUIRED_METRICS = (
    "revenue",
    "net_income",
    "assets",
    "liabilities",
    "operating_cash_flow",
)
FOUR_DECIMAL_PLACES = Decimal("0.0001")


class DerivedMetricsError(Exception):
    """Raised when stored history cannot produce derived metrics."""


def _as_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise DerivedMetricsError(f"{label} must be numeric.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DerivedMetricsError(f"{label} must be numeric.") from exc
    if not number.is_finite():
        raise DerivedMetricsError(f"{label} must be finite.")
    return number


def _percentage(numerator: Decimal, denominator: Decimal) -> float | None:
    if denominator == 0:
        return None
    percentage = (numerator / denominator * Decimal(100)).quantize(
        FOUR_DECIMAL_PLACES, rounding=ROUND_HALF_UP
    )
    return float(percentage)


def _index_metric(records: Any, metric: str) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        raise DerivedMetricsError(f"History for {metric} must be a list.")

    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise DerivedMetricsError(f"History for {metric} contains an invalid record.")
        period_end = record.get("period_end")
        if not isinstance(period_end, str) or not period_end:
            raise DerivedMetricsError(f"History for {metric} is missing period_end.")
        if period_end in indexed:
            raise DerivedMetricsError(
                f"History for {metric} contains duplicate period {period_end}."
            )
        _as_decimal(record.get("value"), f"{metric} value for {period_end}")
        indexed[period_end] = record
    return indexed


def calculate_derived_metrics(history_payload: dict[str, Any]) -> dict[str, Any]:
    """Calculate annual growth and ratios from a normalized history payload."""
    if not isinstance(history_payload, dict):
        raise DerivedMetricsError("Financial history must be a JSON object.")
    metrics = history_payload.get("metrics")
    if not isinstance(metrics, dict):
        raise DerivedMetricsError("Financial history is missing metrics.")

    indexed = {}
    missing = []
    for metric in REQUIRED_METRICS:
        if metric not in metrics:
            missing.append(metric)
        else:
            indexed[metric] = _index_metric(metrics[metric], metric)
    if missing:
        raise DerivedMetricsError(
            "Financial history is missing metric(s): " + ", ".join(missing) + "."
        )

    common_periods = set.intersection(
        *(set(records) for records in indexed.values())
    )
    if not common_periods:
        raise DerivedMetricsError("The financial metrics have no aligned annual periods.")

    periods: list[dict[str, Any]] = []
    previous_revenue: Decimal | None = None
    for period_end in sorted(common_periods):
        revenue_record = indexed["revenue"][period_end]
        revenue = _as_decimal(revenue_record["value"], "revenue")
        net_income = _as_decimal(
            indexed["net_income"][period_end]["value"], "net income"
        )
        assets = _as_decimal(indexed["assets"][period_end]["value"], "assets")
        liabilities = _as_decimal(
            indexed["liabilities"][period_end]["value"], "liabilities"
        )
        operating_cash_flow = _as_decimal(
            indexed["operating_cash_flow"][period_end]["value"],
            "operating cash flow",
        )

        growth = (
            None
            if previous_revenue is None
            else _percentage(revenue - previous_revenue, previous_revenue)
        )
        periods.append(
            {
                "fiscal_year": revenue_record.get("fiscal_year"),
                "period_end": period_end,
                "source_values": {
                    "revenue": revenue_record["value"],
                    "net_income": indexed["net_income"][period_end]["value"],
                    "assets": indexed["assets"][period_end]["value"],
                    "liabilities": indexed["liabilities"][period_end]["value"],
                    "operating_cash_flow": indexed["operating_cash_flow"][period_end][
                        "value"
                    ],
                },
                "revenue_growth_percent": growth,
                "net_profit_margin_percent": _percentage(net_income, revenue),
                "liabilities_to_assets_percent": _percentage(liabilities, assets),
                "operating_cash_flow_margin_percent": _percentage(
                    operating_cash_flow, revenue
                ),
            }
        )
        previous_revenue = revenue

    periods.reverse()
    return {
        "ticker": history_payload.get("ticker"),
        "cik": history_payload.get("cik"),
        "entity_name": history_payload.get("entity_name"),
        "formulas": {
            "revenue_growth_percent": "(revenue - prior_revenue) / prior_revenue * 100",
            "net_profit_margin_percent": "net_income / revenue * 100",
            "liabilities_to_assets_percent": "liabilities / assets * 100",
            "operating_cash_flow_margin_percent": "operating_cash_flow / revenue * 100",
        },
        "periods": periods,
    }


def calculate_metrics_file(
    input_path: Path, output_path: Path | None = None
) -> Path:
    """Read stored history, calculate metrics, and save normalized JSON."""
    source = Path(input_path)
    if not source.is_file():
        raise DerivedMetricsError(f"Financial history not found: {source}")
    destination = Path(output_path) if output_path else source.parent / "derived_metrics.json"

    try:
        history_payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DerivedMetricsError("Financial history contains invalid JSON.") from exc
    except OSError as exc:
        raise DerivedMetricsError(f"Could not read financial history: {exc}") from exc

    derived = calculate_derived_metrics(history_payload)
    derived["source_history"] = str(source)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_suffix(f"{destination.suffix}.part")
        temporary_path.write_text(
            json.dumps(derived, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(destination)
    except OSError as exc:
        raise DerivedMetricsError(f"Could not save derived metrics: {exc}") from exc

    return destination

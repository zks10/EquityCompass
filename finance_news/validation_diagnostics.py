"""Component and industry diagnostics for the Phase 2 pilot."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from finance_news.validation_report import score_group, summarize_group


INDUSTRIES = {
    "Technology": ("AAPL", "MSFT", "NVDA", "INTC"),
    "Banking": ("JPM", "BAC", "GS", "C"),
    "Energy": ("XOM", "CVX", "COP", "SLB"),
    "Healthcare": ("JNJ", "PFE", "UNH", "MDT"),
    "Consumer": ("WMT", "TGT", "KO", "NKE"),
}
TICKER_INDUSTRY = {
    ticker: industry for industry, tickers in INDUSTRIES.items() for ticker in tickers
}


class ValidationDiagnosticsError(Exception):
    """Raised when validation diagnostics cannot be produced."""


def _spearman(values: list[float], outcomes: list[float]) -> float | None:
    if len(values) < 2 or len(set(values)) < 2 or len(set(outcomes)) < 2:
        return None
    correlation = pd.Series(values).rank(method="average").corr(
        pd.Series(outcomes).rank(method="average")
    )
    return None if pd.isna(correlation) else round(float(correlation), 3)


def build_diagnostic_report(
    outcomes_path: Path = Path("data/validation/market/pilot_outcomes.json"),
    snapshot_root: Path = Path("data/validation/sec"),
    output_root: Path = Path("data/validation/report"),
) -> tuple[Path, Path]:
    """Save component and industry breakdowns for completed 12-month outcomes."""
    try:
        outcomes = json.loads(Path(outcomes_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationDiagnosticsError(f"Could not read pilot outcomes: {exc}") from exc

    snapshots = {}
    for path in Path(snapshot_root).glob("*/*.json"):
        if path.name == "manifest.json":
            continue
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            snapshots[snapshot["filing"]["accession_number"]] = snapshot
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue

    records: list[dict[str, Any]] = []
    for company in outcomes.get("companies", []):
        ticker = company["ticker"]
        for outcome in company.get("outcomes", []):
            twelve = outcome["horizons"]["12_months"]
            if twelve["status"] != "completed":
                continue
            accession = outcome["accession_number"]
            snapshot = snapshots.get(accession)
            if snapshot is None:
                raise ValidationDiagnosticsError(
                    f"Score snapshot is missing for accession {accession}."
                )
            records.append(
                {
                    "ticker": ticker,
                    "industry": TICKER_INDUSTRY[ticker],
                    "fiscal_year": outcome["fiscal_year"],
                    "company_return_percent": twelve["company_return_percent"],
                    "excess_return_percent": twelve["excess_return_percent"],
                    "max_drawdown_12_months_percent": outcome[
                        "max_drawdown_12_months_percent"
                    ],
                    "components": {
                        component["name"]: component["score"]
                        for component in snapshot["score"]["components"]
                    },
                }
            )

    industry_results = {
        industry: summarize_group(
            [record for record in records if record["industry"] == industry]
        )
        for industry in INDUSTRIES
    }
    component_names = sorted(
        {name for record in records for name in record["components"]}
    )
    component_results = {}
    for component_name in component_names:
        available = [
            record
            for record in records
            if record["components"].get(component_name) is not None
        ]
        component_results[component_name] = {
            "observations": len(available),
            "spearman_with_company_return": _spearman(
                [record["components"][component_name] for record in available],
                [record["company_return_percent"] for record in available],
            ),
            "spearman_with_excess_return": _spearman(
                [record["components"][component_name] for record in available],
                [record["excess_return_percent"] for record in available],
            ),
            "score_groups": {
                group: summarize_group(
                    [
                        record
                        for record in available
                        if score_group(record["components"][component_name]) == group
                    ]
                )
                for group in ("Higher", "Middle", "Lower")
            },
        }

    report = {
        "schema_version": 1,
        "completed_12_month_observations": len(records),
        "industry_results": industry_results,
        "component_results": component_results,
        "limitations": [
            "Industry groups contain only four companies each.",
            "Component correlations are descriptive and do not establish causation.",
            "Repeated years from the same company are not independent observations.",
            "No score rules were changed after viewing these results.",
        ],
    }
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "pilot_diagnostics.json"
    markdown_path = output / "pilot_diagnostics.md"
    temporary_json = json_path.with_suffix(".json.part")
    temporary_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary_json.replace(json_path)

    lines = [
        "# Equity Compass Phase 2 Pilot Diagnostics",
        "",
        "## Industry breakdown",
        "",
        "| Industry | Records | Median return | Median vs. SPY | Beat SPY | Average max decline |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    display = lambda value: "—" if value is None else f"{value:.2f}%"
    for industry, result in industry_results.items():
        lines.append(
            f"| {industry} | {result['observations']} | "
            f"{display(result['median_company_return_percent'])} | "
            f"{display(result['median_excess_return_percent'])} | "
            f"{display(result['spy_beating_rate_percent'])} | "
            f"{display(result['average_max_drawdown_percent'])} |"
        )
    lines.extend(
        [
            "",
            "## Component diagnostics",
            "",
            "Spearman values range from -1 to +1. Positive values mean higher component scores tended to accompany higher returns in this pilot.",
            "",
            "| Component | Records | Correlation with return | Correlation with return vs. SPY |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for name, result in component_results.items():
        raw = result["spearman_with_company_return"]
        excess = result["spearman_with_excess_return"]
        lines.append(
            f"| {name} | {result['observations']} | "
            f"{'—' if raw is None else f'{raw:.3f}'} | "
            f"{'—' if excess is None else f'{excess:.3f}'} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            *[f"- {item}" for item in report["limitations"]],
            "",
            "These diagnostics are exploratory and are not an investment recommendation.",
        ]
    )
    temporary_markdown = markdown_path.with_suffix(".md.part")
    temporary_markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary_markdown.replace(markdown_path)
    return json_path, markdown_path


__all__ = ["ValidationDiagnosticsError", "build_diagnostic_report"]

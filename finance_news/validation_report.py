"""Create the plain-language Phase 2 pilot comparison report."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median
from typing import Any


class ValidationReportError(Exception):
    """Raised when the pilot comparison report cannot be created."""


def score_group(score: int | float) -> str:
    if score >= 75:
        return "Higher"
    if score >= 50:
        return "Middle"
    return "Lower"


def _round(value: float) -> float:
    return round(value, 2)


def summarize_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize one score group using completed 12-month outcomes."""
    company_returns = [record["company_return_percent"] for record in records]
    excess_returns = [record["excess_return_percent"] for record in records]
    drawdowns = [
        record["max_drawdown_12_months_percent"]
        for record in records
        if record["max_drawdown_12_months_percent"] is not None
    ]
    if not records:
        return {
            "observations": 0,
            "average_company_return_percent": None,
            "median_company_return_percent": None,
            "average_excess_return_percent": None,
            "median_excess_return_percent": None,
            "spy_beating_rate_percent": None,
            "average_max_drawdown_percent": None,
            "median_max_drawdown_percent": None,
        }
    return {
        "observations": len(records),
        "average_company_return_percent": _round(mean(company_returns)),
        "median_company_return_percent": _round(median(company_returns)),
        "average_excess_return_percent": _round(mean(excess_returns)),
        "median_excess_return_percent": _round(median(excess_returns)),
        "spy_beating_rate_percent": _round(
            sum(value > 0 for value in excess_returns) / len(excess_returns) * 100
        ),
        "average_max_drawdown_percent": (
            _round(mean(drawdowns)) if drawdowns else None
        ),
        "median_max_drawdown_percent": (
            _round(median(drawdowns)) if drawdowns else None
        ),
    }


def build_validation_report(
    outcomes_path: Path = Path("data/validation/market/pilot_outcomes.json"),
    output_root: Path = Path("data/validation/report"),
) -> tuple[Path, Path]:
    """Save machine-readable and Markdown summaries of the baseline pilot."""
    try:
        outcomes = json.loads(Path(outcomes_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationReportError(f"Could not read pilot outcomes: {exc}") from exc

    completed: list[dict[str, Any]] = []
    pending = 0
    for company in outcomes.get("companies", []):
        for outcome in company.get("outcomes", []):
            twelve_months = outcome["horizons"]["12_months"]
            if twelve_months["status"] != "completed":
                pending += 1
                continue
            completed.append(
                {
                    "ticker": company["ticker"],
                    "fiscal_year": outcome["fiscal_year"],
                    "score": outcome["score"],
                    "group": score_group(outcome["score"]),
                    "company_return_percent": twelve_months[
                        "company_return_percent"
                    ],
                    "spy_return_percent": twelve_months["spy_return_percent"],
                    "excess_return_percent": twelve_months[
                        "excess_return_percent"
                    ],
                    "max_drawdown_12_months_percent": outcome[
                        "max_drawdown_12_months_percent"
                    ],
                }
            )

    groups = {
        name: summarize_group([record for record in completed if record["group"] == name])
        for name in ("Higher", "Middle", "Lower")
    }
    report = {
        "schema_version": 1,
        "primary_horizon": "12 months",
        "score_groups": {
            "Higher": "75-100",
            "Middle": "50-74",
            "Lower": "0-49",
        },
        "completed_observations": len(completed),
        "pending_observations": pending,
        "groups": groups,
        "records": completed,
        "limitations": [
            "This is a small 20-company pilot, not proof of predictive ability.",
            "Repeated years from the same company are not independent observations.",
            "Newer filings remain pending until the full 12-month horizon elapses.",
            "The baseline score formula was not changed for this comparison.",
        ],
    }
    output = Path(output_root)
    json_path = output / "pilot_report.json"
    markdown_path = output / "pilot_report.md"
    output.mkdir(parents=True, exist_ok=True)
    temporary_json = json_path.with_suffix(".json.part")
    temporary_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary_json.replace(json_path)

    lines = [
        "# Equity Compass Phase 2 Pilot Report",
        "",
        "## Baseline 12-month comparison",
        "",
        f"Completed observations: **{len(completed)}**  ",
        f"Pending observations: **{pending}**",
        "",
        "| Score group | Records | Average return | Median return | Average vs. SPY | Median vs. SPY | Beat SPY | Average max decline |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("Higher", "Middle", "Lower"):
        group = groups[name]
        value = lambda key: (
            "—" if group[key] is None else f"{group[key]:.2f}%"
        )
        lines.append(
            f"| {name} | {group['observations']} | "
            f"{value('average_company_return_percent')} | "
            f"{value('median_company_return_percent')} | "
            f"{value('average_excess_return_percent')} | "
            f"{value('median_excess_return_percent')} | "
            f"{value('spy_beating_rate_percent')} | "
            f"{value('average_max_drawdown_percent')} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            *[f"- {limitation}" for limitation in report["limitations"]],
            "",
            "This report describes the observed pilot results. It is not an investment recommendation.",
        ]
    )
    temporary_markdown = markdown_path.with_suffix(".md.part")
    temporary_markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary_markdown.replace(markdown_path)
    return json_path, markdown_path


__all__ = [
    "ValidationReportError",
    "build_validation_report",
    "score_group",
    "summarize_group",
]

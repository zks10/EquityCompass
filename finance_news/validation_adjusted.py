"""Industry-adjusted comparisons for the Phase 2 pilot."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median
from typing import Any

from finance_news.validation_diagnostics import TICKER_INDUSTRY, _spearman
from finance_news.validation_report import score_group


class IndustryAdjustedError(Exception):
    """Raised when the industry-adjusted comparison cannot be produced."""


def analyze_industry_cohorts(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare scores and excess returns within the same industry and fiscal year."""
    cohorts: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for record in records:
        industry = record.get("industry") or TICKER_INDUSTRY[record["ticker"]]
        item = {**record, "industry": industry}
        cohorts.setdefault((industry, int(record["fiscal_year"])), []).append(item)

    adjusted_records = []
    cohort_results = []
    correlations = []
    for (industry, fiscal_year), cohort in sorted(cohorts.items()):
        if len(cohort) < 2:
            continue
        cohort_average = mean(record["excess_return_percent"] for record in cohort)
        for record in cohort:
            adjusted_records.append(
                {
                    **record,
                    "adjusted_excess_return_percent": round(
                        record["excess_return_percent"] - cohort_average, 4
                    ),
                }
            )
        correlation = _spearman(
            [record["score"] for record in cohort],
            [record["excess_return_percent"] for record in cohort],
        )
        if correlation is not None:
            correlations.append(correlation)
        highest_score = max(record["score"] for record in cohort)
        lowest_score = min(record["score"] for record in cohort)
        highest_return = mean(
            record["excess_return_percent"]
            for record in cohort
            if record["score"] == highest_score
        )
        lowest_return = mean(
            record["excess_return_percent"]
            for record in cohort
            if record["score"] == lowest_score
        )
        difference = highest_return - lowest_return
        cohort_results.append(
            {
                "industry": industry,
                "fiscal_year": fiscal_year,
                "observations": len(cohort),
                "highest_score": highest_score,
                "lowest_score": lowest_score,
                "highest_minus_lowest_excess_return_percent": round(difference, 4),
                "higher_score_outperformed": difference > 0,
                "spearman_score_vs_excess_return": correlation,
            }
        )

    group_results = {}
    for group in ("Higher", "Middle", "Lower"):
        values = [
            record["adjusted_excess_return_percent"]
            for record in adjusted_records
            if score_group(record["score"]) == group
        ]
        group_results[group] = {
            "observations": len(values),
            "average_industry_adjusted_excess_return_percent": (
                round(mean(values), 2) if values else None
            ),
            "median_industry_adjusted_excess_return_percent": (
                round(median(values), 2) if values else None
            ),
            "above_industry_cohort_average_rate_percent": (
                round(sum(value > 0 for value in values) / len(values) * 100, 2)
                if values
                else None
            ),
        }
    differences = [
        cohort["highest_minus_lowest_excess_return_percent"]
        for cohort in cohort_results
    ]
    return {
        "eligible_cohorts": len(cohort_results),
        "adjusted_observations": len(adjusted_records),
        "group_results": group_results,
        "highest_vs_lowest": {
            "average_excess_return_difference_percent": (
                round(mean(differences), 2) if differences else None
            ),
            "median_excess_return_difference_percent": (
                round(median(differences), 2) if differences else None
            ),
            "higher_score_win_rate_percent": (
                round(
                    sum(cohort["higher_score_outperformed"] for cohort in cohort_results)
                    / len(cohort_results)
                    * 100,
                    2,
                )
                if cohort_results
                else None
            ),
        },
        "within_cohort_spearman": {
            "average": round(mean(correlations), 3) if correlations else None,
            "median": round(median(correlations), 3) if correlations else None,
        },
        "cohorts": cohort_results,
        "records": adjusted_records,
    }


def build_industry_adjusted_report(
    baseline_path: Path = Path("data/validation/report/pilot_report.json"),
    output_root: Path = Path("data/validation/report"),
) -> tuple[Path, Path]:
    """Save JSON and Markdown industry-adjusted pilot results."""
    try:
        baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IndustryAdjustedError(f"Could not read baseline report: {exc}") from exc
    analysis = analyze_industry_cohorts(baseline.get("records", []))
    report = {
        "schema_version": 1,
        **analysis,
        "method": (
            "Each observation's SPY-relative return is compared with the average "
            "for companies in the same pilot industry and fiscal year."
        ),
        "limitations": [
            "Each industry-year cohort contains at most four companies.",
            "Industry adjustment reduces but does not remove company-specific differences.",
            "Repeated years from the same company are not independent observations.",
            "This analysis was performed without changing the baseline score.",
        ],
    }
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "pilot_industry_adjusted.json"
    markdown_path = output / "pilot_industry_adjusted.md"
    temporary_json = json_path.with_suffix(".json.part")
    temporary_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary_json.replace(json_path)

    value = lambda number: "—" if number is None else f"{number:.2f}%"
    lines = [
        "# Equity Compass Industry-Adjusted Pilot",
        "",
        report["method"],
        "",
        f"Eligible industry-year cohorts: **{report['eligible_cohorts']}**  ",
        f"Adjusted observations: **{report['adjusted_observations']}**",
        "",
        "## Score groups after industry adjustment",
        "",
        "| Score group | Records | Average adjusted return | Median adjusted return | Above cohort average |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for group, result in report["group_results"].items():
        lines.append(
            f"| {group} | {result['observations']} | "
            f"{value(result['average_industry_adjusted_excess_return_percent'])} | "
            f"{value(result['median_industry_adjusted_excess_return_percent'])} | "
            f"{value(result['above_industry_cohort_average_rate_percent'])} |"
        )
    comparison = report["highest_vs_lowest"]
    correlation = report["within_cohort_spearman"]
    lines.extend(
        [
            "",
            "## Direct within-cohort comparison",
            "",
            f"- Average highest-score minus lowest-score result: **{value(comparison['average_excess_return_difference_percent'])}**",
            f"- Median highest-score minus lowest-score result: **{value(comparison['median_excess_return_difference_percent'])}**",
            f"- Higher-score win rate: **{value(comparison['higher_score_win_rate_percent'])}**",
            f"- Average within-cohort rank correlation: **{correlation['average'] if correlation['average'] is not None else '—'}**",
            "",
            "## Limitations",
            "",
            *[f"- {item}" for item in report["limitations"]],
            "",
            "This exploratory comparison is not an investment recommendation.",
        ]
    )
    temporary_markdown = markdown_path.with_suffix(".md.part")
    temporary_markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary_markdown.replace(markdown_path)
    return json_path, markdown_path


__all__ = [
    "IndustryAdjustedError",
    "analyze_industry_cohorts",
    "build_industry_adjusted_report",
]

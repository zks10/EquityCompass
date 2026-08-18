"""Coverage and robustness checks before the expanded holdout is opened."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from finance_news.validation_diagnostics import _spearman
from finance_news.validation_report import score_group


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = sum(row["status"] == "completed" for row in rows)
    return {
        "attempted": len(rows),
        "completed": completed,
        "missing_initial_price": len(rows) - completed,
        "coverage_percent": round(completed / len(rows) * 100, 2) if rows else None,
    }


def _leave_one_out(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    correlations = []
    for value in sorted({record[field] for record in records}):
        subset = [record for record in records if record[field] != value]
        correlation = _spearman(
            [record["score"] for record in subset],
            [record["excess_return_percent"] for record in subset],
        )
        if correlation is not None:
            correlations.append(correlation)
    return {
        "iterations": len(correlations),
        "positive_iterations": sum(value > 0 for value in correlations),
        "positive_rate_percent": (
            round(sum(value > 0 for value in correlations) / len(correlations) * 100, 2)
            if correlations else None
        ),
        "minimum_spearman": min(correlations) if correlations else None,
        "maximum_spearman": max(correlations) if correlations else None,
    }


def build_coverage_sensitivity_report(
    outcomes_path: Path = Path("data/validation/expanded/market_outcomes.json"),
    output_root: Path = Path("data/validation/expanded/report"),
) -> tuple[Path, Path]:
    payload = json.loads(Path(outcomes_path).read_text(encoding="utf-8"))
    if payload.get("holdout_opened") is not False:
        raise ValueError("Holdout must remain untouched during sensitivity checks.")
    attempts = payload["outcomes"]
    completed = []
    for row in attempts:
        if row["status"] != "completed":
            continue
        twelve = row["horizons"]["12_months"]
        if twelve["status"] == "completed":
            completed.append({**row, **twelve})

    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "pre_holdout_coverage_review_complete",
        "holdout_opened": False,
        "missing_price_reason": "missing_initial_price_from_registered_source",
        "overall_coverage": _coverage(attempts),
        "coverage_by_partition": {
            name: _coverage([row for row in attempts if row["partition"] == name])
            for name in ("development", "validation")
        },
        "coverage_by_score_group": {
            name: _coverage([row for row in attempts if score_group(row["score"]) == name])
            for name in ("Higher", "Middle", "Lower")
        },
        "coverage_by_industry": {
            name: _coverage([row for row in attempts if row["industry_division"] == name])
            for name in sorted({row["industry_division"] for row in attempts})
        },
        "sensitivity_by_partition": {},
    }
    for partition in ("development", "validation"):
        subset = [row for row in completed if row["partition"] == partition]
        report["sensitivity_by_partition"][partition] = {
            "observations": len(subset),
            "leave_one_company_out": _leave_one_out(subset, "cik"),
            "leave_one_industry_out": _leave_one_out(subset, "industry_division"),
        }
    validation = report["sensitivity_by_partition"]["validation"]
    robust = all(
        validation[key]["positive_rate_percent"] == 100.0
        for key in ("leave_one_company_out", "leave_one_industry_out")
    )
    report["holdout_ready"] = False
    report["decision"] = (
        "Do not open holdout yet. Recover or formally accept the registered-source "
        "price exclusions and freeze that decision first."
    )
    report["validation_direction_robust"] = robust

    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "coverage_sensitivity.json"
    markdown_path = output / "coverage_sensitivity.md"
    temporary = json_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(json_path)
    coverage = report["overall_coverage"]
    lines = [
        "# Pre-Holdout Coverage and Sensitivity Review",
        "",
        f"Verified price outcomes: **{coverage['completed']} of {coverage['attempted']} "
        f"({coverage['coverage_percent']:.2f}%)**",
        "",
        "| Partition | Attempted | Completed | Coverage |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, result in report["coverage_by_partition"].items():
        lines.append(
            f"| {name.title()} | {result['attempted']} | {result['completed']} | "
            f"{result['coverage_percent']:.2f}% |"
        )
    lines.extend(["", f"Decision: **{report['decision']}**", ""])
    temporary_md = markdown_path.with_suffix(".md.part")
    temporary_md.write_text("\n".join(lines), encoding="utf-8")
    temporary_md.replace(markdown_path)
    return json_path, markdown_path


__all__ = ["build_coverage_sensitivity_report"]

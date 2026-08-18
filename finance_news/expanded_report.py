"""Report preregistered development and validation results without opening holdout."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from finance_news.validation_adjusted import analyze_industry_cohorts
from finance_news.validation_diagnostics import _spearman
from finance_news.validation_report import score_group, summarize_group


def build_expanded_report(
    outcomes_path: Path = Path("data/validation/expanded/market_outcomes.json"),
    output_root: Path = Path("data/validation/expanded/report"),
) -> tuple[Path, Path]:
    payload = json.loads(Path(outcomes_path).read_text(encoding="utf-8"))
    if payload.get("holdout_opened") is not False:
        raise ValueError("Outcome manifest does not certify an untouched holdout.")
    records = []
    pending_horizons = 0
    for outcome in payload["outcomes"]:
        if outcome["status"] != "completed":
            continue
        twelve = outcome["horizons"]["12_months"]
        if twelve["status"] != "completed":
            pending_horizons += 1
            continue
        records.append(
            {
                **outcome,
                **twelve,
                "industry": outcome["industry_division"],
                "fiscal_year": int(outcome["filing_date"][:4]),
            }
        )

    partitions: dict[str, Any] = {}
    for partition in ("development", "validation"):
        subset = [record for record in records if record["partition"] == partition]
        partitions[partition] = {
            "observations": len(subset),
            "spearman_score_vs_company_return": _spearman(
                [r["score"] for r in subset], [r["company_return_percent"] for r in subset]
            ),
            "spearman_score_vs_excess_return": _spearman(
                [r["score"] for r in subset], [r["excess_return_percent"] for r in subset]
            ),
            "score_groups": {
                group: summarize_group(
                    [r for r in subset if score_group(r["score"]) == group]
                )
                for group in ("Higher", "Middle", "Lower")
            },
            "industry_adjusted": analyze_industry_cohorts(subset),
        }
    report = {
        "schema_version": 1,
        "status": "development_validation_report_complete",
        "holdout_opened": False,
        "qualified_score_attempts": payload["attempted"],
        "market_outcomes_completed": payload["completed"],
        "market_outcomes_incomplete": payload["incomplete"],
        "completed_12_month_observations": len(records),
        "pending_12_month_observations": pending_horizons,
        "partitions": partitions,
        "interpretation": (
            "The frozen baseline has positive rank correlation in development and "
            "validation, but incomplete historical price coverage requires resolution "
            "before the final holdout is opened."
        ),
    }
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "development_validation.json"
    markdown_path = output / "development_validation.md"
    temporary = json_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(json_path)
    lines = [
        "# Expanded Development and Validation Result",
        "",
        f"Qualified scores: **{payload['attempted']}**  ",
        f"Completed market outcomes: **{payload['completed']}**  ",
        f"Incomplete market outcomes: **{payload['incomplete']}**  ",
        "Holdout opened: **No**",
        "",
        "| Partition | 12-month records | Score vs. return | Score vs. SPY-relative return |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name in ("development", "validation"):
        result = partitions[name]
        lines.append(
            f"| {name.title()} | {result['observations']} | "
            f"{result['spearman_score_vs_company_return']:.3f} | "
            f"{result['spearman_score_vs_excess_return']:.3f} |"
        )
    lines.extend(["", report["interpretation"], ""])
    temporary_md = markdown_path.with_suffix(".md.part")
    temporary_md.write_text("\n".join(lines), encoding="utf-8")
    temporary_md.replace(markdown_path)
    return json_path, markdown_path


__all__ = ["build_expanded_report"]

"""Generate the one-time expanded-study final holdout report."""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median

from finance_news.coverage_sensitivity import _leave_one_out
from finance_news.validation_adjusted import analyze_industry_cohorts
from finance_news.validation_diagnostics import _spearman
from finance_news.validation_report import score_group, summarize_group


def build_final_holdout_report(
    outcomes_path: Path = Path("data/validation/expanded/holdout_market_outcomes.json"),
    score_manifest_path: Path = Path("data/validation/expanded/holdout_scores/manifest.json"),
    output_root: Path = Path("data/validation/expanded/report"),
) -> tuple[Path, Path]:
    outcomes = json.loads(Path(outcomes_path).read_text(encoding="utf-8"))
    scores = json.loads(Path(score_manifest_path).read_text(encoding="utf-8"))
    if outcomes.get("holdout_opened") is not True or scores.get("holdout_opened") is not True:
        raise ValueError("Final inputs are not certified holdout results.")
    records = []
    for row in outcomes["outcomes"]:
        if row["status"] != "completed":
            continue
        twelve = row["horizons"]["12_months"]
        if twelve["status"] != "completed":
            continue
        records.append(
            {
                **row,
                **twelve,
                "industry": row["industry_division"],
                "fiscal_year": int(row["filing_date"][:4]),
            }
        )
    adjusted = analyze_industry_cohorts(records)
    ranked = sorted(adjusted["records"], key=lambda row: row["score"])
    quintile_size = math.ceil(len(ranked) * 0.2)
    bottom_median = median(
        row["adjusted_excess_return_percent"] for row in ranked[:quintile_size]
    )
    top_median = median(
        row["adjusted_excess_return_percent"] for row in ranked[-quintile_size:]
    )
    correlation = _spearman(
        [row["score"] for row in records],
        [row["excess_return_percent"] for row in records],
    )
    company_robustness = _leave_one_out(records, "cik")
    industry_robustness = _leave_one_out(records, "industry_division")
    price_coverage = round(len(records) / outcomes["attempted"] * 100, 2)
    score_coverage = round(scores["completed"] / scores["attempted"] * 100, 2)
    checks = {
        "positive_primary_rank_correlation": correlation is not None and correlation > 0,
        "top_quintile_exceeds_bottom_quintile": top_median > bottom_median,
        "within_cohort_win_rate_above_50_percent": (
            adjusted["highest_vs_lowest"]["higher_score_win_rate_percent"] > 50
        ),
        "positive_when_each_company_removed": company_robustness["positive_rate_percent"] == 100,
        "positive_when_each_industry_removed": industry_robustness["positive_rate_percent"] == 100,
        "price_coverage_not_worse_than_pre_holdout": price_coverage >= 71.06,
    }
    report = {
        "schema_version": 1,
        "status": "final_holdout_report_complete",
        "holdout_opened": True,
        "holdout_score_attempts": scores["attempted"],
        "holdout_scores_completed": scores["completed"],
        "qualified_score_attempts": outcomes["attempted"],
        "completed_12_month_outcomes": len(records),
        "score_coverage_percent": score_coverage,
        "price_coverage_percent": price_coverage,
        "spearman_score_vs_excess_return": correlation,
        "score_groups": {
            group: summarize_group([r for r in records if score_group(r["score"]) == group])
            for group in ("Higher", "Middle", "Lower")
        },
        "industry_adjusted_top_quintile_median_percent": round(top_median, 2),
        "industry_adjusted_bottom_quintile_median_percent": round(bottom_median, 2),
        "within_industry_year": adjusted["highest_vs_lowest"],
        "leave_one_company_out": company_robustness,
        "leave_one_industry_out": industry_robustness,
        "success_checks": checks,
        "all_registered_checks_passed": all(checks.values()),
        "decision": (
            "The frozen baseline merits further research, but this study does not "
            "authorize a production score change or establish investability."
        ),
    }
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "final_holdout.json"
    markdown_path = output / "final_holdout.md"
    temporary = json_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(json_path)
    lines = [
        "# Equity Compass Final Holdout Result",
        "",
        f"Completed 12-month outcomes: **{len(records)}**  ",
        f"Score-to-SPY-relative-return rank correlation: **{correlation:.3f}**  ",
        f"Price coverage: **{price_coverage:.2f}%**  ",
        f"All registered checks passed: **{'Yes' if all(checks.values()) else 'No'}**",
        "",
        f"Top-quintile median industry-adjusted result: **{top_median:.2f}%**  ",
        f"Bottom-quintile median industry-adjusted result: **{bottom_median:.2f}%**  ",
        f"Within-industry/year higher-score win rate: **{adjusted['highest_vs_lowest']['higher_score_win_rate_percent']:.2f}%**",
        "",
        report["decision"],
        "",
    ]
    temporary_md = markdown_path.with_suffix(".md.part")
    temporary_md.write_text("\n".join(lines), encoding="utf-8")
    temporary_md.replace(markdown_path)
    return json_path, markdown_path


__all__ = ["build_final_holdout_report"]

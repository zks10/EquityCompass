"""Build point-in-time scores for the frozen expanded validation cohort."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests

from finance_news.score_snapshots import ScoreSnapshotError, save_historical_score_snapshot
from finance_news.sec_companies import DEFAULT_USER_AGENT


COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


def historical_ticker(record: dict[str, Any]) -> str:
    """Return the verified common-stock ticker recorded on the filing cover."""
    classes = record.get("eligible_security_classes", [])
    if not classes or not classes[0].get("ticker"):
        raise ValueError("Eligible record has no verified common-stock ticker.")
    return str(classes[0]["ticker"]).strip().upper()


def _download_companyfacts(cik: str, cache: Path, headers: dict[str, str]) -> Path:
    destination = cache / f"CIK{cik}.json"
    if destination.is_file():
        return destination
    response = requests.get(
        COMPANYFACTS_URL.format(cik=cik), headers=headers, timeout=60
    )
    response.raise_for_status()
    payload = response.json()
    temporary = destination.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(destination)
    return destination


def build_expanded_score_manifest(
    cohort_path: Path = Path("data/validation/expanded/eligible_cohort.json"),
    output_root: Path = Path("data/validation/expanded/scores"),
    facts_cache: Path = Path("data/validation/expanded/companyfacts"),
    partitions: tuple[str, ...] = ("development", "validation"),
    max_workers: int = 5,
) -> Path:
    """Reconstruct registered baseline scores without inspecting holdout records."""
    cohort = json.loads(Path(cohort_path).read_text(encoding="utf-8"))
    records = [r for r in cohort["records"] if r["partition"] in partitions]
    cache = Path(facts_cache)
    cache.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT)}
    ciks = sorted({record["cik"] for record in records})
    facts: dict[str, Path] = {}
    failures: dict[str, str] = {}

    def fetch(cik: str) -> tuple[str, Path | None, str | None]:
        try:
            return cik, _download_companyfacts(cik, cache, headers), None
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            return cik, None, str(exc)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for cik, path, error in executor.map(fetch, ciks):
            if path is not None:
                facts[cik] = path
            else:
                failures[cik] = error or "Unknown Company Facts error."

    attempts = []
    for record in records:
        base = {
            "cik": record["cik"],
            "accession_number": record["accession_number"],
            "filing_date": record["filing_date"],
            "partition": record["partition"],
            "industry_division": record["industry_division"],
            "ticker": historical_ticker(record),
        }
        if record["cik"] in failures:
            attempts.append({**base, "status": "incomplete", "reason": failures[record["cik"]]})
            continue
        try:
            snapshot = save_historical_score_snapshot(
                facts[record["cik"]],
                record["accession_number"],
                base["ticker"],
                output_root=Path(output_root) / "snapshots",
            )
            attempts.append({**base, "status": "completed", "snapshot_path": str(snapshot)})
        except (ScoreSnapshotError, OSError, ValueError) as exc:
            attempts.append({**base, "status": "incomplete", "reason": str(exc)})

    manifest = {
        "schema_version": 1,
        "status": (
            "holdout_scores_complete"
            if "holdout" in partitions
            else "development_validation_scores_complete"
        ),
        "holdout_opened": "holdout" in partitions,
        "partitions": list(partitions),
        "attempted": len(attempts),
        "completed": sum(a["status"] == "completed" for a in attempts),
        "incomplete": sum(a["status"] == "incomplete" for a in attempts),
        "attempts": attempts,
    }
    destination = Path(output_root) / "manifest.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


__all__ = ["build_expanded_score_manifest", "historical_ticker"]

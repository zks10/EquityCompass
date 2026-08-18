"""Freeze the historically verified expanded-study cohort."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def study_partition(filing_date: str) -> str:
    if "2017-01-01" <= filing_date <= "2021-12-31":
        return "development"
    if "2022-01-01" <= filing_date <= "2023-12-31":
        return "validation"
    if "2024-01-01" <= filing_date <= "2025-08-15":
        return "holdout"
    raise ValueError(f"Filing date is outside the registered study: {filing_date}")


def freeze_eligible_cohort(
    audit_path: Path = Path("data/validation/expanded/security_identity_audit.json"),
    output_path: Path = Path("data/validation/expanded/eligible_cohort.json"),
) -> Path:
    """Write the eligible records and registered time partitions before returns."""
    audit_file = Path(audit_path)
    audit = json.loads(audit_file.read_text(encoding="utf-8"))
    if audit.get("unattempted") != 0:
        raise ValueError("Historical identity resolution is not complete.")
    records = []
    for record in audit["records"]:
        if record["eligibility_status"] != "eligible":
            continue
        frozen = dict(record)
        frozen["partition"] = study_partition(record["filing_date"])
        records.append(frozen)
    records.sort(key=lambda row: (row["filing_date"], row["cik"], row["accession_number"]))
    payload = {
        "schema_version": 1,
        "status": "frozen_eligible_cohort",
        "returns_joined": False,
        "source_audit_sha256": hashlib.sha256(audit_file.read_bytes()).hexdigest(),
        "eligible_filings": len(records),
        "eligible_companies": len({row["cik"] for row in records}),
        "partition_counts": {
            name: sum(row["partition"] == name for row in records)
            for name in ("development", "validation", "holdout")
        },
        "records": records,
    }
    destination = Path(output_path)
    temporary = destination.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


__all__ = ["freeze_eligible_cohort", "study_partition"]

"""Build the preregistered expanded-study attempt universe from SEC indexes."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import requests

from finance_news.sec_companies import DEFAULT_USER_AGENT


INDEX_URL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx"
SUBMISSION_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


class ExpandedUniverseError(Exception):
    """Raised when the expanded attempt universe cannot be constructed."""


def parse_master_index(text: str) -> list[dict[str, str]]:
    """Extract non-amended 10-K rows from an SEC quarterly master index."""
    rows = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or parts[2] != "10-K":
            continue
        filename = parts[4].strip()
        accession = Path(filename).stem
        rows.append(
            {
                "cik": parts[0].strip().zfill(10),
                "company_name_at_filing": parts[1].strip(),
                "form": "10-K",
                "filing_date": parts[3].strip(),
                "accession_number": accession,
                "index_path": filename,
            }
        )
    return rows


def sic_division(sic: int) -> str:
    if 100 <= sic <= 999:
        return "Agriculture"
    if 1000 <= sic <= 1499:
        return "Mining"
    if 1500 <= sic <= 1799:
        return "Construction"
    if 2000 <= sic <= 3999:
        return "Manufacturing"
    if 4000 <= sic <= 4999:
        return "Transportation and utilities"
    if 5000 <= sic <= 5199:
        return "Wholesale trade"
    if 5200 <= sic <= 5999:
        return "Retail trade"
    if 6000 <= sic <= 6799:
        return "Finance"
    if 7000 <= sic <= 8999:
        return "Services"
    return "Other"


def _order_key(seed: int, cik: str) -> str:
    return hashlib.sha256(f"{seed}:{cik}".encode()).hexdigest()


def build_expanded_attempt_manifest(
    output_root: Path = Path("data/validation/expanded"),
    seed: int = 20260815,
    target_companies: int = 300,
    metadata_pool_size: int = 550,
) -> Path:
    """Download SEC indexes and freeze a deterministic, industry-stratified pool."""
    root = Path(output_root)
    cache = root / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT)}
    filings: list[dict[str, str]] = []
    for year in range(2017, 2026):
        for quarter in range(1, 5):
            cache_path = cache / f"{year}-q{quarter}-master.idx"
            if cache_path.is_file():
                text = cache_path.read_text(encoding="latin-1")
            else:
                response = requests.get(
                    INDEX_URL.format(year=year, quarter=quarter),
                    headers=headers,
                    timeout=60,
                )
                response.raise_for_status()
                text = response.text
                cache_path.write_text(text, encoding="latin-1")
            filings.extend(parse_master_index(text))

    by_cik: dict[str, list[dict[str, str]]] = {}
    for filing in filings:
        if "2017-01-01" <= filing["filing_date"] <= "2025-08-15":
            by_cik.setdefault(filing["cik"], []).append(filing)
    ordered_ciks = sorted(by_cik, key=lambda cik: _order_key(seed, cik))

    candidates = []
    for cik in ordered_ciks[:metadata_pool_size]:
        metadata_path = cache / f"CIK{cik}.json"
        try:
            if metadata_path.is_file():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            else:
                response = requests.get(
                    SUBMISSION_URL.format(cik=cik), headers=headers, timeout=60
                )
                response.raise_for_status()
                metadata = response.json()
                metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            sic = int(metadata.get("sic") or 0)
        except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError):
            continue
        if sic <= 0:
            continue
        candidates.append(
            {
                "cik": cik,
                "entity_name": str(metadata.get("name", "")),
                "sic": sic,
                "sic_description": str(metadata.get("sicDescription", "")),
                "industry_division": sic_division(sic),
                "current_tickers": metadata.get("tickers", []),
                "current_exchanges": metadata.get("exchanges", []),
                "historical_security_verification": "pending",
                "filings": sorted(by_cik[cik], key=lambda row: row["filing_date"]),
            }
        )

    divisions: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        divisions.setdefault(candidate["industry_division"], []).append(candidate)
    usable = {name: rows for name, rows in divisions.items() if len(rows) >= 20}
    selected = []
    for name in sorted(usable):
        selected.extend(usable[name][:20])
    selected_ciks = {row["cik"] for row in selected}
    remaining = sorted(
        (row for rows in usable.values() for row in rows if row["cik"] not in selected_ciks),
        key=lambda row: _order_key(seed, row["cik"]),
    )
    selected.extend(remaining[: max(0, target_companies - len(selected))])
    selected = selected[:target_companies]
    if len(selected) < target_companies:
        raise ExpandedUniverseError(
            f"Only {len(selected)} eligible candidates were found; target is {target_companies}."
        )

    manifest = {
        "schema_version": 1,
        "status": "frozen_attempt_universe",
        "registered_seed": seed,
        "source": "SEC EDGAR quarterly master indexes and submissions metadata",
        "returns_joined": False,
        "target_companies": target_companies,
        "selected_companies": len(selected),
        "industry_counts": {
            name: sum(row["industry_division"] == name for row in selected)
            for name in sorted({row["industry_division"] for row in selected})
        },
        "companies": selected,
    }
    destination = root / "attempt_manifest.json"
    temporary = destination.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


__all__ = [
    "ExpandedUniverseError",
    "build_expanded_attempt_manifest",
    "parse_master_index",
    "sic_division",
]

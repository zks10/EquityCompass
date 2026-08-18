"""Resolve filing-time security identities from SEC 10-K cover pages."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from finance_news.sec_companies import DEFAULT_USER_AGENT


SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
RESOLVER_VERSION = 2
COMMON_LABELS = ("common stock", "common shares", "ordinary shares")
INELIGIBLE_LABELS = ("preferred", "warrant", "unit", "note", "bond", "debenture")


class HistoricalIdentityError(Exception):
    """Raised when filing-time identity evidence cannot be processed."""


def extract_security_classes(html: str) -> list[dict[str, str]]:
    """Extract XBRL cover-page security title, symbol, and exchange by context."""
    soup = BeautifulSoup(html, "html.parser")
    names = {
        "dei:security12btitle": "security_title",
        "dei:tradingsymbol": "ticker",
        "dei:securityexchangename": "exchange",
    }
    contexts: dict[str, dict[str, str]] = {}
    for tag in soup.find_all():
        name = str(tag.get("name", tag.name or "")).lower()
        field = names.get(name)
        if field is None:
            continue
        context = str(tag.get("contextref", tag.get("contextRef", "default")))
        value = " ".join(tag.get_text(" ", strip=True).split())
        if value:
            contexts.setdefault(context, {})[field] = value
    return [value for value in contexts.values() if value]


def classify_security_classes(classes: list[dict[str, str]]) -> dict[str, Any]:
    """Classify filing eligibility from security classes disclosed on its cover."""
    complete = [
        item
        for item in classes
        if item.get("security_title") and item.get("ticker") and item.get("exchange")
    ]
    common = [
        item
        for item in complete
        if any(label in item["security_title"].lower() for label in COMMON_LABELS)
        and not any(label in item["security_title"].lower() for label in INELIGIBLE_LABELS)
    ]
    if common:
        return {
            "identity_status": "resolved",
            "eligibility_status": "eligible",
            "reason": "The 10-K cover identifies an exchange-listed common-stock class.",
            "eligible_security_classes": common,
            "reported_security_classes": classes,
        }
    if complete and all(
        any(label in item["security_title"].lower() for label in INELIGIBLE_LABELS)
        for item in complete
    ):
        return {
            "identity_status": "resolved",
            "eligibility_status": "ineligible",
            "reason": "The 10-K cover identifies only excluded security classes.",
            "eligible_security_classes": [],
            "reported_security_classes": classes,
        }
    return {
        "identity_status": "unresolved",
        "eligibility_status": "pending",
        "reason": "The 10-K cover lacks a complete common-stock title, ticker, and exchange set.",
        "eligible_security_classes": [],
        "reported_security_classes": classes,
    }


def _filing_documents(index_html: str) -> tuple[str | None, str | None]:
    soup = BeautifulSoup(index_html, "html.parser")
    primary = None
    instance = None
    for row in soup.select("table.tableFile tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue
        link = cells[2].find("a")
        if not link or not link.get("href"):
            continue
        document = str(link["href"]).rsplit("/", 1)[-1]
        document_type = cells[3].get_text(strip=True)
        if document_type == "10-K":
            primary = document
        elif document_type == "EX-101.INS":
            instance = document
    return primary, instance


def _save_audit(audit: dict[str, Any], audit_file: Path) -> None:
    """Refresh audit totals and atomically checkpoint resolved records."""
    records = audit["records"]
    audit["eligible"] = sum(r["eligibility_status"] == "eligible" for r in records)
    audit["ineligible"] = sum(r["eligibility_status"] == "ineligible" for r in records)
    audit["pending"] = sum(r["eligibility_status"] == "pending" for r in records)
    audit["unattempted"] = sum(
        r["identity_status"] == "pending_historical_verification" for r in records
    )
    if audit["unattempted"]:
        audit["status"] = "identity_resolution_in_progress"
    elif audit["pending"]:
        audit["status"] = "identity_resolution_review_required"
    else:
        audit["status"] = "identity_resolution_complete"
    temporary = audit_file.with_suffix(".json.part")
    temporary.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    temporary.replace(audit_file)


def _resolve_record(record: dict[str, Any], cache: Path, headers: dict[str, str]) -> dict[str, Any]:
    """Return filing-time identity evidence for one audit record."""
    cik_number = str(int(record["cik"]))
    accession = record["accession_number"]
    compact = accession.replace("-", "")
    record_cache = cache / f"{compact}.json"
    try:
        result = None
        if record_cache.is_file():
            result = json.loads(record_cache.read_text(encoding="utf-8"))
        if result is None or result["eligibility_status"] == "pending":
            base = f"{SEC_ARCHIVES}/{cik_number}/{compact}"
            index_response = requests.get(
                f"{base}/{accession}-index.html", headers=headers, timeout=30
            )
            index_response.raise_for_status()
            primary, instance = _filing_documents(index_response.text)
            if not primary:
                raise HistoricalIdentityError("Primary 10-K document was not listed.")
            filing_response = requests.get(
                f"{base}/{primary}",
                headers={**headers, "Range": "bytes=0-524287"},
                timeout=60,
            )
            filing_response.raise_for_status()
            classes = extract_security_classes(filing_response.text)
            if not classes and instance:
                instance_response = requests.get(
                    f"{base}/{instance}", headers=headers, timeout=60
                )
                instance_response.raise_for_status()
                classes = extract_security_classes(instance_response.text)
            result = classify_security_classes(classes)
            result["primary_document"] = primary
            result["xbrl_instance_document"] = instance
            result["source_url"] = f"{base}/{primary}"
            result["resolver_version"] = RESOLVER_VERSION
            record_cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
    except (requests.RequestException, HistoricalIdentityError, ValueError) as exc:
        return {
            "identity_status": "unresolved",
            "eligibility_status": "pending",
            "reason": f"SEC filing-time identity retrieval failed: {exc}",
            "resolver_version": RESOLVER_VERSION,
        }


def resolve_identity_audit(
    audit_path: Path = Path("data/validation/expanded/security_identity_audit.json"),
    cache_root: Path = Path("data/validation/expanded/identity_cache"),
    max_new_requests: int = 200,
    max_workers: int = 5,
) -> Path:
    """Resolve a bounded batch and atomically update the filing identity audit."""
    audit_file = Path(audit_path)
    audit = json.loads(audit_file.read_text(encoding="utf-8"))
    cache = Path(cache_root)
    cache.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT)}
    candidates = []
    for record in audit["records"]:
        needs_resolution = record["identity_status"] == "pending_historical_verification"
        needs_legacy_retry = (
            record["identity_status"] == "unresolved"
            and record.get("resolver_version", 1) < RESOLVER_VERSION
        )
        if needs_resolution or needs_legacy_retry:
            candidates.append(record)
        if len(candidates) >= max_new_requests:
            break

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = executor.map(
            lambda record: _resolve_record(record, cache, headers), candidates
        )
        for processed, (record, result) in enumerate(zip(candidates, results), start=1):
            record.update(result)
            if processed % 10 == 0:
                _save_audit(audit, audit_file)

    _save_audit(audit, audit_file)
    return audit_file


__all__ = [
    "HistoricalIdentityError",
    "classify_security_classes",
    "extract_security_classes",
    "resolve_identity_audit",
]

"""Extract event-item sections from cleaned SEC 8-K text."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from finance_news.section_extractor import SectionExtractionError


EVENT_HEADING = re.compile(
    r"^\s*item\s+([0-9]{1,2}\.[0-9]{2})\s*[.\-:]?\s*(.*?)\s*$",
    re.IGNORECASE,
)
MIN_EVENT_CHARACTERS = 40


class EventExtractionError(SectionExtractionError):
    """Raised when event items cannot be extracted or saved."""


@dataclass(frozen=True)
class EventItem:
    item_number: str
    title: str
    text: str


def extract_8k_items(text: str) -> list[EventItem]:
    """Extract and deduplicate Item x.xx sections from cleaned 8-K text."""
    if not text.strip():
        raise EventExtractionError("The processed 8-K is empty.")

    lines = text.splitlines()
    headings: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = EVENT_HEADING.match(line)
        if match:
            headings.append((index, match.group(1), match.group(2).strip()))

    candidates: dict[str, tuple[int, int, EventItem]] = {}
    for position, (start_index, item_number, inline_title) in enumerate(headings):
        end_index = (
            headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        )
        content = "\n".join(lines[start_index:end_index]).strip()
        if len(content) < MIN_EVENT_CHARACTERS:
            continue

        title = inline_title
        if not title and start_index + 1 < end_index:
            title = lines[start_index + 1].strip()
        event = EventItem(item_number=item_number, title=title, text=content + "\n")
        existing = candidates.get(item_number)
        if existing is None or len(content) > existing[0]:
            candidates[item_number] = (len(content), start_index, event)

    if not candidates:
        raise EventExtractionError("No Item x.xx event sections were found in the 8-K.")

    ordered = sorted(candidates.values(), key=lambda candidate: candidate[1])
    return [candidate[2] for candidate in ordered]


def save_event_items(
    processed_filing_path: Path,
    items: list[EventItem],
    output_directory: Path | None = None,
) -> list[Path]:
    """Save each extracted 8-K item as a separate text file."""
    source = Path(processed_filing_path)
    destination_directory = (
        Path(output_directory) if output_directory else source.parent / "events"
    )
    try:
        destination_directory.mkdir(parents=True, exist_ok=True)
        paths = []
        for item in items:
            safe_number = item.item_number.replace(".", "_")
            destination = destination_directory / f"item_{safe_number}.txt"
            temporary_path = destination.with_suffix(f"{destination.suffix}.part")
            temporary_path.write_text(item.text, encoding="utf-8")
            temporary_path.replace(destination)
            paths.append(destination)
    except OSError as exc:
        raise EventExtractionError(f"Could not save 8-K event items: {exc}") from exc
    return paths


def save_event_manifest(
    cik: str,
    ticker: str,
    company_name: str,
    filings: list[dict[str, Any]],
    output_root: Path = Path("data/processed/sec"),
) -> Path:
    """Save a normalized manifest covering the collected 8-K filings and items."""
    destination = Path(output_root) / cik / "eight_k_events.json"
    payload = {
        "ticker": ticker,
        "cik": cik,
        "company_name": company_name,
        "filings": filings,
    }
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_suffix(f"{destination.suffix}.part")
        temporary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(destination)
    except OSError as exc:
        raise EventExtractionError(f"Could not save 8-K event manifest: {exc}") from exc
    return destination


def item_metadata(item: EventItem, path: Path) -> dict[str, str]:
    """Return serializable metadata for one extracted item."""
    metadata = asdict(item)
    metadata.pop("text")
    metadata["text_path"] = str(path)
    return metadata

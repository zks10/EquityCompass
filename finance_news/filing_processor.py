"""Convert saved SEC filing HTML into clean plain text."""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup, Comment


DEFAULT_RAW_ROOT = Path("data/raw/sec")
DEFAULT_PROCESSED_ROOT = Path("data/processed/sec")
REMOVED_TAGS = ("script", "style", "noscript", "svg", "ix:header", "ix:hidden")
WHITESPACE = re.compile(r"\s+")


class FilingProcessingError(Exception):
    """Raised when a raw filing cannot be converted to clean text."""


def clean_filing_html(html: bytes | str) -> str:
    """Return readable text from a raw SEC HTML document."""
    if not html:
        raise FilingProcessingError("The raw filing document is empty.")

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(REMOVED_TAGS):
        tag.decompose()

    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()

    for tag in reversed(soup.find_all(True)):
        style = str(tag.get("style", "")).replace(" ", "").lower()
        if (
            tag.has_attr("hidden")
            or str(tag.get("aria-hidden", "")).lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        ):
            tag.decompose()

    lines = []
    for text in soup.stripped_strings:
        normalized = WHITESPACE.sub(" ", text).strip()
        if normalized:
            lines.append(normalized)

    if not lines:
        raise FilingProcessingError("No readable text was found in the filing.")

    return "\n".join(lines) + "\n"


def default_output_path(
    input_path: Path,
    raw_root: Path = DEFAULT_RAW_ROOT,
    processed_root: Path = DEFAULT_PROCESSED_ROOT,
) -> Path:
    """Map a raw SEC filing path to its processed text path."""
    try:
        relative_path = input_path.resolve().relative_to(raw_root.resolve())
    except ValueError as exc:
        raise FilingProcessingError(
            f"The input must be inside {raw_root}, or --output must be provided."
        ) from exc

    if len(relative_path.parts) != 3:
        raise FilingProcessingError(
            "Expected a raw path containing CIK, accession number, and filename."
        )

    cik, accession_number, _ = relative_path.parts
    return Path(processed_root) / cik / accession_number / "filing.txt"


def process_filing(input_path: Path, output_path: Path | None = None) -> Path:
    """Clean a saved raw filing and return the processed text path."""
    source = Path(input_path)
    if not source.is_file():
        raise FilingProcessingError(f"Raw filing not found: {source}")

    destination = Path(output_path) if output_path else default_output_path(source)

    try:
        cleaned_text = clean_filing_html(source.read_bytes())
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = destination.with_suffix(f"{destination.suffix}.part")
        temporary_path.write_text(cleaned_text, encoding="utf-8")
        temporary_path.replace(destination)
    except FilingProcessingError:
        raise
    except OSError as exc:
        raise FilingProcessingError(f"Could not process the filing: {exc}") from exc

    return destination

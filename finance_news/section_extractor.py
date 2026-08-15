"""Extract major sections from cleaned SEC 10-K and 10-Q text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


ITEM_HEADING = re.compile(
    r"^\s*item\s+([0-9]{1,2}[a-z]?)\s*[.\-:]?\s*(.*?)\s*$", re.IGNORECASE
)
MIN_SECTION_CHARACTERS = 80
FALLBACK_END_TITLES = {
    "business.txt": ("risk factors", "management", "discussion"),
    "mda.txt": (
        "management’s report on internal control",
        "management's report on internal control",
        "consolidated financial statements",
        "risk factors",
        "properties",
    ),
    "risk_factors.txt": ("financial statements",),
}


class SectionExtractionError(Exception):
    """Raised when required filing sections cannot be extracted."""


@dataclass(frozen=True)
class SectionDefinition:
    name: str
    start_item: str
    title_terms: tuple[str, ...]
    end_items: frozenset[str]
    filename: str


SECTION_DEFINITIONS = (
    SectionDefinition(
        name="Business",
        start_item="1",
        title_terms=("business",),
        end_items=frozenset({"1A"}),
        filename="business.txt",
    ),
    SectionDefinition(
        name="Risk Factors",
        start_item="1A",
        title_terms=("risk factor",),
        end_items=frozenset({"1B", "1C", "2"}),
        filename="risk_factors.txt",
    ),
    SectionDefinition(
        name="MD&A",
        start_item="7",
        title_terms=("management", "discussion", "analysis"),
        end_items=frozenset({"7A", "8"}),
        filename="mda.txt",
    ),
)

QUARTERLY_SECTION_DEFINITIONS = (
    SectionDefinition(
        name="MD&A",
        start_item="2",
        title_terms=("management", "discussion", "analysis"),
        end_items=frozenset({"3"}),
        filename="mda.txt",
    ),
    SectionDefinition(
        name="Risk Factors",
        start_item="1A",
        title_terms=("risk factor",),
        end_items=frozenset({"2"}),
        filename="risk_factors.txt",
    ),
)


def _heading_title(lines: list[str], index: int, inline_title: str) -> str:
    if inline_title:
        return inline_title.lower()
    if index + 1 < len(lines):
        return lines[index + 1].strip().lower()
    return ""


def _matches_title(title: str, terms: tuple[str, ...]) -> bool:
    return all(term in title for term in terms)


def _extract_section(lines: list[str], definition: SectionDefinition) -> str | None:
    headings: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = ITEM_HEADING.match(line)
        if match:
            item = match.group(1).upper()
            headings.append((index, item, match.group(2)))

    candidates: list[tuple[int, str]] = []
    for position, (start_index, item, inline_title) in enumerate(headings):
        if item != definition.start_item:
            continue

        title = _heading_title(lines, start_index, inline_title)
        if not _matches_title(title, definition.title_terms):
            continue

        end_index = None
        for candidate_index, candidate_item, _ in headings[position + 1 :]:
            if candidate_item in definition.end_items:
                end_index = candidate_index
                break

        if end_index is None:
            continue

        content = "\n".join(lines[start_index:end_index]).strip()
        if len(content) >= MIN_SECTION_CHARACTERS:
            candidates.append((len(content), content))

    # Some issuers organize the body by descriptive headings and include the
    # traditional Item numbers only in a cross-reference index. Consider titled
    # sections alongside Item-based candidates and keep the most substantive one.
    if not candidates or max(length for length, _content in candidates) < 500:
        fallback_ends = FALLBACK_END_TITLES.get(definition.filename, ())
        for start_index, line in enumerate(lines):
            title = line.strip().lower()
            if (
                not title
                or title.startswith("item ")
                or title.endswith((".", "?", "!", ";"))
                or len(title.split()) > 12
                or not _matches_title(title, definition.title_terms)
            ):
                continue
            nearby_lines = [
                candidate.strip()
                for candidate in lines[start_index + 1 : start_index + 8]
                if candidate.strip()
            ]
            if any(candidate.isdigit() for candidate in nearby_lines):
                continue
            end_index = None
            for candidate_index in range(start_index + 1, len(lines)):
                candidate = lines[candidate_index].strip().lower()
                if (
                    candidate
                    and len(candidate) <= 100
                    and not candidate.startswith("item ")
                    and any(candidate.startswith(end_title) for end_title in fallback_ends)
                ):
                    end_index = candidate_index
                    break
            if end_index is None:
                continue
            content = "\n".join(lines[start_index:end_index]).strip()
            if len(content) >= MIN_SECTION_CHARACTERS:
                candidates.append((len(content), content))

    if not candidates:
        return None

    return max(candidates, key=lambda candidate: candidate[0])[1] + "\n"


def _extract_required_sections(
    text: str, definitions: tuple[SectionDefinition, ...]
) -> dict[str, str]:
    if not text.strip():
        raise SectionExtractionError("The processed filing is empty.")

    lines = text.splitlines()
    sections: dict[str, str] = {}
    missing: list[str] = []

    for definition in definitions:
        content = _extract_section(lines, definition)
        if content is None:
            missing.append(definition.name)
        else:
            sections[definition.filename] = content

    if missing:
        raise SectionExtractionError(
            "Could not locate required section(s): " + ", ".join(missing) + "."
        )

    return sections


def _extract_available_sections(
    text: str, definitions: tuple[SectionDefinition, ...]
) -> dict[str, str]:
    """Return every section that can be identified without requiring all of them."""
    if not text.strip():
        raise SectionExtractionError("The processed filing is empty.")
    lines = text.splitlines()
    return {
        definition.filename: content
        for definition in definitions
        if (content := _extract_section(lines, definition)) is not None
    }


def extract_10k_sections(text: str) -> dict[str, str]:
    """Extract Business, Risk Factors, and MD&A from cleaned 10-K text."""
    return _extract_required_sections(text, SECTION_DEFINITIONS)


def extract_10q_sections(text: str) -> dict[str, str]:
    """Extract MD&A and Risk Factors from cleaned 10-Q text."""
    return _extract_required_sections(text, QUARTERLY_SECTION_DEFINITIONS)


def extract_sections_file(
    input_path: Path, output_directory: Path | None = None
) -> list[Path]:
    """Extract major sections from a processed filing and save separate files."""
    source = Path(input_path)
    if not source.is_file():
        raise SectionExtractionError(f"Processed filing not found: {source}")

    destination_directory = (
        Path(output_directory) if output_directory else source.parent / "sections"
    )

    try:
        filing_text = source.read_text(encoding="utf-8")
        sections = _extract_available_sections(filing_text, SECTION_DEFINITIONS)
        destination_directory.mkdir(parents=True, exist_ok=True)

        saved_paths = []
        for filename, content in sections.items():
            destination = destination_directory / filename
            temporary_path = destination.with_suffix(f"{destination.suffix}.part")
            temporary_path.write_text(content, encoding="utf-8")
            temporary_path.replace(destination)
            saved_paths.append(destination)
    except SectionExtractionError:
        raise
    except (OSError, UnicodeError) as exc:
        raise SectionExtractionError(f"Could not extract filing sections: {exc}") from exc

    return saved_paths


def extract_quarterly_sections_file(
    input_path: Path, output_directory: Path | None = None
) -> list[Path]:
    """Extract major sections from a processed 10-Q and save separate files."""
    source = Path(input_path)
    if not source.is_file():
        raise SectionExtractionError(f"Processed filing not found: {source}")

    destination_directory = (
        Path(output_directory) if output_directory else source.parent / "sections"
    )

    try:
        sections = _extract_available_sections(
            source.read_text(encoding="utf-8"), QUARTERLY_SECTION_DEFINITIONS
        )
        destination_directory.mkdir(parents=True, exist_ok=True)
        saved_paths = []
        for filename, content in sections.items():
            destination = destination_directory / filename
            temporary_path = destination.with_suffix(f"{destination.suffix}.part")
            temporary_path.write_text(content, encoding="utf-8")
            temporary_path.replace(destination)
            saved_paths.append(destination)
    except SectionExtractionError:
        raise
    except (OSError, UnicodeError) as exc:
        raise SectionExtractionError(f"Could not extract filing sections: {exc}") from exc

    return saved_paths

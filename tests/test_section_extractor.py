"""Tests for major 10-K section extraction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finance_news.section_extractor import (
    SectionExtractionError,
    extract_10k_sections,
    extract_10q_sections,
    extract_quarterly_sections_file,
    extract_sections_file,
)


SAMPLE_10K = """Table of Contents
Item 1.
Business
3
Item 1A.
Risk Factors
8
Item 7.
Management's Discussion and Analysis
20
Item 7A.
Market Risk
25
Item 1. Business
The company builds useful products.
It serves customers around the world.
Item 1A. Risk Factors
Competition and supply constraints could affect results.
The business operates in changing markets.
Item 1B. Unresolved Staff Comments
None.
Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations
Revenue increased during the year.
Management discusses liquidity and capital resources here.
Item 7A. Quantitative and Qualitative Disclosures About Market Risk
Interest-rate information follows.
Item 8. Financial Statements and Supplementary Data
Statements follow.
"""

SAMPLE_10Q = """Table of Contents
Item 2.
Management's Discussion and Analysis
12
Item 3.
Market Risk
20
Item 1A.
Risk Factors
25
Item 2.
Unregistered Sales
27
Item 2. Management's Discussion and Analysis of Financial Condition and Results of Operations
Quarterly revenue increased and management discusses operating results.
Liquidity and capital resources remained sufficient during the quarter.
Item 3. Quantitative and Qualitative Disclosures About Market Risk
Market-risk disclosures follow.
Item 1A. Risk Factors
The company faces competition, supply constraints, and regulatory uncertainty.
These risks could materially affect quarterly and future operating results.
Item 2. Unregistered Sales of Equity Securities and Use of Proceeds
Issuer purchase information follows.
"""


class Extract10KSectionsTests(unittest.TestCase):
    def test_prefers_substantive_cross_referenced_financial_section_mda(self) -> None:
        text = """ITEM 1. BUSINESS
The company describes its operations, customers, products, competition, and business model in substantive detail here.
ITEM 1A. RISK FACTORS
The company faces market, operational, regulatory, financial, and competitive risks that could affect future results.
ITEM 2. PROPERTIES
Property information follows.
ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS
Reference is made to Management's Discussion and Analysis in the Financial Section.
ITEM 8. FINANCIAL STATEMENTS
FINANCIAL SECTION
MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS
OVERVIEW
Management discusses operating results, liquidity, capital resources, and the business environment in substantive detail.
This discussion contains enough additional financial context to be the useful body rather than the short cross-reference.
MANAGEMENT'S REPORT ON INTERNAL CONTROL OVER FINANCIAL REPORTING
The internal-control report begins here.
"""

        sections = extract_10k_sections(text)

        self.assertIn("liquidity, capital resources", sections["mda.txt"])
        self.assertNotIn("internal-control report begins", sections["mda.txt"])

    def test_extracts_three_required_sections(self) -> None:
        sections = extract_10k_sections(SAMPLE_10K)

        self.assertEqual(
            set(sections), {"business.txt", "risk_factors.txt", "mda.txt"}
        )
        self.assertIn("builds useful products", sections["business.txt"])
        self.assertIn("supply constraints", sections["risk_factors.txt"])
        self.assertIn("liquidity", sections["mda.txt"])

    def test_chooses_body_instead_of_table_of_contents(self) -> None:
        sections = extract_10k_sections(SAMPLE_10K)

        self.assertNotEqual(sections["business.txt"].strip(), "Item 1.\nBusiness\n3")
        self.assertIn("serves customers", sections["business.txt"])

    def test_ends_each_section_at_next_item(self) -> None:
        sections = extract_10k_sections(SAMPLE_10K)

        self.assertNotIn("Risk Factors", sections["business.txt"])
        self.assertNotIn("Unresolved Staff", sections["risk_factors.txt"])
        self.assertNotIn("Market Risk", sections["mda.txt"])

    def test_reports_missing_required_section(self) -> None:
        without_mda = SAMPLE_10K.replace(
            "Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations",
            "Management Discussion",
        )

        with self.assertRaisesRegex(SectionExtractionError, "MD&A"):
            extract_10k_sections(without_mda)

    def test_rejects_empty_text(self) -> None:
        with self.assertRaisesRegex(SectionExtractionError, "empty"):
            extract_10k_sections("  ")

    def test_extracts_descriptive_heading_layout_without_item_numbers(self) -> None:
        filing = """Our Business
The company develops processors and related technology for customers worldwide.
It competes across several large and changing markets.
Management's Discussion and Analysis
Revenue changed during the year and management discusses operating performance.
Liquidity and capital resources remained important to the business.
Properties
The company owns and leases facilities.
Risk Factors
Competition, manufacturing complexity, and economic uncertainty could materially
and adversely affect the company's operations, cash flows, and financial results.
Financial Statements and Supplemental Details
The audited statements follow.
"""

        sections = extract_10k_sections(filing)

        self.assertIn("develops processors", sections["business.txt"])
        self.assertIn("Liquidity", sections["mda.txt"])
        self.assertIn("manufacturing complexity", sections["risk_factors.txt"])


class ExtractSectionsFileTests(unittest.TestCase):
    def test_writes_separate_section_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "filing.txt"
            output = root / "sections"
            source.write_text(SAMPLE_10K, encoding="utf-8")

            saved_paths = extract_sections_file(source, output)

            self.assertEqual(
                {path.name for path in saved_paths},
                {"business.txt", "risk_factors.txt", "mda.txt"},
            )
            self.assertTrue(all(path.is_file() for path in saved_paths))
            self.assertFalse(any(output.glob("*.part")))

    def test_reports_missing_input_file(self) -> None:
        with self.assertRaisesRegex(SectionExtractionError, "not found"):
            extract_sections_file(Path("missing-filing.txt"))


class Extract10QSectionsTests(unittest.TestCase):
    def test_extracts_quarterly_mda_and_risk_factors(self) -> None:
        sections = extract_10q_sections(SAMPLE_10Q)

        self.assertEqual(set(sections), {"mda.txt", "risk_factors.txt"})
        self.assertIn("Quarterly revenue increased", sections["mda.txt"])
        self.assertIn("regulatory uncertainty", sections["risk_factors.txt"])

    def test_quarterly_sections_end_at_correct_items(self) -> None:
        sections = extract_10q_sections(SAMPLE_10Q)

        self.assertNotIn("Market-risk disclosures", sections["mda.txt"])
        self.assertNotIn("Unregistered Sales", sections["risk_factors.txt"])

    def test_writes_quarterly_section_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "filing.txt"
            source.write_text(SAMPLE_10Q, encoding="utf-8")

            paths = extract_quarterly_sections_file(source)

            self.assertEqual(
                {path.name for path in paths}, {"mda.txt", "risk_factors.txt"}
            )


if __name__ == "__main__":
    unittest.main()

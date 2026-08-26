from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from finance_news.sec_filings import Filing
from finance_news.weekly.financial_refresh import (
    FinancialRefreshError,
    index_known_filings,
    ingest_equity_score_snapshot,
)
from finance_news.weekly.storage import connect_database, migrate_database


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
COMPANY_ID = "0000000001"


def filing(form, date, accession, document="report.htm"):
    return Filing(
        form=form,
        filing_date=date,
        accession_number=accession,
        primary_document=document,
        document_url=f"https://example.invalid/{accession}/{document}",
    )


class WeeklyFinancialRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "weekly.sqlite3"
        self.raw = self.root / "raw"
        self.processed = self.root / "processed"
        migrate_database(self.database)
        self.connection = connect_database(self.database)
        self.connection.execute(
            "INSERT INTO companies "
            "(company_id, current_ticker, company_name, created_at, updated_at) "
            "VALUES (?, 'FIC', 'Fictional Corporation', ?, ?)",
            (COMPANY_ID, "2026-08-26T00:00:00Z", "2026-08-26T00:00:00Z"),
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def test_indexes_known_accessions_and_updates_cursor(self):
        filings = (
            filing("8-K", "2026-08-25", "0000000001-26-000003"),
            filing("10-Q", "2026-08-10", "0000000001-26-000002"),
            filing("10-K", "2026-02-01", "0000000001-26-000001"),
        )
        summary = index_known_filings(
            self.connection, COMPANY_ID, filings, checked_at=NOW,
            raw_root=self.raw, processed_root=self.processed,
        )
        self.assertEqual(summary.latest_10k_accession, "0000000001-26-000001")
        self.assertEqual(summary.latest_10q_accession, "0000000001-26-000002")
        self.assertEqual(summary.latest_8k_accession, "0000000001-26-000003")
        cursor = self.connection.execute(
            "SELECT * FROM filing_refresh_state WHERE company_id = ?", (COMPANY_ID,)
        ).fetchone()
        self.assertEqual(cursor["latest_known_accession"], "0000000001-26-000003")
        self.assertEqual(json.loads(cursor["known_accessions_json"]), sorted(item.accession_number for item in filings))

    def test_reports_only_newly_indexed_accessions_on_repeat(self):
        first = (filing("10-K", "2026-02-01", "0000000001-26-000001"),)
        index_known_filings(self.connection, COMPANY_ID, first, checked_at=NOW, raw_root=self.raw, processed_root=self.processed)
        second = first + (filing("10-Q", "2026-08-10", "0000000001-26-000002"),)
        summary = index_known_filings(self.connection, COMPANY_ID, second, checked_at=NOW, raw_root=self.raw, processed_root=self.processed)
        self.assertEqual(summary.newly_indexed_accessions, ("0000000001-26-000002",))

    def test_registers_cached_filing_and_extracted_sections(self):
        item = filing("10-K", "2026-02-01", "0000000001-26-000001")
        compact = item.accession_number.replace("-", "")
        directory = self.processed / COMPANY_ID / compact
        sections = directory / "sections"
        sections.mkdir(parents=True)
        (directory / "filing.txt").write_text("Fictional filing body", encoding="utf-8")
        (sections / "risk_factors.txt").write_text("Fictional risks", encoding="utf-8")
        summary = index_known_filings(
            self.connection, COMPANY_ID, (item,), checked_at=NOW,
            raw_root=self.raw, processed_root=self.processed,
        )
        self.assertEqual(summary.cached_artifact_count, 1)
        stored = self.connection.execute("SELECT * FROM filings").fetchone()
        self.assertEqual(stored["processing_status"], "cached")
        self.assertIsNotNone(stored["source_id"])
        section = self.connection.execute("SELECT * FROM filing_sections").fetchone()
        self.assertEqual(section["section_type"], "risk_factors")
        source = self.connection.execute("SELECT * FROM source_documents").fetchone()
        self.assertEqual(
            source["content_hash"],
            hashlib.sha256(b"Fictional filing body").hexdigest(),
        )

    def test_rejects_duplicate_accessions(self):
        item = filing("10-K", "2026-02-01", "0000000001-26-000001")
        with self.assertRaisesRegex(FinancialRefreshError, "duplicate"):
            index_known_filings(
                self.connection, COMPANY_ID, (item, item), checked_at=NOW,
                raw_root=self.raw, processed_root=self.processed,
            )

    def test_stores_provenance_backed_equity_score(self):
        facts_path, metrics_path = self._financial_files()
        record = ingest_equity_score_snapshot(
            self.connection, facts_path, metrics_path, calculated_at=NOW
        )
        self.assertEqual(record.company_id, COMPANY_ID)
        self.assertEqual(record.score, 96)
        self.assertEqual(record.available_components, 4)
        self.assertEqual(record.source_accessions, ("0000000001-26-000001",))
        stored = self.connection.execute("SELECT * FROM equity_score_snapshots").fetchone()
        self.assertEqual(stored["score"], 96)
        self.assertEqual(stored["information_available_at"], "2026-02-01")
        self.assertEqual(len(json.loads(stored["source_ids_json"])), 2)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM financial_observations").fetchone()[0], 5)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM derived_financial_metrics").fetchone()[0], 4)

    def test_repeated_equity_score_ingestion_is_idempotent(self):
        facts_path, metrics_path = self._financial_files()
        ingest_equity_score_snapshot(self.connection, facts_path, metrics_path, calculated_at=NOW)
        ingest_equity_score_snapshot(self.connection, facts_path, metrics_path, calculated_at=NOW)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM equity_score_snapshots").fetchone()[0], 1)

    def test_rejects_mixed_score_provenance_dates(self):
        facts_path, metrics_path = self._financial_files()
        payload = json.loads(facts_path.read_text(encoding="utf-8"))
        payload["facts"][0]["filed"] = "2026-02-02"
        facts_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(FinancialRefreshError, "information-available date"):
            ingest_equity_score_snapshot(self.connection, facts_path, metrics_path, calculated_at=NOW)

    def _financial_files(self):
        accession = "0000000001-26-000001"
        facts = {
            "ticker": "FIC", "cik": COMPANY_ID, "entity_name": "Fictional Corporation",
            "facts": [
                {"metric": metric, "value": value, "unit": "USD", "fiscal_year": 2025,
                 "period_end": "2025-12-31", "filed": "2026-02-01",
                 "accession_number": accession, "form": "10-K"}
                for metric, value in (
                    ("revenue", 1000), ("net_income", 250), ("assets", 1000),
                    ("liabilities", 500), ("operating_cash_flow", 250),
                )
            ],
        }
        metrics = {
            "ticker": "FIC", "cik": COMPANY_ID, "entity_name": "Fictional Corporation",
            "periods": [{
                "fiscal_year": 2025, "period_end": "2025-12-31",
                "revenue_growth_percent": 10,
                "net_profit_margin_percent": 25,
                "liabilities_to_assets_percent": 50,
                "operating_cash_flow_margin_percent": 25,
            }],
        }
        facts_path = self.root / "financial_facts.json"
        metrics_path = self.root / "derived_metrics.json"
        facts_path.write_text(json.dumps(facts), encoding="utf-8")
        metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
        return facts_path, metrics_path


if __name__ == "__main__":
    unittest.main()

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from finance_news.weekly.event_intelligence import (
    EventFamily,
    EventIntelligenceError,
    EventStatus,
    EventThread,
    EvidenceDirection,
    EvidenceItem,
    EvidenceRelationship,
    FundamentalImpact,
    SourceDocument,
    attach_evidence,
    calculate_market_anchor,
    create_event_thread,
    store_evidence_item,
    store_market_anchor,
    store_source_document,
)
from finance_news.weekly.market import DailyBar
from finance_news.weekly.models import FactInterpretationType
from finance_news.weekly.storage import connect_database, migrate_database


NOW = datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
COMPANY_ID = "0000000001"
OTHER_COMPANY_ID = "0000000002"


def bars(symbol, values, start=date(2026, 8, 17)):
    return tuple(
        DailyBar(
            symbol=symbol, session_date=start + timedelta(days=index),
            open=value, high=value, low=value, close=value, adjusted_close=value,
            volume=1_000_000, currency="USD", provider="fixture", collected_at=NOW,
        )
        for index, value in enumerate(values)
    )


class EventIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "weekly.sqlite3"
        migrate_database(self.database)
        self.connection = connect_database(self.database)
        for company_id, ticker in ((COMPANY_ID, "FIC"), (OTHER_COMPANY_ID, "OTH")):
            self.connection.execute(
                "INSERT INTO companies "
                "(company_id, current_ticker, company_name, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (company_id, ticker, f"{ticker} Corporation", _iso(NOW), _iso(NOW)),
            )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def test_stores_source_document_idempotently_and_rejects_drift(self):
        source = self._source()
        store_source_document(self.connection, source)
        store_source_document(self.connection, source)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0], 1)
        with self.assertRaisesRegex(EventIntelligenceError, "different content"):
            store_source_document(self.connection, replace(source, title="Changed title"))

    def test_stores_evidence_with_fact_provenance(self):
        source = self._source()
        store_source_document(self.connection, source)
        evidence = self._evidence()
        store_evidence_item(self.connection, evidence)
        row = self.connection.execute("SELECT * FROM evidence_items").fetchone()
        self.assertEqual(row["fact_interpretation_type"], "reported_fact")
        self.assertEqual(row["source_location"], "Item 2.02")
        self.assertEqual(row["direction"], "negative")

    def test_stores_evidence_idempotently_and_rejects_drift(self):
        store_source_document(self.connection, self._source())
        evidence = self._evidence()
        store_evidence_item(self.connection, evidence)
        store_evidence_item(self.connection, evidence)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0], 1)
        with self.assertRaisesRegex(EventIntelligenceError, "different content"):
            store_evidence_item(self.connection, replace(evidence, claim="Changed claim"))

    def test_rejects_evidence_linked_to_another_company_source(self):
        source = replace(self._source(), company_id=OTHER_COMPANY_ID)
        store_source_document(self.connection, source)
        with self.assertRaisesRegex(EventIntelligenceError, "another company"):
            store_evidence_item(self.connection, self._evidence())

    def test_creates_event_and_attaches_company_consistent_evidence(self):
        self._store_source_and_evidence()
        event = self._event()
        create_event_thread(self.connection, event)
        attach_evidence(
            self.connection, event.event_id, "evidence-1", EvidenceRelationship.PRIMARY,
            attached_at=NOW, match_confidence=94,
        )
        stored = self.connection.execute("SELECT * FROM event_threads").fetchone()
        attachment = self.connection.execute("SELECT * FROM event_evidence").fetchone()
        self.assertEqual(stored["current_status"], "detected")
        self.assertEqual(stored["fundamental_impact"], "unknown")
        self.assertEqual(attachment["relationship"], "primary")
        self.assertEqual(attachment["match_confidence"], 94)

    def test_rejects_new_event_in_resolution_state(self):
        self._store_source_and_evidence()
        with self.assertRaisesRegex(EventIntelligenceError, "only start"):
            create_event_thread(
                self.connection,
                replace(self._event(), status=EventStatus.RESOLVED_POSITIVE),
            )

    def test_rejects_cross_company_event_attachment(self):
        self._store_source_and_evidence()
        create_event_thread(self.connection, replace(self._event(), company_id=OTHER_COMPANY_ID, primary_evidence_id=None))
        with self.assertRaisesRegex(EventIntelligenceError, "same company"):
            attach_evidence(
                self.connection, "event-1", "evidence-1", EvidenceRelationship.SUPPORTING,
                attached_at=NOW, match_confidence=80,
            )

    def test_calculates_prior_close_anchor_and_benchmark_adjusted_reaction(self):
        event_time = datetime(2026, 8, 20, 13, tzinfo=timezone.utc)
        stock = bars("FIC", [100, 102, 100, 90, 85, 88])
        benchmark = bars("SPY", [100, 101, 100, 98, 97, 99])
        anchor = calculate_market_anchor("event-1", event_time, stock, benchmark)
        self.assertEqual(anchor.anchor_session, "2026-08-19")
        self.assertEqual(anchor.anchor_price, 100)
        self.assertEqual(anchor.first_reaction_session, "2026-08-20")
        self.assertAlmostEqual(anchor.initial_reaction, -0.10)
        self.assertAlmostEqual(anchor.benchmark_initial_reaction, -0.02)
        self.assertAlmostEqual(anchor.benchmark_adjusted_initial_reaction, -0.08)
        self.assertAlmostEqual(anchor.maximum_drawdown, -0.15)

    def test_market_anchor_uses_adjusted_prices(self):
        event_time = datetime(2026, 8, 20, 13, tzinfo=timezone.utc)
        stock = list(bars("FIC", [100, 100, 100, 50]))
        split = stock[-1]
        stock[-1] = replace(split, adjusted_close=100)
        anchor = calculate_market_anchor("event-1", event_time, stock, bars("SPY", [100] * 4))
        self.assertEqual(anchor.initial_reaction, 0)

    def test_persists_anchor_idempotently_and_rejects_drift(self):
        self._store_source_and_evidence()
        create_event_thread(self.connection, self._event())
        anchor = calculate_market_anchor(
            "event-1", datetime(2026, 8, 20, 13, tzinfo=timezone.utc),
            bars("FIC", [100, 100, 100, 90]), bars("SPY", [100] * 4),
        )
        store_market_anchor(self.connection, anchor)
        store_market_anchor(self.connection, anchor)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM event_market_anchors").fetchone()[0], 1)
        with self.assertRaisesRegex(EventIntelligenceError, "conflicts"):
            store_market_anchor(self.connection, replace(anchor, anchor_price=101))

    def test_anchor_requires_sessions_on_both_sides_of_event(self):
        with self.assertRaisesRegex(EventIntelligenceError, "before and after"):
            calculate_market_anchor(
                "event-1", datetime(2026, 8, 10, tzinfo=timezone.utc),
                bars("FIC", [100, 90]), bars("SPY", [100, 100]),
            )

    def _source(self):
        return SourceDocument(
            source_id="source-1", company_id=COMPANY_ID, source_type="SEC_8K",
            source_tier=1, title="Fictional Current Report", publisher="SEC",
            published_at=NOW, effective_at=NOW, collected_at=NOW,
            canonical_url="https://example.invalid/filing", provider="fixture",
            content_hash="abc123", accession_number="0000000001-26-000001",
            metadata={"form": "8-K"},
        )

    def _evidence(self):
        return EvidenceItem(
            evidence_id="evidence-1", source_id="source-1", company_id=COMPANY_ID,
            evidence_type="operational_status", claim="A fictional facility paused production.",
            direction=EvidenceDirection.NEGATIVE, materiality=75, reliability=95,
            confidence=92, effective_at=NOW, extracted_at=NOW,
            source_location="Item 2.02", fact_type=FactInterpretationType.REPORTED_FACT,
            extraction_method="fixture", extraction_version="fixture-v1",
        )

    def _event(self):
        return EventThread(
            event_id="event-1", company_id=COMPANY_ID,
            event_family=EventFamily.OPERATIONS_SUPPLY,
            title="Fictional facility interruption",
            summary="A fictional production facility paused operations.",
            detected_at=NOW, event_started_at=NOW, status=EventStatus.DETECTED,
            materiality=75, initial_severity=70, current_severity=70,
            fundamental_impact=FundamentalImpact.UNKNOWN, evidence_confidence=85,
            primary_evidence_id="evidence-1",
        )

    def _store_source_and_evidence(self):
        store_source_document(self.connection, self._source())
        store_evidence_item(self.connection, self._evidence())


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()

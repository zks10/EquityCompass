import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from finance_news.weekly.storage import connect_database, migrate_database
from finance_news.weekly.universe import (
    UniverseError,
    load_pilot_universe,
    store_universe_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "config" / "phase3" / "pilot-universe-v1.json"
COLLECTED_AT = datetime(2026, 8, 23, 12, tzinfo=timezone.utc)


class PilotUniverseTests(unittest.TestCase):
    def test_loads_diversified_deterministic_pilot(self):
        snapshot = load_pilot_universe(PILOT, collected_at=COLLECTED_AT)
        self.assertEqual(snapshot.universe_id, "phase3-pilot-v1")
        self.assertEqual(len(snapshot.members), 20)
        self.assertGreaterEqual(len({member.sector for member in snapshot.members}), 5)
        self.assertTrue(all(len(member.company_id) == 10 for member in snapshot.members))

    def test_source_hash_is_stable(self):
        first = load_pilot_universe(PILOT, collected_at=COLLECTED_AT)
        second = load_pilot_universe(PILOT, collected_at=COLLECTED_AT)
        self.assertEqual(first.source_hash, second.source_hash)

    def test_rejects_duplicate_ticker(self):
        payload = json.loads(PILOT.read_text(encoding="utf-8"))
        payload["members"][1]["ticker"] = payload["members"][0]["ticker"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pilot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(UniverseError, "unique"):
                load_pilot_universe(path, collected_at=COLLECTED_AT)

    def test_rejects_unexpected_member_field(self):
        payload = json.loads(PILOT.read_text(encoding="utf-8"))
        payload["members"][0]["weight"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pilot.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(UniverseError, "fields"):
                load_pilot_universe(path, collected_at=COLLECTED_AT)


class StorePilotUniverseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "weekly.sqlite3"
        migrate_database(self.database)
        self.connection = connect_database(self.database)
        self.snapshot = load_pilot_universe(PILOT, collected_at=COLLECTED_AT)

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def test_stores_companies_and_frozen_membership(self):
        store_universe_snapshot(self.connection, self.snapshot)
        companies = self.connection.execute("SELECT COUNT(*) AS count FROM companies").fetchone()["count"]
        members = self.connection.execute("SELECT COUNT(*) AS count FROM universe_members").fetchone()["count"]
        status = self.connection.execute("SELECT status FROM universe_snapshots").fetchone()["status"]
        self.assertEqual(companies, 20)
        self.assertEqual(members, 20)
        self.assertEqual(status, "frozen")

    def test_repeated_store_is_idempotent(self):
        store_universe_snapshot(self.connection, self.snapshot)
        store_universe_snapshot(self.connection, self.snapshot)
        count = self.connection.execute("SELECT COUNT(*) AS count FROM universe_snapshots").fetchone()["count"]
        self.assertEqual(count, 1)

    def test_rejects_same_universe_id_with_different_content(self):
        store_universe_snapshot(self.connection, self.snapshot)
        changed = copy.copy(self.snapshot)
        object.__setattr__(changed, "source_hash", "different")
        with self.assertRaisesRegex(UniverseError, "different content"):
            store_universe_snapshot(self.connection, changed)


if __name__ == "__main__":
    unittest.main()

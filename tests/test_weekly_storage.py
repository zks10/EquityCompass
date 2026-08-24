from pathlib import Path
import sqlite3
import tempfile
import unittest

from finance_news.weekly.storage import (
    WeeklyStorageError,
    connect_database,
    current_schema_version,
    discover_migrations,
    migrate_database,
    transaction,
)


class WeeklyStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "weekly.sqlite3"

    def tearDown(self):
        self.temporary.cleanup()

    def test_discovers_numbered_repository_migrations(self):
        migrations = discover_migrations()
        self.assertEqual([item.version for item in migrations], [1])
        self.assertEqual(migrations[0].name, "001_foundation.sql")

    def test_migrates_empty_database_and_records_version(self):
        self.assertEqual(migrate_database(self.database), 1)
        connection = connect_database(self.database)
        try:
            self.assertEqual(current_schema_version(connection), 1)
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue({
                "companies", "universe_snapshots", "weekly_runs",
                "detector_triggers", "detector_results", "ranking_assessments",
                "final_selections",
            }.issubset(tables))
        finally:
            connection.close()

    def test_repeated_migration_is_idempotent(self):
        migrate_database(self.database)
        migrate_database(self.database)
        connection = connect_database(self.database)
        try:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM schema_migrations"
            ).fetchone()["count"]
            self.assertEqual(count, 1)
        finally:
            connection.close()

    def test_enforces_foreign_keys(self):
        migrate_database(self.database)
        connection = connect_database(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO universe_members "
                    "(universe_id, company_id, ticker, company_name, membership_status, identity_status) "
                    "VALUES ('missing', '0000000001', 'FIC', 'Fictional', 'active', 'resolved')"
                )
        finally:
            connection.close()

    def test_transaction_rolls_back_all_writes(self):
        migrate_database(self.database)
        connection = connect_database(self.database)
        try:
            with self.assertRaisesRegex(RuntimeError, "stop"):
                with transaction(connection):
                    connection.execute(
                        "INSERT INTO companies "
                        "(company_id, current_ticker, company_name, created_at, updated_at) "
                        "VALUES ('0000000001', 'FIC', 'Fictional', '2026-08-23T00:00:00Z', '2026-08-23T00:00:00Z')"
                    )
                    raise RuntimeError("stop")
            count = connection.execute("SELECT COUNT(*) AS count FROM companies").fetchone()["count"]
            self.assertEqual(count, 0)
        finally:
            connection.close()

    def test_rejects_nested_transaction(self):
        migrate_database(self.database)
        connection = connect_database(self.database)
        try:
            with transaction(connection):
                with self.assertRaisesRegex(WeeklyStorageError, "Nested"):
                    with transaction(connection):
                        pass
        finally:
            connection.close()

    def test_database_preserves_detector_null_zero_and_failure_semantics(self):
        migrate_database(self.database)
        connection = connect_database(self.database)
        try:
            self._seed_run(connection)
            base = (
                "INSERT INTO detector_results "
                "(result_id, run_id, company_id, detector, applicable, analysis_status, score, "
                "evaluated_at, methodology_version) VALUES (?, 'run-1', '0000000001', ?, ?, ?, ?, "
                "'2026-08-23T12:00:00Z', 'phase-3.1-v1')"
            )
            connection.execute(base, ("result-zero", "market_overreaction", 1, "completed", 0))
            connection.execute(base, ("result-na", "valuation_reset", 0, "not_applicable", None))
            connection.execute(base, ("result-failed", "emerging_catalyst", 1, "failed", None))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(base, ("bad", "temporary_headwind", 0, "not_applicable", 0))
        finally:
            connection.close()

    def _seed_run(self, connection):
        connection.execute(
            "INSERT INTO companies "
            "(company_id, current_ticker, company_name, created_at, updated_at) "
            "VALUES ('0000000001', 'FIC', 'Fictional', '2026-08-23T00:00:00Z', '2026-08-23T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO universe_snapshots "
            "(universe_id, universe_name, effective_at, collected_at, provider, status) "
            "VALUES ('pilot-1', 'Pilot', '2026-08-22', '2026-08-23T00:00:00Z', 'fixture', 'frozen')"
        )
        connection.execute(
            "INSERT INTO weekly_runs "
            "(run_id, week_ending, information_cutoff, market_data_through, universe_id, "
            "methodology_version, configuration_version, database_schema_version, status, started_at) "
            "VALUES ('run-1', '2026-08-21', '2026-08-22T08:00:00Z', '2026-08-21', 'pilot-1', "
            "'phase-3.1-v1', 'opportunity-v1', 1, 'created', '2026-08-23T00:00:00Z')"
        )
        connection.commit()


if __name__ == "__main__":
    unittest.main()

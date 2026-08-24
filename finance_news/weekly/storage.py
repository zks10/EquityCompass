"""SQLite lifecycle and transaction helpers for Phase 3.1 research state."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


DEFAULT_DATABASE_PATH = Path("data/phase3/equity_compass.sqlite3")
DEFAULT_MIGRATIONS_PATH = Path(__file__).with_name("migrations")
MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


class WeeklyStorageError(Exception):
    """Raised when the weekly research database cannot be prepared safely."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path


def connect_database(path: Path | str = DEFAULT_DATABASE_PATH) -> sqlite3.Connection:
    """Open a configured SQLite connection without applying migrations."""
    database = Path(path)
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise WeeklyStorageError(f"Could not open weekly database: {exc}") from exc


def discover_migrations(path: Path | str = DEFAULT_MIGRATIONS_PATH) -> tuple[Migration, ...]:
    """Return strictly ordered repository-owned SQL migrations."""
    root = Path(path)
    try:
        files = sorted(item for item in root.iterdir() if item.is_file())
    except OSError as exc:
        raise WeeklyStorageError(f"Could not read migrations: {exc}") from exc
    migrations = []
    seen: set[int] = set()
    for file in files:
        match = MIGRATION_NAME.fullmatch(file.name)
        if not match:
            continue
        version = int(match.group(1))
        if version in seen:
            raise WeeklyStorageError(f"Duplicate migration version: {version}.")
        seen.add(version)
        migrations.append(Migration(version, file.name, file))
    if not migrations:
        raise WeeklyStorageError("No database migrations were found.")
    return tuple(sorted(migrations, key=lambda item: item.version))


def migrate_database(
    path: Path | str = DEFAULT_DATABASE_PATH,
    migrations_path: Path | str = DEFAULT_MIGRATIONS_PATH,
) -> int:
    """Apply every pending migration atomically and return the schema version."""
    migrations = discover_migrations(migrations_path)
    connection = connect_database(path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, applied_at TEXT NOT NULL)"
        )
        connection.commit()
        applied = {
            row["version"]: row["name"]
            for row in connection.execute("SELECT version, name FROM schema_migrations")
        }
        known = {migration.version: migration.name for migration in migrations}
        for version, name in applied.items():
            if known.get(version) != name:
                raise WeeklyStorageError(
                    f"Applied migration {version} does not match repository migrations."
                )
        for migration in migrations:
            if migration.version in applied:
                continue
            sql = migration.path.read_text(encoding="utf-8")
            safe_name = migration.name.replace("'", "''")
            script = (
                "BEGIN IMMEDIATE;\n" + sql + "\n"
                f"INSERT INTO schema_migrations(version, name, applied_at) "
                f"VALUES ({migration.version}, '{safe_name}', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));\n"
                "COMMIT;"
            )
            try:
                connection.executescript(script)
            except (OSError, sqlite3.Error) as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise WeeklyStorageError(
                    f"Migration {migration.name} failed: {exc}"
                ) from exc
        return migrations[-1].version
    finally:
        connection.close()


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit a logical write unit or roll it back completely on failure."""
    if connection.in_transaction:
        raise WeeklyStorageError("Nested weekly database transactions are not supported.")
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def current_schema_version(connection: sqlite3.Connection) -> int:
    """Return the latest applied migration version, or zero for an empty database."""
    try:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["version"])


__all__ = [
    "DEFAULT_DATABASE_PATH", "Migration", "WeeklyStorageError", "connect_database",
    "current_schema_version", "discover_migrations", "migrate_database", "transaction",
]

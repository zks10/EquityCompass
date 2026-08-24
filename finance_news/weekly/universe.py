"""Deterministic pilot-universe loading and persistence."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from finance_news.weekly.storage import WeeklyStorageError, transaction


CIK_PATTERN = re.compile(r"^\d{10}$")
TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class UniverseError(ValueError):
    """Raised when a universe snapshot is invalid or conflicts with stored state."""


@dataclass(frozen=True)
class UniverseMember:
    company_id: str
    ticker: str
    company_name: str
    sector: str
    industry: str

    def __post_init__(self) -> None:
        if not CIK_PATTERN.fullmatch(self.company_id):
            raise UniverseError("Pilot company_id must be a normalized 10-digit SEC CIK.")
        if not TICKER_PATTERN.fullmatch(self.ticker):
            raise UniverseError(f"Invalid pilot ticker: {self.ticker}.")
        if not all(value.strip() for value in (self.company_name, self.sector, self.industry)):
            raise UniverseError("Pilot company name, sector, and industry are required.")


@dataclass(frozen=True)
class UniverseSnapshot:
    schema_version: int
    universe_id: str
    universe_name: str
    effective_at: date
    collected_at: datetime
    provider: str
    source_path: str
    source_hash: str
    members: tuple[UniverseMember, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise UniverseError("Only universe schema version 1 is supported.")
        if self.collected_at.tzinfo is None or self.collected_at.utcoffset() is None:
            raise UniverseError("Universe collection timestamp must be timezone-aware.")
        if not 15 <= len(self.members) <= 25:
            raise UniverseError("The development pilot must contain between 15 and 25 companies.")
        company_ids = [member.company_id for member in self.members]
        tickers = [member.ticker for member in self.members]
        if len(company_ids) != len(set(company_ids)) or len(tickers) != len(set(tickers)):
            raise UniverseError("Pilot company IDs and tickers must be unique.")
        if len({member.sector for member in self.members}) < 5:
            raise UniverseError("The development pilot must cover at least five sectors.")


def load_pilot_universe(
    path: Path | str,
    *,
    collected_at: datetime | None = None,
) -> UniverseSnapshot:
    """Load a reviewed local pilot file without writes or network access."""
    source = Path(path)
    try:
        raw = source.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise UniverseError(f"Could not load pilot universe: {exc}") from exc
    expected = {"schema_version", "universe_id", "universe_name", "effective_at", "members"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise UniverseError("Pilot universe fields do not match schema version 1.")
    if not isinstance(payload["members"], list):
        raise UniverseError("Pilot universe members must be a list.")
    member_fields = {"company_id", "ticker", "company_name", "sector", "industry"}
    members = []
    for item in payload["members"]:
        if not isinstance(item, dict) or set(item) != member_fields:
            raise UniverseError("Pilot universe member fields do not match schema version 1.")
        members.append(UniverseMember(**item))
    timestamp = collected_at or datetime.now(timezone.utc)
    return UniverseSnapshot(
        schema_version=payload["schema_version"],
        universe_id=payload["universe_id"],
        universe_name=payload["universe_name"],
        effective_at=date.fromisoformat(payload["effective_at"]),
        collected_at=timestamp,
        provider="reviewed_local_pilot",
        source_path=str(source),
        source_hash=hashlib.sha256(raw).hexdigest(),
        members=tuple(members),
    )


def store_universe_snapshot(
    connection: sqlite3.Connection,
    snapshot: UniverseSnapshot,
) -> None:
    """Persist a pilot snapshot idempotently, rejecting identity drift."""
    existing = connection.execute(
        "SELECT source_hash FROM universe_snapshots WHERE universe_id = ?",
        (snapshot.universe_id,),
    ).fetchone()
    if existing is not None and existing["source_hash"] != snapshot.source_hash:
        raise UniverseError(
            f"Universe {snapshot.universe_id} already exists with different content."
        )
    collected_at = snapshot.collected_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        with transaction(connection):
            for member in snapshot.members:
                connection.execute(
                    "INSERT INTO companies "
                    "(company_id, current_ticker, company_name, active, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, ?, ?) "
                    "ON CONFLICT(company_id) DO UPDATE SET "
                    "current_ticker = excluded.current_ticker, company_name = excluded.company_name, "
                    "active = 1, updated_at = excluded.updated_at",
                    (member.company_id, member.ticker, member.company_name, collected_at, collected_at),
                )
            connection.execute(
                "INSERT OR IGNORE INTO universe_snapshots "
                "(universe_id, universe_name, effective_at, collected_at, provider, source_path, source_hash, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'frozen')",
                (
                    snapshot.universe_id, snapshot.universe_name, snapshot.effective_at.isoformat(),
                    collected_at, snapshot.provider, snapshot.source_path, snapshot.source_hash,
                ),
            )
            for member in snapshot.members:
                connection.execute(
                    "INSERT OR IGNORE INTO universe_members "
                    "(universe_id, company_id, ticker, company_name, sector, industry, "
                    "membership_status, identity_status) VALUES (?, ?, ?, ?, ?, ?, 'active', 'resolved')",
                    (
                        snapshot.universe_id, member.company_id, member.ticker,
                        member.company_name, member.sector, member.industry,
                    ),
                )
    except (sqlite3.Error, WeeklyStorageError) as exc:
        raise UniverseError(f"Could not store pilot universe: {exc}") from exc


__all__ = [
    "UniverseError", "UniverseMember", "UniverseSnapshot", "load_pilot_universe",
    "store_universe_snapshot",
]

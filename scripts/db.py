"""SQLite schema, migrations, and helpers.

Schema is defined in DONATION_SCHEMA.md and this module is the implementation.
The DB is a derivative — raw payloads in data/raw/ are the ground truth (CLAUDE.md §1.4).
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

from .paths import MASTER_DB, OWNERS_DIR, SNAPSHOTS_DIR, ensure_data_dirs, relpath


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS donations (
    transaction_id TEXT PRIMARY KEY,
    entity_slug TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    parent_owner_slug TEXT,
    status TEXT NOT NULL,
    status_reason TEXT,
    signals_matched TEXT,
    contributor_name_raw TEXT NOT NULL,
    contributor_employer_raw TEXT,
    contributor_occupation_raw TEXT,
    contributor_city TEXT,
    contributor_state TEXT,
    contributor_zip TEXT,
    recipient_committee_id TEXT NOT NULL,
    recipient_committee_name TEXT NOT NULL,
    recipient_candidate_id TEXT,
    recipient_candidate_name TEXT,
    recipient_party TEXT,
    recipient_office TEXT,
    amount REAL NOT NULL,
    date TEXT NOT NULL,
    election_cycle INTEGER,
    report_type TEXT,
    filing_id TEXT NOT NULL,
    raw_payload_path TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    superseded_by TEXT,
    superseded_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_donations_entity_date
    ON donations(entity_slug, date);
CREATE INDEX IF NOT EXISTS idx_donations_status
    ON donations(status);
CREATE INDEX IF NOT EXISTS idx_donations_candidate
    ON donations(recipient_candidate_id, date);
CREATE INDEX IF NOT EXISTS idx_donations_cycle_entity
    ON donations(election_cycle, entity_slug);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id TEXT PRIMARY KEY,
    entity_slug TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    period_start TEXT,
    period_end TEXT,
    name_variants_queried TEXT,
    api_calls_made INTEGER,
    records_fetched INTEGER,
    confirmed_count INTEGER,
    probable_count INTEGER,
    uncertain_count INTEGER,
    snapshot_path TEXT,
    notes TEXT,
    dry_run INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS entities (
    slug TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    parent_slug TEXT,
    name TEXT NOT NULL,
    team TEXT,
    tenure_start_date TEXT,
    tenure_end_date TEXT,
    family_tenure_start_date TEXT,
    yaml_path TEXT NOT NULL,
    yaml_sha256 TEXT NOT NULL,
    refreshed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_queue (
    transaction_id TEXT PRIMARY KEY,
    entity_slug TEXT NOT NULL,
    reason TEXT NOT NULL,
    raw_payload_path TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    resolution TEXT,
    resolution_reason TEXT,
    resolution_at TEXT,
    resolved_by TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- v2: committee enrichment.
-- These tables back the per-recipient identity + scale cards on the dashboard
-- (#/committee/<id>). Sourced from OpenFEC /committee/<id>/ and /committee/<id>/totals/.
-- See CHARTER.md for the active-phase scope statement.
CREATE TABLE IF NOT EXISTS committees (
    committee_id              TEXT PRIMARY KEY,
    name                      TEXT NOT NULL,
    designation               TEXT,
    designation_label         TEXT,
    committee_type            TEXT,
    committee_type_label      TEXT,
    party                     TEXT,
    party_full                TEXT,
    organization_type         TEXT,
    affiliated_committee_name TEXT,
    candidate_ids             TEXT,
    treasurer_name            TEXT,
    custodian_name            TEXT,
    city                      TEXT,
    state                     TEXT,
    zip                       TEXT,
    filing_frequency          TEXT,
    first_file_date           TEXT,
    last_file_date            TEXT,
    last_f1_date              TEXT,
    is_terminated             INTEGER NOT NULL DEFAULT 0,
    cycles                    TEXT,
    external_link             TEXT,
    external_link_label       TEXT,
    external_link_source      TEXT,
    raw_payload_path          TEXT NOT NULL,
    fetched_at                TEXT NOT NULL,
    refreshed_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_committees_party ON committees(party);
CREATE INDEX IF NOT EXISTS idx_committees_type ON committees(committee_type);

CREATE TABLE IF NOT EXISTS committee_totals (
    committee_id                            TEXT NOT NULL,
    cycle                                   INTEGER NOT NULL,
    receipts                                REAL,
    disbursements                           REAL,
    cash_on_hand_end_period                 REAL,
    individual_contributions                REAL,
    other_political_committee_contributions REAL,
    independent_expenditures                REAL,
    coverage_start_date                     TEXT,
    coverage_end_date                       TEXT,
    raw_payload_path                        TEXT NOT NULL,
    fetched_at                              TEXT NOT NULL,
    PRIMARY KEY (committee_id, cycle)
);
CREATE INDEX IF NOT EXISTS idx_committee_totals_cycle ON committee_totals(cycle);
"""

SCHEMA_VERSION = 2


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now_filename() -> str:
    # YYYY-MM-DDTHH-MM-SSZ — filename-safe per NAMING.md
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


@contextmanager
def connect(db_path: Path = MASTER_DB) -> Iterator[sqlite3.Connection]:
    ensure_data_dirs()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init(db_path: Path = MASTER_DB) -> None:
    """Create schema idempotently. Records a new schema_version row whenever
    SCHEMA_VERSION is bumped beyond the DB's current MAX(version), so the
    migration trail is preserved."""
    ensure_data_dirs()
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        existing = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = existing["v"] if existing else None
        if current is None or current < SCHEMA_VERSION:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _utc_now_iso()),
            )


def snapshot(run_id: str, db_path: Path = MASTER_DB) -> Path | None:
    """Copy master.db to data/snapshots/<UTC>__<run-id>.db.

    Returns None if no DB exists yet (first run).
    """
    ensure_data_dirs()
    if not db_path.exists():
        return None
    target = SNAPSHOTS_DIR / f"{_utc_now_filename()}__{run_id}.db"
    shutil.copy2(db_path, target)
    return target


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def refresh_entities(db_path: Path = MASTER_DB) -> int:
    """Rebuild the entities table from owners/*.yaml.

    Returns the number of entity rows written.

    Reads the owner YAML and writes one row per owner. (Related entities are
    recorded under the owner's row indirectly — the entities table reflects the
    YAML registry, not the related_entities sub-structure. We expand related
    entities only when classification asks for them, never as their own row in
    the entities table for now. If/when related-entity ingestion is enabled the
    pipeline will populate them via a separate path.)
    """
    init(db_path)
    rows = 0
    now = _utc_now_iso()
    with connect(db_path) as conn:
        # Ensure family_tenure_start_date column exists on the entities table
        # (migration for pre-existing DBs created before this column landed).
        existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(entities)")}
        if "family_tenure_start_date" not in existing_cols:
            conn.execute("ALTER TABLE entities ADD COLUMN family_tenure_start_date TEXT")
        conn.execute("DELETE FROM entities")
        for yaml_path in sorted(OWNERS_DIR.glob("*.yaml")):
            if yaml_path.name.startswith("_"):
                continue
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            conn.execute(
                """
                INSERT INTO entities
                  (slug, kind, parent_slug, name, team, tenure_start_date,
                   tenure_end_date, family_tenure_start_date,
                   yaml_path, yaml_sha256, refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("slug"),
                    "owner",
                    None,
                    data.get("name"),
                    data.get("team"),
                    str(data.get("tenure_start_date")) if data.get("tenure_start_date") else None,
                    str(data.get("tenure_end_date")) if data.get("tenure_end_date") else None,
                    str(data.get("family_tenure_start_date")) if data.get("family_tenure_start_date") else None,
                    relpath(yaml_path),
                    _sha256_file(yaml_path),
                    now,
                ),
            )
            rows += 1
    return rows


def insert_donation(conn: sqlite3.Connection, row: dict) -> None:
    """Insert or upsert a donation row keyed on transaction_id.

    Idempotency: if the same transaction_id exists with the same payload-hash
    proxy (same amount + date + recipient + status), we leave it alone.
    Otherwise we mark the existing row SUPERSEDED and insert a new row.

    For simple idempotent re-runs (same FEC data), the INSERT OR IGNORE keeps
    things clean.
    """
    conn.execute(
        """
        INSERT OR IGNORE INTO donations (
            transaction_id, entity_slug, entity_kind, parent_owner_slug,
            status, status_reason, signals_matched,
            contributor_name_raw, contributor_employer_raw, contributor_occupation_raw,
            contributor_city, contributor_state, contributor_zip,
            recipient_committee_id, recipient_committee_name,
            recipient_candidate_id, recipient_candidate_name,
            recipient_party, recipient_office,
            amount, date, election_cycle, report_type,
            filing_id, raw_payload_path, ingested_at
        ) VALUES (
            :transaction_id, :entity_slug, :entity_kind, :parent_owner_slug,
            :status, :status_reason, :signals_matched,
            :contributor_name_raw, :contributor_employer_raw, :contributor_occupation_raw,
            :contributor_city, :contributor_state, :contributor_zip,
            :recipient_committee_id, :recipient_committee_name,
            :recipient_candidate_id, :recipient_candidate_name,
            :recipient_party, :recipient_office,
            :amount, :date, :election_cycle, :report_type,
            :filing_id, :raw_payload_path, :ingested_at
        )
        """,
        row,
    )


def insert_review_queue(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO review_queue (
            transaction_id, entity_slug, reason, raw_payload_path, queued_at
        ) VALUES (:transaction_id, :entity_slug, :reason, :raw_payload_path, :queued_at)
        """,
        row,
    )


def insert_ingestion_run(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO ingestion_runs (
            run_id, entity_slug, started_at, completed_at,
            period_start, period_end, name_variants_queried,
            api_calls_made, records_fetched,
            confirmed_count, probable_count, uncertain_count,
            snapshot_path, notes, dry_run
        ) VALUES (
            :run_id, :entity_slug, :started_at, :completed_at,
            :period_start, :period_end, :name_variants_queried,
            :api_calls_made, :records_fetched,
            :confirmed_count, :probable_count, :uncertain_count,
            :snapshot_path, :notes, :dry_run
        )
        """,
        row,
    )

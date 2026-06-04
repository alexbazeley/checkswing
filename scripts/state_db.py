"""SQLite schema + helpers for the Phase 4 state campaign-finance index (data/state.db).

This is a SEPARATE database from master.db (federal/FEC) and from legislation.db
(Phase 3). It holds owner-MATCHED state campaign-finance contributions, classified
under the SAME three-tier verification standard as the federal data, plus the
recipient filers (state candidates/committees) those contributions point at.

The schema is defined here and documented in STATE_DONATION_SCHEMA.md. The DB is a
derivative — the persisted state-portal bulk extracts in data/raw/state/ are the
ground truth (GOVERNANCE.md §1.4).

Design rules this module encodes:

  * **Same verification bar (GOVERNANCE.md §1.1–1.2).** A name match is necessary
    but not sufficient: a record is CONFIRMED only with two confirming signals
    (or one strong signal), PROBABLE on one, UNCERTAIN otherwise. The classifier
    (`scripts/resolve_entities.py`) is reused verbatim — only the input adapter
    differs by source.
  * **Hybrid sourcing (CHARTER.md §Phase 4, GOVERNANCE.md §3).** The official
    state portal is the primary source (`source`, e.g. "CAL-ACCESS"); an aggregator
    may only DISCOVER candidates (`discovery_source`, e.g. "TAP"), never stand in
    as the record. A CONFIRMED/PROBABLE row always traces to an official filing
    (`source_filing_id` + `raw_payload_path`).
  * **Separate, non-LFS, small.** master.db is untouched. Like legislation.db this
    DB is committed as a normal git blob (NOT in .gitattributes LFS): it stores
    only owner-matched rows + their recipient filers, a few MB even at a full
    multi-state buildout, so a state-data commit never re-pushes master.db's
    ~124 MB LFS object.

The schema mirrors scripts/db.py's conventions: a single SCHEMA_SQL executescript,
an idempotent init(), a state_schema_version migration trail, a snapshot() guard
for gated mutations, and the same review_resolutions / manual_attributions
durability model (verdicts survive reclassify).
"""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

from .paths import OWNERS_DIR, SNAPSHOTS_DIR, STATE_DB, ensure_data_dirs, relpath


SCHEMA_SQL = """
-- ── State contributions (owner-matched) ─────────────────────────────────────
-- One row per matched state contribution. state_txn_id is a composed, stable key
-- "{jurisdiction}:{source}:{source_filing_id}:{source_tran_id}" so re-ingesting
-- the same portal extract is idempotent and amendments supersede cleanly. Columns
-- parallel master.db's donations table, plus the state/source provenance fields.
CREATE TABLE IF NOT EXISTS state_donations (
    state_txn_id              TEXT PRIMARY KEY,
    jurisdiction              TEXT NOT NULL,      -- USPS state code, e.g. 'CA'
    source                    TEXT NOT NULL,      -- official portal, e.g. 'CAL-ACCESS'
    source_tran_id            TEXT,               -- portal's per-item id (CAL-ACCESS TRAN_ID)
    source_filing_id          TEXT,               -- portal's filing id (CAL-ACCESS FILING_ID)
    discovery_source          TEXT,               -- aggregator that surfaced it (TAP/FTM) or NULL for direct scan

    entity_slug               TEXT NOT NULL,
    entity_kind               TEXT NOT NULL,
    parent_owner_slug         TEXT,

    status                    TEXT NOT NULL,      -- CONFIRMED / PROBABLE / UNCERTAIN / SUPERSEDED
    status_reason             TEXT,
    signals_matched           TEXT,               -- JSON array

    contributor_name_raw      TEXT NOT NULL,
    contributor_employer_raw  TEXT,
    contributor_occupation_raw TEXT,
    contributor_city          TEXT,
    contributor_state         TEXT,
    contributor_zip           TEXT,

    recipient_filer_id        TEXT,               -- → state_filers.filer_id
    recipient_name            TEXT NOT NULL,      -- committee/candidate name as filed
    recipient_type            TEXT,               -- 'candidate' / 'committee' / 'ballot_measure' / NULL
    recipient_party           TEXT,               -- often NULL at state level
    recipient_office          TEXT,               -- often NULL at state level

    amount                    REAL NOT NULL,
    date                      TEXT NOT NULL,      -- ISO 8601
    election_cycle            INTEGER,            -- calendar year of the contribution (state cycles vary; see schema doc)
    report_type               TEXT,

    raw_payload_path          TEXT NOT NULL,
    ingested_at               TEXT NOT NULL,
    superseded_by             TEXT,
    superseded_reason         TEXT
);
CREATE INDEX IF NOT EXISTS idx_state_donations_entity_date
    ON state_donations(entity_slug, date);
CREATE INDEX IF NOT EXISTS idx_state_donations_status
    ON state_donations(status);
CREATE INDEX IF NOT EXISTS idx_state_donations_jurisdiction
    ON state_donations(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_state_donations_filer
    ON state_donations(recipient_filer_id);

-- ── Recipient filers (state candidates / committees / ballot-measure cmtes) ──
-- The state-level analog of master.db's committees table. Enriched from the
-- portal's filer/cover-page lookup so a contribution can name who it went to.
CREATE TABLE IF NOT EXISTS state_filers (
    filer_id          TEXT NOT NULL,
    jurisdiction      TEXT NOT NULL,
    source            TEXT NOT NULL,
    name              TEXT NOT NULL,
    filer_type        TEXT,               -- 'candidate' / 'committee' / 'ballot_measure' / NULL
    party             TEXT,
    office            TEXT,
    raw_payload_path  TEXT,
    fetched_at        TEXT,
    refreshed_at      TEXT NOT NULL,
    PRIMARY KEY (jurisdiction, source, filer_id)
);

-- ── Ingestion run log ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS state_ingestion_runs (
    run_id            TEXT PRIMARY KEY,
    entity_slug       TEXT NOT NULL,
    jurisdiction      TEXT NOT NULL,
    source            TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    completed_at      TEXT,
    extract_label     TEXT,               -- which portal extract (e.g. CCDC release date)
    name_variants_queried TEXT,
    records_scanned   INTEGER,
    confirmed_count   INTEGER,
    probable_count    INTEGER,
    uncertain_count   INTEGER,
    snapshot_path     TEXT,
    notes             TEXT,
    dry_run           INTEGER NOT NULL DEFAULT 0
);

-- ── Review queue (UNCERTAIN awaiting adjudication) ──────────────────────────
-- Mirrors master.db's review_queue, plus a `source` discriminator. The
-- aggregator-only reconciliation (discover_state) lands here with reason
-- 'aggregator-only — verify against <portal>' until found in the official bulk.
CREATE TABLE IF NOT EXISTS state_review_queue (
    state_txn_id      TEXT PRIMARY KEY,
    entity_slug       TEXT NOT NULL,
    jurisdiction      TEXT NOT NULL,
    source            TEXT NOT NULL,
    reason            TEXT NOT NULL,
    raw_payload_path  TEXT NOT NULL,
    queued_at         TEXT NOT NULL,
    resolution        TEXT,
    resolution_reason TEXT,
    resolution_at     TEXT,
    resolved_by       TEXT
);

-- Durable verdicts that survive reclassify (same model as master.db).
CREATE TABLE IF NOT EXISTS state_review_resolutions (
    state_txn_id      TEXT NOT NULL,
    entity_slug       TEXT NOT NULL,
    resolution        TEXT NOT NULL,      -- e.g. DISCARDED
    resolution_reason TEXT,
    resolved_at       TEXT NOT NULL,
    resolved_by       TEXT,
    PRIMARY KEY (state_txn_id, entity_slug)
);
CREATE INDEX IF NOT EXISTS idx_state_review_resolutions_slug
    ON state_review_resolutions(entity_slug);

CREATE TABLE IF NOT EXISTS state_manual_attributions (
    state_txn_id      TEXT NOT NULL,
    entity_slug       TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'CONFIRMED',  -- CONFIRMED / PROBABLE / EXCLUDED
    reason            TEXT,
    source            TEXT,
    attributed_at     TEXT NOT NULL,
    attributed_by     TEXT,
    PRIMARY KEY (state_txn_id, entity_slug)
);
CREATE INDEX IF NOT EXISTS idx_state_manual_attributions_slug
    ON state_manual_attributions(entity_slug);

CREATE TABLE IF NOT EXISTS state_schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

STATE_SCHEMA_VERSION = 1

# State-portal-sourced fields that define the substance of a contribution. A
# change in any of these across re-ingests means the portal restated the item
# (amended amount, corrected recipient/filing), which triggers supersession.
# Derived columns (status, signals_matched) are excluded so a reclassification
# never looks like a restatement — mirrors db.DONATION_SUBSTANCE_COLS.
STATE_DONATION_SUBSTANCE_COLS = (
    "amount",
    "date",
    "recipient_filer_id",
    "source_filing_id",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


@contextmanager
def connect(db_path: Path = STATE_DB) -> Iterator[sqlite3.Connection]:
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


def init(db_path: Path = STATE_DB) -> None:
    """Create the state schema idempotently and record the version row."""
    ensure_data_dirs()
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        existing = conn.execute(
            "SELECT MAX(version) AS v FROM state_schema_version"
        ).fetchone()
        current = existing["v"] if existing else None
        if current is None or current < STATE_SCHEMA_VERSION:
            conn.execute(
                "INSERT OR IGNORE INTO state_schema_version (version, applied_at) VALUES (?, ?)",
                (STATE_SCHEMA_VERSION, _utc_now_iso()),
            )


def snapshot(run_id: str, db_path: Path = STATE_DB) -> Path | None:
    """Copy state.db to data/snapshots/<UTC>__<run-id>.db before a gated
    mutation. Returns None if the DB does not exist yet (first run).
    """
    ensure_data_dirs()
    if not db_path.exists():
        return None
    target = SNAPSHOTS_DIR / f"{_utc_now_filename()}__{run_id}.db"
    shutil.copy2(db_path, target)
    return target


def compose_state_txn_id(
    *, jurisdiction: str, source: str, source_filing_id: str | None, source_tran_id: str | None
) -> str:
    """Stable composed primary key for a state contribution.

    Combines jurisdiction + source + filing + item id. CAL-ACCESS TRAN_ID is
    unique per item but only within a filing, so filing_id is part of the key.
    Missing parts collapse to empty segments (kept positional for stability).
    """
    return ":".join(
        [
            (jurisdiction or "").upper(),
            (source or ""),
            (source_filing_id or ""),
            (source_tran_id or ""),
        ]
    )


def _state_values_equal(a, b) -> bool:
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        try:
            return round(float(a or 0), 2) == round(float(b or 0), 2)
        except (TypeError, ValueError):
            pass
    return (str(a) if a is not None else "") == (str(b) if b is not None else "")


_STATE_DONATION_COLS = (
    "state_txn_id, jurisdiction, source, source_tran_id, source_filing_id, discovery_source, "
    "entity_slug, entity_kind, parent_owner_slug, status, status_reason, signals_matched, "
    "contributor_name_raw, contributor_employer_raw, contributor_occupation_raw, "
    "contributor_city, contributor_state, contributor_zip, "
    "recipient_filer_id, recipient_name, recipient_type, recipient_party, recipient_office, "
    "amount, date, election_cycle, report_type, raw_payload_path, ingested_at"
)


def _insert_state_donation_row(conn: sqlite3.Connection, row: dict) -> None:
    cols = [c.strip() for c in _STATE_DONATION_COLS.split(",")]
    placeholders = ", ".join(f":{c}" for c in cols)
    full = {c: row.get(c) for c in cols}
    conn.execute(
        f"INSERT INTO state_donations ({_STATE_DONATION_COLS}) VALUES ({placeholders})",
        full,
    )


def insert_state_donation(conn: sqlite3.Connection, row: dict) -> tuple[str, str | None]:
    """Insert, dedup, or supersede a state contribution keyed on state_txn_id.

    Returns (action, reason) with the same semantics as db.insert_donation:
      - ("inserted", None)
      - ("unchanged", None)           — idempotent re-ingest, identical substance
      - ("superseded", <reason>)      — portal restated the item; old row archived
                                        under a derived key with status='SUPERSEDED'.
    Supersession compares only STATE_DONATION_SUBSTANCE_COLS.
    """
    txn = row["state_txn_id"]
    existing = conn.execute(
        "SELECT * FROM state_donations WHERE state_txn_id = ?", (txn,)
    ).fetchone()
    if existing is None:
        _insert_state_donation_row(conn, row)
        return ("inserted", None)

    existing_d = dict(existing)
    changed = [
        f
        for f in STATE_DONATION_SUBSTANCE_COLS
        if not _state_values_equal(existing_d.get(f), row.get(f))
    ]
    if not changed:
        return ("unchanged", None)

    reason = f"portal restatement: {', '.join(changed)}"
    archived_key = f"{txn}~superseded~{_utc_now_filename()}"
    conn.execute(
        """
        UPDATE state_donations
           SET state_txn_id = ?, status = ?, superseded_by = ?, superseded_reason = ?
         WHERE state_txn_id = ?
        """,
        (archived_key, "SUPERSEDED", txn, reason, txn),
    )
    _insert_state_donation_row(conn, row)
    return ("superseded", reason)


def upsert_state_filer(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO state_filers (
            filer_id, jurisdiction, source, name, filer_type, party, office,
            raw_payload_path, fetched_at, refreshed_at
        ) VALUES (
            :filer_id, :jurisdiction, :source, :name, :filer_type, :party, :office,
            :raw_payload_path, :fetched_at, :refreshed_at
        )
        ON CONFLICT(jurisdiction, source, filer_id) DO UPDATE SET
            name = excluded.name,
            filer_type = excluded.filer_type,
            party = excluded.party,
            office = excluded.office,
            raw_payload_path = excluded.raw_payload_path,
            refreshed_at = excluded.refreshed_at
        """,
        row,
    )


def insert_state_review_queue(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO state_review_queue (
            state_txn_id, entity_slug, jurisdiction, source, reason,
            raw_payload_path, queued_at
        ) VALUES (
            :state_txn_id, :entity_slug, :jurisdiction, :source, :reason,
            :raw_payload_path, :queued_at
        )
        """,
        row,
    )


def upsert_state_review_resolution(
    conn: sqlite3.Connection,
    *,
    state_txn_id: str,
    entity_slug: str,
    resolution: str,
    resolution_reason: str | None,
    resolved_at: str,
    resolved_by: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO state_review_resolutions (
            state_txn_id, entity_slug, resolution, resolution_reason,
            resolved_at, resolved_by
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(state_txn_id, entity_slug) DO UPDATE SET
            resolution = excluded.resolution,
            resolution_reason = excluded.resolution_reason,
            resolved_at = excluded.resolved_at,
            resolved_by = excluded.resolved_by
        """,
        (state_txn_id, entity_slug, resolution, resolution_reason, resolved_at, resolved_by),
    )


def discarded_txns_for_slug(conn: sqlite3.Connection, entity_slug: str) -> set[str]:
    return {
        r["state_txn_id"]
        for r in conn.execute(
            "SELECT state_txn_id FROM state_review_resolutions "
            "WHERE entity_slug = ? AND resolution = 'DISCARDED'",
            (entity_slug,),
        )
    }


def upsert_state_manual_attribution(
    conn: sqlite3.Connection,
    *,
    state_txn_id: str,
    entity_slug: str,
    status: str = "CONFIRMED",
    reason: str | None,
    source: str | None,
    attributed_at: str,
    attributed_by: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO state_manual_attributions (
            state_txn_id, entity_slug, status, reason, source,
            attributed_at, attributed_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(state_txn_id, entity_slug) DO UPDATE SET
            status = excluded.status,
            reason = excluded.reason,
            source = excluded.source,
            attributed_at = excluded.attributed_at,
            attributed_by = excluded.attributed_by
        """,
        (state_txn_id, entity_slug, status, reason, source, attributed_at, attributed_by),
    )


def state_manual_attributions_for_slug(
    conn: sqlite3.Connection, entity_slug: str
) -> dict[str, str]:
    return {
        r["state_txn_id"]: r["status"]
        for r in conn.execute(
            "SELECT state_txn_id, status FROM state_manual_attributions WHERE entity_slug = ?",
            (entity_slug,),
        )
    }


def insert_state_ingestion_run(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO state_ingestion_runs (
            run_id, entity_slug, jurisdiction, source, started_at, completed_at,
            extract_label, name_variants_queried, records_scanned,
            confirmed_count, probable_count, uncertain_count,
            snapshot_path, notes, dry_run
        ) VALUES (
            :run_id, :entity_slug, :jurisdiction, :source, :started_at, :completed_at,
            :extract_label, :name_variants_queried, :records_scanned,
            :confirmed_count, :probable_count, :uncertain_count,
            :snapshot_path, :notes, :dry_run
        )
        """,
        row,
    )


def delete_donations_for_slug(conn: sqlite3.Connection, entity_slug: str) -> int:
    """Delete this entity's state_donations + open review-queue rows (for reclassify).

    Durable verdicts (state_review_resolutions, state_manual_attributions) are NOT
    touched — they survive reclassify by design. Returns donation rows deleted.
    """
    cur = conn.execute("DELETE FROM state_donations WHERE entity_slug = ?", (entity_slug,))
    conn.execute("DELETE FROM state_review_queue WHERE entity_slug = ?", (entity_slug,))
    return cur.rowcount

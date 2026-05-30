"""SQLite schema, migrations, and helpers.

Schema is defined in DONATION_SCHEMA.md and this module is the implementation.
The DB is a derivative — raw payloads in data/raw/ are the ground truth (GOVERNANCE.md §1.4).
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

-- v6: standing review-queue resolutions, keyed by (transaction_id, entity_slug).
-- The review_queue table itself is rebuilt from raw on every reclassify (it is a
-- derived projection of the current classifier output), so a resolution stored
-- *there* is lost on the next reclassify (audit finding M6). This table is the
-- durable record of a human verdict and is NEVER wiped by reclassify. A
-- DISCARDED verdict suppresses the transaction from re-entering review_queue on
-- future ingests/reclassifies (GOVERNANCE.md §2.5). It does NOT affect
-- attribution: if a later signal change makes the donor CONFIRMED/PROBABLE, the
-- record is attributed normally — discard only governs the UNCERTAIN queue.
CREATE TABLE IF NOT EXISTS review_resolutions (
    transaction_id    TEXT NOT NULL,
    entity_slug       TEXT NOT NULL,
    resolution        TEXT NOT NULL,   -- e.g. DISCARDED
    resolution_reason TEXT,
    resolved_at       TEXT NOT NULL,
    resolved_by       TEXT,
    PRIMARY KEY (transaction_id, entity_slug)
);
CREATE INDEX IF NOT EXISTS idx_review_resolutions_slug
    ON review_resolutions(entity_slug);

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

-- v4: per-filing metadata for the donation card's "Full filing PDF" link.
-- Sourced from OpenFEC /v1/filings/?file_number=<id>. The real PDF lives at
-- pdf_url; the older HTML fec.gov page link (filing_page_url) stays as the
-- fallback for filings we haven't enriched yet (e.g. ancient records FEC's
-- batch endpoint doesn't return).
CREATE TABLE IF NOT EXISTS filings (
    file_number              TEXT PRIMARY KEY,
    pdf_url                  TEXT,
    form_type                TEXT,
    document_type            TEXT,
    document_type_full       TEXT,
    filed_date               TEXT,
    receipt_date             TEXT,
    coverage_start_date      TEXT,
    coverage_end_date        TEXT,
    committee_id             TEXT,
    committee_name           TEXT,
    is_amended               INTEGER NOT NULL DEFAULT 0,
    amendment_chain          TEXT,
    cycle                    INTEGER,
    raw_payload_path         TEXT NOT NULL,
    fetched_at               TEXT NOT NULL,
    refreshed_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_filings_committee ON filings(committee_id);

-- v5: per-committee, per-cycle beneficiaries — "who did this committee fund".
-- Sourced from OpenFEC /schedules/schedule_b/by_recipient/?committee_id=<id>.
-- Each row is one recipient (a candidate or another committee) and the total
-- the spending committee disbursed to them in that cycle. Schedule B aggregates
-- transactions at the recipient level, so n_transactions is FEC's count, not
-- a join we compute. GOVERNANCE.md §6: names and amounts only; no editorial
-- linkage to legislation or policy outcomes (Phase 3 if ever).
CREATE TABLE IF NOT EXISTS committee_disbursements_by_recipient (
    committee_id      TEXT NOT NULL,
    cycle             INTEGER NOT NULL,
    recipient_id      TEXT NOT NULL,
    recipient_kind    TEXT NOT NULL,
    recipient_name    TEXT,
    recipient_party   TEXT,
    recipient_office  TEXT,
    total_amount      REAL NOT NULL,
    n_transactions    INTEGER,
    raw_payload_path  TEXT NOT NULL,
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (committee_id, cycle, recipient_id, recipient_kind)
);
CREATE INDEX IF NOT EXISTS idx_cdbr_committee_cycle
    ON committee_disbursements_by_recipient(committee_id, cycle);
"""

# v3 adds six per-transaction FEC fields (image_number, pdf_url, filing_form,
# line_number, receipt_type_full, recipient_committee_type) to the donations
# table. These used to be looked up from raw payloads at build_data.py time,
# which broke whenever raw payloads were inaccessible (e.g., a GHA matrix
# refresh writes raw payloads to an ephemeral runner that's then destroyed).
# Now they're baked onto each row at ingest time. The columns are added via
# ALTER TABLE in init() — CREATE TABLE IF NOT EXISTS doesn't add columns to
# existing tables. The list is also kept in DONATION_EXTRA_COLS for use by
# the migration runner and the insert helper.
DONATION_EXTRA_COLS: list[tuple[str, str]] = [
    ("image_number", "TEXT"),
    ("pdf_url", "TEXT"),
    ("filing_form", "TEXT"),
    ("line_number", "TEXT"),
    ("receipt_type_full", "TEXT"),
    ("recipient_committee_type", "TEXT"),
]

SCHEMA_VERSION = 6


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
    migration trail is preserved.

    Column additions (ALTER TABLE) live alongside the CREATE statements
    because SQLite's `CREATE TABLE IF NOT EXISTS` won't add columns to a
    pre-existing table. PRAGMA table_info gates each ADD COLUMN so the
    migration is idempotent.
    """
    ensure_data_dirs()
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        # v3: per-transaction FEC fields on donations
        existing_donation_cols = {r["name"] for r in conn.execute("PRAGMA table_info(donations)")}
        for col_name, col_type in DONATION_EXTRA_COLS:
            if col_name not in existing_donation_cols:
                conn.execute(f"ALTER TABLE donations ADD COLUMN {col_name} {col_type}")
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


# FEC-sourced fields that define the substance of a contribution. A change in
# any of these across re-fetches means FEC restated the transaction (amended
# amount, corrected recipient, re-filed under a new file number, etc.) and
# triggers supersession. Our DERIVED columns (status, signals_matched) are
# deliberately excluded so a reclassification never looks like a restatement.
DONATION_SUBSTANCE_COLS = (
    "amount",
    "date",
    "recipient_committee_id",
    "recipient_candidate_id",
    "filing_id",
    "image_number",
)


def _donation_values_equal(a, b) -> bool:
    """Substance-equality for one donation field (stored value vs incoming).

    Amounts are compared to the cent (REAL round-trip vs float of the incoming
    value); everything else is compared as strings with None treated as "".
    """
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        try:
            return round(float(a or 0), 2) == round(float(b or 0), 2)
        except (TypeError, ValueError):
            pass
    return (str(a) if a is not None else "") == (str(b) if b is not None else "")


def _insert_donation_row(conn: sqlite3.Connection, full_row: dict) -> None:
    conn.execute(
        """
        INSERT INTO donations (
            transaction_id, entity_slug, entity_kind, parent_owner_slug,
            status, status_reason, signals_matched,
            contributor_name_raw, contributor_employer_raw, contributor_occupation_raw,
            contributor_city, contributor_state, contributor_zip,
            recipient_committee_id, recipient_committee_name,
            recipient_candidate_id, recipient_candidate_name,
            recipient_party, recipient_office,
            amount, date, election_cycle, report_type,
            filing_id, raw_payload_path, ingested_at,
            image_number, pdf_url, filing_form, line_number,
            receipt_type_full, recipient_committee_type
        ) VALUES (
            :transaction_id, :entity_slug, :entity_kind, :parent_owner_slug,
            :status, :status_reason, :signals_matched,
            :contributor_name_raw, :contributor_employer_raw, :contributor_occupation_raw,
            :contributor_city, :contributor_state, :contributor_zip,
            :recipient_committee_id, :recipient_committee_name,
            :recipient_candidate_id, :recipient_candidate_name,
            :recipient_party, :recipient_office,
            :amount, :date, :election_cycle, :report_type,
            :filing_id, :raw_payload_path, :ingested_at,
            :image_number, :pdf_url, :filing_form, :line_number,
            :receipt_type_full, :recipient_committee_type
        )
        """,
        full_row,
    )


def insert_donation(conn: sqlite3.Connection, row: dict) -> tuple[str, str | None]:
    """Insert, dedup, or supersede a donation row keyed on transaction_id.

    Returns (action, reason):
      - ("inserted", None)   — no prior row; the payload was inserted.
      - ("unchanged", None)  — a row with identical FEC substance already
                               exists; idempotent re-fetch, left alone (§1.5).
      - ("superseded", <reason>) — a live row existed whose FEC substance
                               differs (FEC restated the transaction). The old
                               row is archived under a derived transaction_id
                               with status='SUPERSEDED' and superseded_by set to
                               the canonical id; the restated payload is then
                               inserted under the canonical id. The old row is
                               never deleted (§1.10), and citations to
                               transaction_id resolve to the current version.

    Supersession compares only DONATION_SUBSTANCE_COLS (FEC-sourced fields), so
    a future reclassification — which changes our derived status/signals but not
    FEC substance — does not spuriously trip it.
    """
    # Fill in the v3 per-transaction FEC fields with None if the caller didn't
    # provide them. The columns are nullable; missing data shows "Image link not
    # available" on the donation card, which is the honest fallback.
    payload = {col: row.get(col) for col, _ in DONATION_EXTRA_COLS}
    full_row = {**row, **payload}
    txn = full_row["transaction_id"]

    existing = conn.execute(
        "SELECT * FROM donations WHERE transaction_id = ?", (txn,)
    ).fetchone()
    if existing is None:
        _insert_donation_row(conn, full_row)
        return ("inserted", None)

    existing_d = dict(existing)
    changed = [
        f
        for f in DONATION_SUBSTANCE_COLS
        if not _donation_values_equal(existing_d.get(f), full_row.get(f))
    ]
    if not changed:
        return ("unchanged", None)

    # FEC restated this transaction — archive the old row, insert the new one.
    reason = f"FEC restatement: {', '.join(changed)}"
    archived_key = f"{txn}~superseded~{_utc_now_filename()}"
    conn.execute(
        """
        UPDATE donations
           SET transaction_id = ?, status = ?, superseded_by = ?, superseded_reason = ?
         WHERE transaction_id = ?
        """,
        (archived_key, "SUPERSEDED", txn, reason, txn),
    )
    _insert_donation_row(conn, full_row)
    return ("superseded", reason)


def insert_review_queue(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO review_queue (
            transaction_id, entity_slug, reason, raw_payload_path, queued_at
        ) VALUES (:transaction_id, :entity_slug, :reason, :raw_payload_path, :queued_at)
        """,
        row,
    )


def upsert_review_resolution(
    conn: sqlite3.Connection,
    *,
    transaction_id: str,
    entity_slug: str,
    resolution: str,
    resolution_reason: str | None,
    resolved_at: str,
    resolved_by: str | None = None,
) -> None:
    """Record (or overwrite) a standing resolution for one queue item.

    Keyed by (transaction_id, entity_slug). Survives reclassify — this is the
    durable verdict store, distinct from the rebuilt review_queue table.
    """
    conn.execute(
        """
        INSERT INTO review_resolutions (
            transaction_id, entity_slug, resolution, resolution_reason,
            resolved_at, resolved_by
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(transaction_id, entity_slug) DO UPDATE SET
            resolution = excluded.resolution,
            resolution_reason = excluded.resolution_reason,
            resolved_at = excluded.resolved_at,
            resolved_by = excluded.resolved_by
        """,
        (transaction_id, entity_slug, resolution, resolution_reason, resolved_at, resolved_by),
    )


def delete_review_resolution(
    conn: sqlite3.Connection, *, transaction_id: str, entity_slug: str
) -> int:
    """Remove a standing resolution (undo). Returns rows deleted (0 or 1)."""
    cur = conn.execute(
        "DELETE FROM review_resolutions WHERE transaction_id = ? AND entity_slug = ?",
        (transaction_id, entity_slug),
    )
    return cur.rowcount


def discarded_txns_for_slug(conn: sqlite3.Connection, entity_slug: str) -> set[str]:
    """Set of transaction_ids with a standing DISCARDED verdict for this entity.

    Used at ingest time to suppress these from re-entering review_queue.
    """
    return {
        r["transaction_id"]
        for r in conn.execute(
            "SELECT transaction_id FROM review_resolutions "
            "WHERE entity_slug = ? AND resolution = 'DISCARDED'",
            (entity_slug,),
        )
    }


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

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
-- v12: PK is (record_uid, entity_slug), where record_uid = COALESCE(sub_id,
-- transaction_id). FEC transaction ids are FILER-assigned and unique only within
-- a single filing, so they cannot identify a row at all:
--   * ACROSS owners — two owners' contributions can share one id. Fixed in v11
--     by adding entity_slug to the PK. Before that, and before the §1.3 guard,
--     the second was recorded as a fabricated "FEC restatement" that silently
--     removed the first owner's donation from every total.
--   * WITHIN one owner — the SAME owner can have two real contributions sharing
--     an id (observed: same-day pairs to a campaign and its compliance fund).
--     v11 could not represent this; v12 keys on FEC's globally-unique `sub_id`,
--     falling back to transaction_id only for older records that carry none.
-- transaction_id remains as the citation field and the join key used by
-- review_queue / review_resolutions / manual_attributions; it is indexed.
CREATE TABLE IF NOT EXISTS donations (
    transaction_id TEXT NOT NULL,
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
    superseded_reason TEXT,
    sub_id TEXT,
    -- Deliberately not NOT NULL. SQLite permits NULL in a non-INTEGER PRIMARY
    -- KEY column, and test fixtures that insert donation rows by raw SQL do not
    -- care about identity. Every production write goes through
    -- _insert_donation_row, which always populates it, and `cli validate`
    -- asserts that no live row is missing one — so the guarantee is enforced
    -- where it matters without forcing every fixture to carry the column.
    record_uid TEXT,
    PRIMARY KEY (record_uid, entity_slug)
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

-- v9: PK is (transaction_id, entity_slug), not transaction_id alone — two owners
-- can each flag the same FEC transaction as UNCERTAIN (same-named donors), and a
-- single-column PK made INSERT OR IGNORE silently drop the second owner's item.
-- Now consistent with review_resolutions / manual_attributions. Existing DBs are
-- migrated in init() (table rebuild, data preserved).
CREATE TABLE IF NOT EXISTS review_queue (
    transaction_id TEXT NOT NULL,
    entity_slug TEXT NOT NULL,
    reason TEXT NOT NULL,
    raw_payload_path TEXT NOT NULL,
    queued_at TEXT NOT NULL,
    resolution TEXT,
    resolution_reason TEXT,
    resolution_at TEXT,
    resolved_by TEXT,
    PRIMARY KEY (transaction_id, entity_slug)
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

-- v7: manual attribution overrides, keyed by (transaction_id, entity_slug).
-- The positive counterpart to review_resolutions: a documented human judgment
-- that a specific transaction belongs to an owner even though the automated
-- classifier could not confirm it (e.g. a record misfiled with the wrong
-- generational suffix that no name_variant can safely capture without also
-- matching a same-named relative). Applied at ingest/reclassify time to force
-- the record to the recorded status, and NEVER wiped by reclassify. Every entry
-- carries a reason + source so the override is itself auditable (GOVERNANCE.md
-- §1.1, §2.5). Use sparingly and only with documented evidence — this bypasses
-- the two-signal rule by explicit human decision.
-- status values: 'CONFIRMED' / 'PROBABLE' force-attribute the txn (the `attribute`
-- CLI); 'EXCLUDED' (the `exclude` CLI) is the negative — it DROPS the txn from
-- this owner's classification entirely (not even to the review queue), for a
-- documented "this is NOT this owner" decision where no signal can separate a
-- same-named relative. No CHECK constraint: status is validated by the CLI layer.
CREATE TABLE IF NOT EXISTS manual_attributions (
    transaction_id  TEXT NOT NULL,
    entity_slug     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'CONFIRMED',
    reason          TEXT,
    source          TEXT,
    attributed_at   TEXT NOT NULL,
    attributed_by   TEXT,
    PRIMARY KEY (transaction_id, entity_slug)
);
CREATE INDEX IF NOT EXISTS idx_manual_attributions_slug
    ON manual_attributions(entity_slug);

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

-- v10: committee tombstones. A committee_id that appears as a donation recipient
-- but whose /committee/<id>/ endpoint permanently fails (404 dissolved id, 403,
-- 422, or a 200 with zero results) has no row in `committees` to carry a
-- `not_found_at` stamp — so the id is a candidate every convergence run and fails
-- every run (§4.4). This table records the permanent failure so `ingest_all_committees`
-- skips it. A `force_refresh` run re-attempts a tombstoned id and clears its row
-- on success (in case FEC restores the record). Neutral provenance: status + the
-- endpoint + the UTC stamp only, no interpretation.
CREATE TABLE IF NOT EXISTS committee_tombstones (
    committee_id  TEXT PRIMARY KEY,
    not_found_at  TEXT NOT NULL,
    http_status   INTEGER,
    endpoint      TEXT,
    reason        TEXT
);
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
    # v8: FEC earmark/conduit fields. An earmarked contribution routed through a
    # conduit (ActBlue/WinRed) is reported twice — once by the conduit and once
    # by the ultimate recipient — under distinct transaction_ids, so a naive SUM
    # double-counts it. FEC's own convention distinguishes the legs via
    # is_individual (the conduit passthrough leg is is_individual=False); memo_code
    # / memo_text are stored for provenance. The derived `counted` column (below)
    # is what SUM surfaces filter on. See DONATION_SCHEMA.md.
    ("memo_code", "TEXT"),
    ("memo_text", "TEXT"),
    ("is_individual", "INTEGER"),
    # v9: FEC's globally-unique record id. `transaction_id` is FILER-assigned
    # (e.g. "SA11AI.20387") and is NOT unique across committees, so two distinct
    # contributions can share one — which insert_donation would otherwise treat as
    # a restatement and supersede the wrong record. `sub_id` is FEC's own unique
    # id; it is the authoritative identity used to tell a genuine restatement
    # (same sub_id) from a cross-committee collision (different sub_id). Stored for
    # every new row; NULL on legacy rows whose raw is gone (backfill via
    # `cli backfill-sub-id`). transaction_id is kept for display/citation.
    ("sub_id", "TEXT"),
]

# v8: derived dedup flag. NOT an FEC field — it is neutral, mechanical arithmetic
# (the same dedup FEC applies to its own totals via is_individual), recomputed by
# recompute_counted() after every ingest/reclassify. counted=0 marks a conduit
# passthrough leg (is_individual=False) that has a countable sibling leg in the
# same (entity_slug, contributor_name_raw, date, amount) group — i.e. the genuine
# double-count. A lone conduit leg (the only record of a real contribution) keeps
# counted=1. Every published SUM filters `counted = 1`; the row is never deleted
# (GOVERNANCE.md §1.10) and stays queryable.
DONATION_DERIVED_COLS: list[tuple[str, str]] = [
    ("counted", "INTEGER NOT NULL DEFAULT 1"),
    # v12: the row's identity. COALESCE(sub_id, transaction_id) — see
    # _migrate_donations_record_uid for why transaction_id alone cannot be it.
    ("record_uid", "TEXT"),
]

SCHEMA_VERSION = 12


def record_uid_for(sub_id, transaction_id) -> str:
    """The donations identity key: FEC's globally-unique `sub_id` when present,
    else the filer-assigned `transaction_id`.

    `transaction_id` is unique only WITHIN one filing — different committees
    reuse it — so it cannot identify a row. `sub_id` is FEC's own row id and is
    globally unique, but it is absent on older (largely pre-2006) records, hence
    the fallback. Keep this in lockstep with `fetch_fec._dedupe_key`, which
    picks the same field in the same order one layer earlier in the pipeline.
    """
    s = (str(sub_id).strip() if sub_id is not None else "")
    return s or str(transaction_id)


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
    # §4.5: wait up to 30s for a competing writer's lock instead of failing
    # instantly — a manual CLI op during a scheduled refresh otherwise dies with
    # "database is locked".
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_review_queue_pk(conn: sqlite3.Connection) -> None:
    """v9: rebuild review_queue with PK (transaction_id, entity_slug) if it still
    has the legacy single-column PK. Idempotent, data-preserving. A pre-existing
    DB created its review_queue via the old CREATE (transaction_id PRIMARY KEY);
    SQLite can't ALTER a PK, so we rebuild. New DBs already get the composite PK
    from SCHEMA_SQL and skip this."""
    pk_cols = [r["name"] for r in conn.execute("PRAGMA table_info(review_queue)") if r["pk"]]
    if set(pk_cols) == {"transaction_id", "entity_slug"}:
        return  # already migrated
    conn.executescript(
        """
        CREATE TABLE review_queue_v9 (
            transaction_id TEXT NOT NULL,
            entity_slug TEXT NOT NULL,
            reason TEXT NOT NULL,
            raw_payload_path TEXT NOT NULL,
            queued_at TEXT NOT NULL,
            resolution TEXT,
            resolution_reason TEXT,
            resolution_at TEXT,
            resolved_by TEXT,
            PRIMARY KEY (transaction_id, entity_slug)
        );
        INSERT OR IGNORE INTO review_queue_v9
            SELECT transaction_id, entity_slug, reason, raw_payload_path, queued_at,
                   resolution, resolution_reason, resolution_at, resolved_by
              FROM review_queue;
        DROP TABLE review_queue;
        ALTER TABLE review_queue_v9 RENAME TO review_queue;
        """
    )


def _migrate_donations_pk(conn: sqlite3.Connection) -> None:
    """v11: rebuild `donations` with PK (transaction_id, entity_slug).

    Idempotent, data-preserving. SQLite cannot ALTER a primary key, so this
    rebuilds — mirroring _migrate_review_queue_pk, which did the same for
    review_queue in v9.

    Unlike that one, the column list is read from PRAGMA rather than hard-coded:
    `donations` gains columns through DONATION_EXTRA_COLS / DONATION_DERIVED_COLS
    ALTERs in init(), so a literal CREATE here would silently drop whatever a
    future version adds. We copy the CREATE statement SQLite already has, swap
    only the PK clause, and copy every column by name.
    """
    pk_cols = [r["name"] for r in conn.execute("PRAGMA table_info(donations)") if r["pk"]]
    if set(pk_cols) == {"transaction_id", "entity_slug"}:
        return  # already migrated
    if set(pk_cols) == {"record_uid", "entity_slug"}:
        # Already at v12 (a fresh DB built from SCHEMA_SQL, or one that has run
        # _migrate_donations_record_uid). v12 supersedes this migration; without
        # this branch the rewrite below would not find the v10 PK clause and
        # would raise.
        return

    cols = [r["name"] for r in conn.execute("PRAGMA table_info(donations)")]
    col_list = ", ".join(f'"{c}"' for c in cols)

    create_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='donations'"
    ).fetchone()["sql"]
    # Swap the single-column PK for a NOT NULL column + a table-level composite PK.
    rebuilt = create_sql.replace(
        "transaction_id TEXT PRIMARY KEY", "transaction_id TEXT NOT NULL", 1
    )
    if rebuilt == create_sql:
        raise RuntimeError(
            "donations PK migration: could not find 'transaction_id TEXT PRIMARY KEY' "
            "in the stored CREATE statement — refusing to rebuild blindly."
        )
    rebuilt = rebuilt.replace(
        "CREATE TABLE donations", "CREATE TABLE donations_v11", 1
    ).rstrip().rstrip(";")
    # Append the composite PK just inside the closing paren.
    assert rebuilt.endswith(")"), rebuilt[-40:]
    rebuilt = rebuilt[:-1].rstrip().rstrip(",") + ", PRIMARY KEY (transaction_id, entity_slug))"

    conn.execute(rebuilt)
    # INSERT (not INSERT OR IGNORE): a row silently dropped here would be exactly
    # the data loss this migration exists to prevent, so let it fail loudly.
    conn.execute(f"INSERT INTO donations_v11 ({col_list}) SELECT {col_list} FROM donations")
    moved = conn.execute("SELECT COUNT(*) AS n FROM donations_v11").fetchone()["n"]
    original = conn.execute("SELECT COUNT(*) AS n FROM donations").fetchone()["n"]
    if moved != original:
        raise RuntimeError(
            f"donations PK migration: copied {moved} of {original} rows — aborting."
        )
    conn.execute("DROP TABLE donations")
    conn.execute("ALTER TABLE donations_v11 RENAME TO donations")
    # DROP TABLE takes the table's indexes with it, and init() already ran
    # SCHEMA_SQL (which creates them) BEFORE this migration — so without this
    # they would silently be gone until some later init(). Recreate them here so
    # the migration is self-contained.
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_donations_entity_date
            ON donations(entity_slug, date);
        CREATE INDEX IF NOT EXISTS idx_donations_status
            ON donations(status);
        CREATE INDEX IF NOT EXISTS idx_donations_candidate
            ON donations(recipient_candidate_id, date);
        CREATE INDEX IF NOT EXISTS idx_donations_cycle_entity
            ON donations(election_cycle, entity_slug);
        """
    )


def _migrate_donations_record_uid(conn: sqlite3.Connection) -> None:
    """v12: rebuild `donations` with PK (record_uid, entity_slug).

    WHY. v11 made the PK (transaction_id, entity_slug), which fixed *cross-owner*
    reuse of a filer-assigned id. It left *within-owner* reuse unrepresentable —
    and that case is real. Two examples found live on 2026-07-20, both same-day
    pairs to related committees:

        dewitt-bill      SA18.1160868    $300 → John McCain 2008
                                       $2,300 → McCain-Palin Compliance Fund
        reinsdorf-jerry  SA17A.939857  −$2,300 → John McCain 2008
                                       $2,300 → McCain-Palin Compliance Fund

    Each pair is two genuine contributions distinguished only by `sub_id`. Under
    the v11 PK the second one superseded the first with a fabricated
    "FEC restatement: …" — the §1.3 corruption class. Keying on `record_uid`
    (= sub_id when present) lets both exist, which is what FEC actually published.

    `transaction_id` is KEPT as a plain column: it is the citation people quote
    and what `review_queue` / `review_resolutions` / `manual_attributions` key on.
    It gains an index here because it is no longer the PK.

    Idempotent and data-preserving. Follows _migrate_donations_pk's two hard-won
    rules: read the column list from PRAGMA and reuse SQLite's own stored CREATE
    (never a literal — `donations` gains columns via ALTER in init()), and
    recreate the indexes afterwards, because init() runs SCHEMA_SQL *before* this
    migration so the DROP TABLE takes them with it.
    """
    pk_cols = [r["name"] for r in conn.execute("PRAGMA table_info(donations)") if r["pk"]]
    if set(pk_cols) == {"record_uid", "entity_slug"}:
        return  # already migrated

    cols = [r["name"] for r in conn.execute("PRAGMA table_info(donations)")]
    if "record_uid" not in cols:
        conn.execute("ALTER TABLE donations ADD COLUMN record_uid TEXT")
        cols.append("record_uid")

    # Backfill before the rebuild so the new NOT NULL PK column is fully populated.
    conn.execute(
        "UPDATE donations SET record_uid = COALESCE(NULLIF(TRIM(sub_id), ''), transaction_id) "
        "WHERE record_uid IS NULL OR TRIM(record_uid) = ''"
    )
    dupes = conn.execute(
        "SELECT COUNT(*) AS n FROM (SELECT record_uid, entity_slug FROM donations "
        "GROUP BY record_uid, entity_slug HAVING COUNT(*) > 1)"
    ).fetchone()["n"]
    if dupes:
        raise RuntimeError(
            f"donations record_uid migration: {dupes} (record_uid, entity_slug) pairs "
            "are not unique — refusing to rebuild, since the new PK would drop rows. "
            "Inspect before retrying."
        )

    col_list = ", ".join(f'"{c}"' for c in cols)
    create_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='donations'"
    ).fetchone()["sql"]

    rebuilt = create_sql.replace(
        "PRIMARY KEY (transaction_id, entity_slug)", "PRIMARY KEY (record_uid, entity_slug)", 1
    )
    if rebuilt == create_sql:
        raise RuntimeError(
            "donations record_uid migration: could not find the v11 composite PK clause "
            "in the stored CREATE statement — refusing to rebuild blindly."
        )
    rebuilt = rebuilt.replace("CREATE TABLE donations", "CREATE TABLE donations_v12", 1)
    rebuilt = rebuilt.replace('"donations"', "donations_v12", 1)

    conn.execute(rebuilt)
    # INSERT, not INSERT OR IGNORE: a silently dropped row here is exactly the
    # data loss this migration exists to prevent.
    conn.execute(f"INSERT INTO donations_v12 ({col_list}) SELECT {col_list} FROM donations")
    moved = conn.execute("SELECT COUNT(*) AS n FROM donations_v12").fetchone()["n"]
    original = conn.execute("SELECT COUNT(*) AS n FROM donations").fetchone()["n"]
    if moved != original:
        raise RuntimeError(
            f"donations record_uid migration: copied {moved} of {original} rows — aborting."
        )
    conn.execute("DROP TABLE donations")
    conn.execute("ALTER TABLE donations_v12 RENAME TO donations")
    # DROP TABLE took the indexes; init() already ran SCHEMA_SQL before this.
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_donations_entity_date
            ON donations(entity_slug, date);
        CREATE INDEX IF NOT EXISTS idx_donations_status
            ON donations(status);
        CREATE INDEX IF NOT EXISTS idx_donations_candidate
            ON donations(recipient_candidate_id, date);
        CREATE INDEX IF NOT EXISTS idx_donations_cycle_entity
            ON donations(election_cycle, entity_slug);
        -- v12: transaction_id is no longer the PK but is still the citation key
        -- and the join key for review_queue / manual_attributions.
        CREATE INDEX IF NOT EXISTS idx_donations_txn
            ON donations(transaction_id, entity_slug);
        """
    )


def check_record_uid_integrity(db_path: Path = MASTER_DB) -> list[str]:
    """v12 integrity: every donations row must carry a record_uid, and
    (record_uid, entity_slug) must be unique.

    The column is nullable in SCHEMA_SQL so that test fixtures inserting rows by
    raw SQL need not carry it (SQLite permits NULL in a non-INTEGER PK). That
    convenience must not leak into real data, so the guarantee is asserted here
    instead and wired into `cli validate`. Returns a list of error strings;
    empty means clean. A missing DB is not an error — nothing to check.
    """
    errors: list[str] = []
    if not Path(db_path).exists():
        return errors
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(donations)")}
        if "record_uid" not in cols:
            return ["donations.record_uid is missing — run `cli init` to apply the v12 migration"]
        missing = conn.execute(
            "SELECT COUNT(*) AS n FROM donations "
            "WHERE record_uid IS NULL OR TRIM(record_uid) = ''"
        ).fetchone()["n"]
        if missing:
            errors.append(
                f"{missing} donations row(s) have no record_uid — every row needs an "
                "identity (COALESCE(sub_id, transaction_id)); re-run `cli init`"
            )
        dupes = conn.execute(
            "SELECT COUNT(*) AS n FROM (SELECT record_uid, entity_slug FROM donations "
            "WHERE record_uid IS NOT NULL GROUP BY record_uid, entity_slug HAVING COUNT(*) > 1)"
        ).fetchone()["n"]
        if dupes:
            errors.append(
                f"{dupes} (record_uid, entity_slug) pair(s) are duplicated — the v12 "
                "identity is not unique, which means two rows are claiming one FEC record"
            )
        # A stored uid that disagrees with its own sub_id/transaction_id means a
        # writer bypassed record_uid_for(); catch the drift rather than the symptom.
        drift = conn.execute(
            "SELECT COUNT(*) AS n FROM donations WHERE record_uid IS NOT NULL "
            "AND superseded_by IS NULL "
            "AND record_uid <> COALESCE(NULLIF(TRIM(sub_id), ''), transaction_id)"
        ).fetchone()["n"]
        if drift:
            errors.append(
                f"{drift} live donations row(s) have a record_uid that is neither their "
                "sub_id nor their transaction_id — a writer bypassed db.record_uid_for()"
            )
    finally:
        conn.close()
    return errors


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
        # v3/v8/v9: per-transaction FEC fields + the derived counted flag on donations
        existing_donation_cols = {r["name"] for r in conn.execute("PRAGMA table_info(donations)")}
        for col_name, col_type in DONATION_EXTRA_COLS + DONATION_DERIVED_COLS:
            if col_name not in existing_donation_cols:
                conn.execute(f"ALTER TABLE donations ADD COLUMN {col_name} {col_type}")
        # v9: migrate review_queue to the composite PK (transaction_id, entity_slug).
        _migrate_review_queue_pk(conn)
        # v11: same migration for donations — see _migrate_donations_pk.
        _migrate_donations_pk(conn)
        # v12: re-key donations onto record_uid (= sub_id when present) so two
        # contributions sharing one filer-assigned transaction_id can coexist.
        # Must run AFTER the v11 rebuild, whose PK clause it rewrites.
        _migrate_donations_record_uid(conn)
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

    Reads the owner YAML and writes one row per owner, PLUS one row per declared
    `related_entity` (spouse/child/parent/sibling/pac/business_entity) with
    `kind` from the YAML and `parent_slug` set to the owner's slug. This keeps the
    entities mirror honest once related-entity ingestion is enabled: the dashboard
    builder and any per-entity query can see the household members the donations
    table now references via `parent_owner_slug`. Related rows carry the owner's
    `yaml_path`/`yaml_sha256` (they live in the same file) and no team/tenure.
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
            owner_slug = data.get("slug")
            yaml_rel = relpath(yaml_path)
            yaml_hash = _sha256_file(yaml_path)
            conn.execute(
                """
                INSERT INTO entities
                  (slug, kind, parent_slug, name, team, tenure_start_date,
                   tenure_end_date, family_tenure_start_date,
                   yaml_path, yaml_sha256, refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_slug,
                    "owner",
                    None,
                    data.get("name"),
                    data.get("team"),
                    str(data.get("tenure_start_date")) if data.get("tenure_start_date") else None,
                    str(data.get("tenure_end_date")) if data.get("tenure_end_date") else None,
                    str(data.get("family_tenure_start_date")) if data.get("family_tenure_start_date") else None,
                    yaml_rel,
                    yaml_hash,
                    now,
                ),
            )
            rows += 1
            # Related entities (households) — one mirror row each, parented to the
            # owner. Skipped silently if malformed/unnamed (validate_owners is the
            # gate for YAML correctness; refresh is not the place to fail).
            for ent in (data.get("related_entities") or []):
                if not isinstance(ent, dict):
                    continue
                ent_slug = ent.get("slug")
                if not ent_slug:
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
                        ent_slug,
                        ent.get("kind") or "spouse",
                        owner_slug,
                        ent.get("name") or ent_slug,
                        None,
                        None,
                        None,
                        None,
                        yaml_rel,
                        yaml_hash,
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
            receipt_type_full, recipient_committee_type,
            memo_code, memo_text, is_individual, sub_id, record_uid
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
            :receipt_type_full, :recipient_committee_type,
            :memo_code, :memo_text, :is_individual, :sub_id, :record_uid
        )
        """,
        {**full_row, "record_uid": full_row.get("record_uid")
         or record_uid_for(full_row.get("sub_id"), full_row["transaction_id"])},
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
      - ("collision", <reason>) — a live row shares this transaction_id but is a
                               DIFFERENT contribution (filer-assigned ids are not
                               unique across committees). Nothing is written; the
                               caller logs it (§1.3). Detected three ways:
                                 1. differing globally-unique sub_ids;
                                 2. a differing entity_slug (one FEC transaction
                                    has exactly one contributor, so two owners
                                    claiming it is id reuse by construction);
                                 3. same owner, but recipient committee AND date
                                    AND amount all differ — a restatement fixes a
                                    field or two of one contribution, it does not
                                    change all three at once.
                               Test 1 alone was the original guard, but it can
                               only fire when BOTH rows carry a sub_id, and sub_id
                               is sparsely populated — so it was inert for most of
                               the archive and real collisions fell through to the
                               supersede path, where they were recorded as
                               fabricated "FEC restatements" of one owner's
                               donation into another's. Two were found live on
                               2026-07-19 (kendrick-ken $2,800 and dewitt-bill
                               $5,000, both "restated" into reinsdorf-jerry rows).
                               Tests 2 and 3 need no sub_id and close that hole.

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
    uid = record_uid_for(full_row.get("sub_id"), txn)
    full_row["record_uid"] = uid
    slug = full_row["entity_slug"]

    # v12: identity is (record_uid, entity_slug), where record_uid is FEC's
    # globally-unique sub_id when present. v11 scoped the lookup by entity_slug,
    # which fixed CROSS-owner reuse of a filer id; keying on record_uid fixes
    # WITHIN-owner reuse, where two real same-day contributions share one
    # transaction_id and the second used to supersede the first with a fabricated
    # restatement.
    existing = conn.execute(
        "SELECT * FROM donations WHERE record_uid = ? AND entity_slug = ?",
        (uid, slug),
    ).fetchone()

    if existing is None and uid != txn:
        # Legacy bridge. Rows written before sub_id was captured (58% of the
        # archive at v12) have record_uid = transaction_id. The same contribution
        # arriving now WITH a sub_id would look brand-new and duplicate. Adopt
        # such a row instead — but only when it is unambiguously the same
        # contribution (same committee, date and amount) and carries no sub_id of
        # its own, so a genuine within-owner id collision still falls through to
        # a clean insert.
        legacy = conn.execute(
            """
            SELECT * FROM donations
             WHERE transaction_id = ? AND entity_slug = ?
               AND (sub_id IS NULL OR TRIM(sub_id) = '')
               AND record_uid = transaction_id
               AND recipient_committee_id IS ?
               AND date IS ?
               AND amount IS ?
            """,
            (txn, slug, full_row.get("recipient_committee_id"),
             full_row.get("date"), full_row.get("amount")),
        ).fetchall()
        if len(legacy) == 1:
            conn.execute(
                "UPDATE donations SET record_uid = ?, sub_id = ? "
                "WHERE record_uid = ? AND entity_slug = ?",
                (uid, full_row.get("sub_id"), txn, slug),
            )
            existing = conn.execute(
                "SELECT * FROM donations WHERE record_uid = ? AND entity_slug = ?",
                (uid, slug),
            ).fetchone()

    if existing is None:
        _insert_donation_row(conn, full_row)
        return ("inserted", None)

    existing_d = dict(existing)

    # v9 collision guard: transaction_id is filer-assigned and NOT unique across
    # committees. If the incoming row and the stored row carry DIFFERENT globally-
    # unique sub_ids, they are DIFFERENT contributions that merely share a
    # transaction_id — NOT a restatement. Superseding here would clobber the wrong
    # record (the latent §1.3 bug). Refuse loudly rather than corrupt silently;
    # the caller logs it. (Zero occurrences in the live DB — this closes the risk.)
    inc_sub = full_row.get("sub_id")
    old_sub = existing_d.get("sub_id")
    if inc_sub and old_sub and str(inc_sub) != str(old_sub):
        return ("collision", f"transaction_id {txn}: sub_id {old_sub} vs {inc_sub} (distinct contributions)")

    # The sub_id test above can only fire when BOTH rows carry one, and sub_id is
    # sparsely populated (a few percent of the archive) — so for most rows it is
    # inert and a real collision fell through to the supersede path below, where
    # it was recorded as a fabricated "FEC restatement" of one owner's donation
    # into another's. Two such rows were found live in the archive (2026-07-19).
    # These two tests need no sub_id.
    #
    # (a) The cross-owner case no longer reaches here at all: since v11 the PK is
    #     (transaction_id, entity_slug) and the lookup above is scoped to this
    #     entity, so another owner reusing the id simply inserts alongside. That
    #     is the fix; the guard below covers what remains.
    #
    # (b) Same entity, but the contribution's whole identity differs. A genuine
    #     FEC restatement corrects a field or two of ONE contribution; it does not
    #     simultaneously change who received it, when, and how much. Requiring all
    #     three to diverge keeps ordinary restatements (amount fix, re-image,
    #     amended filing) on the supersede path.
    # v12 tightening. This test previously required committee AND date AND amount
    # to ALL differ. That was too weak: the real within-owner collisions found on
    # 2026-07-20 were SAME-DAY pairs (a campaign and its compliance fund), so the
    # date matched, the guard stayed silent, and the supersede path fabricated a
    # restatement. Requiring committee AND amount to differ — with date free —
    # catches those while still leaving ordinary restatements alone: a genuine
    # restatement corrects the amount OR re-designates the recipient, not both at
    # once. (Re-images change filing_id/image_number only and never reach here.)
    #
    # Under v12 this guard is mostly a backstop: two contributions with distinct
    # sub_ids now have distinct record_uids and simply insert alongside each
    # other. It still fires for older rows where NEITHER side carries a sub_id,
    # which is the only case that can still share an identity.
    differing = [
        f
        for f in ("recipient_committee_id", "date", "amount")
        if not _donation_values_equal(existing_d.get(f), full_row.get(f))
    ]
    if "recipient_committee_id" in differing and "amount" in differing:
        return (
            "collision",
            f"transaction_id {txn}: committee AND amount differ "
            f"({existing_d.get('recipient_committee_id')}/{existing_d.get('date')}/"
            f"{existing_d.get('amount')} vs {full_row.get('recipient_committee_id')}/"
            f"{full_row.get('date')}/{full_row.get('amount')}) — distinct contributions, "
            "not a restatement",
        )

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
    # v12: scope the archive UPDATE by record_uid (the PK), not transaction_id —
    # the latter is no longer unique within an owner, so a transaction_id-scoped
    # UPDATE could archive a *sibling* contribution that merely shares the id.
    # record_uid is re-keyed alongside transaction_id so the archived row keeps a
    # distinct identity and the canonical uid is freed for the restated payload.
    conn.execute(
        """
        UPDATE donations
           SET transaction_id = ?, record_uid = ?, status = ?,
               superseded_by = ?, superseded_reason = ?
         WHERE record_uid = ? AND entity_slug = ?
        """,
        (archived_key, archived_key, "SUPERSEDED", txn, reason, uid, slug),
    )
    _insert_donation_row(conn, full_row)
    return ("superseded", reason)


def recompute_counted(conn: sqlite3.Connection, slug: str | None = None) -> int:
    """Recompute the derived `counted` dedup flag on CONFIRMED/PROBABLE donations.

    An earmarked contribution routed through a conduit (ActBlue/WinRed) is
    reported twice in FEC data — once by the conduit and once by the ultimate
    recipient — under distinct transaction_ids. FEC excludes the conduit
    passthrough leg from its own individual-contribution totals; that leg carries
    is_individual = 0. We mirror that: a leg is marked counted = 0 (excluded from
    every published SUM) **only when** is_individual = 0 AND a countable sibling
    leg exists in the same (entity_slug, contributor_name_raw, date, amount)
    group. A lone conduit leg — the sole record of a real contribution FEC only
    itemized at the conduit — keeps counted = 1 so it is never silently dropped.

    Idempotent; rows are never deleted (GOVERNANCE.md §1.10). Pass `slug` to scope
    to one owner (unset column stays at its DEFAULT 1 elsewhere), or None to
    recompute the whole table. Returns the number of counted = 0 rows after the
    pass. A sibling with unknown is_individual (NULL — e.g. a row whose raw
    payload is gone) is treated as countable, so a real recipient leg is never
    mistaken for a passthrough.
    """
    params: list = []
    scope = ""
    if slug is not None:
        scope = " AND (entity_slug = ? OR parent_owner_slug = ?)"
        params = [slug, slug]
    conn.execute(
        f"""
        UPDATE donations
           SET counted = CASE
               WHEN is_individual = 0
                AND EXISTS (
                    SELECT 1 FROM donations s
                     WHERE s.entity_slug = donations.entity_slug
                       AND s.contributor_name_raw = donations.contributor_name_raw
                       AND s.date = donations.date
                       AND s.amount = donations.amount
                       AND s.transaction_id <> donations.transaction_id
                       AND s.status IN ('CONFIRMED', 'PROBABLE')
                       AND (s.is_individual IS NULL OR s.is_individual <> 0)
                )
               THEN 0 ELSE 1 END
         WHERE status IN ('CONFIRMED', 'PROBABLE'){scope}
        """,
        params,
    )
    return conn.execute(
        "SELECT COUNT(*) FROM donations WHERE counted = 0 AND status IN ('CONFIRMED','PROBABLE')"
    ).fetchone()[0]


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


def upsert_manual_attribution(
    conn: sqlite3.Connection,
    *,
    transaction_id: str,
    entity_slug: str,
    status: str = "CONFIRMED",
    reason: str | None,
    source: str | None,
    attributed_at: str,
    attributed_by: str | None = None,
) -> None:
    """Record (or overwrite) a manual attribution override for one transaction.

    Keyed by (transaction_id, entity_slug). Survives reclassify — applied at
    classify time to force the record to `status` for this owner regardless of
    the automated classifier's verdict (GOVERNANCE.md §1.1: a documented human
    decision, with reason + source, not an inference).
    """
    conn.execute(
        """
        INSERT INTO manual_attributions (
            transaction_id, entity_slug, status, reason, source,
            attributed_at, attributed_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(transaction_id, entity_slug) DO UPDATE SET
            status = excluded.status,
            reason = excluded.reason,
            source = excluded.source,
            attributed_at = excluded.attributed_at,
            attributed_by = excluded.attributed_by
        """,
        (transaction_id, entity_slug, status, reason, source, attributed_at, attributed_by),
    )


def delete_manual_attribution(
    conn: sqlite3.Connection, *, transaction_id: str, entity_slug: str
) -> int:
    """Remove a manual attribution override (undo). Returns rows deleted (0/1)."""
    cur = conn.execute(
        "DELETE FROM manual_attributions WHERE transaction_id = ? AND entity_slug = ?",
        (transaction_id, entity_slug),
    )
    return cur.rowcount


def manual_attributions_for_slug(conn: sqlite3.Connection, entity_slug: str) -> dict[str, str]:
    """Map of {transaction_id: status} of manual overrides for this entity.

    Used at classify time to force these transactions to the recorded status.
    """
    return {
        r["transaction_id"]: r["status"]
        for r in conn.execute(
            "SELECT transaction_id, status FROM manual_attributions WHERE entity_slug = ?",
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

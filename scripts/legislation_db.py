"""SQLite schema + helpers for the Phase 3 legislation index (data/legislation.db).

This is a SEPARATE database from master.db. It holds the neutral, sourced index
of MLB-relevant federal legislation, roll-call votes, and the legislator
crosswalk that lets a donation (keyed by FEC candidate_id) be joined to a vote
(keyed by Bioguide id). See CHARTER.md §Phase 3 and the Phase-3 addendum in
SOURCES.md.

Design rules this module encodes:

  * **Neutrality (project CLAUDE.md §2, GOVERNANCE.md §6).** Every column stores a
    neutral, sourced fact in a law-librarian's tone — a bill exists, its sponsor
    is X, a roll call happened on date D, legislator L voted Yea. `relevance_basis`
    is a *sourced factual reason* a bill is indexed (e.g. "amends 15 U.S.C. §26b"),
    never editorial spin. Interpretation lives only in reports/.
  * **Provenance.** Fetched rows carry `raw_payload_path` (the persisted upstream
    response, GOVERNANCE.md §1.4) and `source` / `source_url` (the citable origin).
  * **Separate, non-LFS, small.** master.db is untouched by anything here. This DB
    is committed as a normal git blob (NOT in .gitattributes LFS) so legislation
    churn never re-pushes master.db's ~124 MB LFS object.

The schema mirrors scripts/db.py's conventions: a single SCHEMA_SQL executescript,
an idempotent init(), a leg_schema_version migration trail, and a snapshot()
guard for gated mutations.
"""
from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .paths import LEGISLATION_DB, SNAPSHOTS_DIR, ensure_data_dirs


SCHEMA_SQL = """
-- ── Legislator crosswalk ────────────────────────────────────────────────────
-- The spine of the whole phase. Sourced from the public-domain
-- unitedstates/congress-legislators project (Tier-2 entity identification, see
-- SOURCES.md Phase-3 addendum). One person → one bioguide_id; a person may carry
-- several FEC candidate ids across cycles, so those live in legislator_fec_ids.
CREATE TABLE IF NOT EXISTS legislators (
    bioguide_id      TEXT PRIMARY KEY,
    icpsr_id         TEXT,
    govtrack_id      TEXT,
    opensecrets_id   TEXT,
    full_name        TEXT NOT NULL,
    first_name       TEXT,
    last_name        TEXT,
    current_party    TEXT,
    current_state    TEXT,
    source           TEXT NOT NULL,
    raw_payload_path TEXT,
    fetched_at       TEXT,
    refreshed_at     TEXT NOT NULL
);

-- FEC candidate_id → bioguide_id. The join key from donations
-- (donations.recipient_candidate_id) and committee beneficiaries
-- (committee_disbursements_by_recipient.recipient_id where recipient_kind =
-- 'candidate') into the legislation index. Many-to-one onto a legislator.
CREATE TABLE IF NOT EXISTS legislator_fec_ids (
    fec_candidate_id TEXT NOT NULL,
    bioguide_id      TEXT NOT NULL,
    PRIMARY KEY (fec_candidate_id, bioguide_id)
);
CREATE INDEX IF NOT EXISTS idx_legislator_fec_ids_bioguide
    ON legislator_fec_ids(bioguide_id);

-- One row per term served. Lets a query confirm a legislator was in office (and
-- in which chamber) at the time of a vote, and recover their party/state then.
CREATE TABLE IF NOT EXISTS legislator_terms (
    bioguide_id  TEXT NOT NULL,
    congress     INTEGER,
    chamber      TEXT NOT NULL,   -- 'house' / 'senate'
    state        TEXT,
    district     TEXT,
    party        TEXT,
    start_date   TEXT,
    end_date     TEXT,
    PRIMARY KEY (bioguide_id, chamber, start_date)
);
CREATE INDEX IF NOT EXISTS idx_legislator_terms_bioguide
    ON legislator_terms(bioguide_id);

-- ── Curated MLB-relevant bills ──────────────────────────────────────────────
-- The curated source of truth is legislation/bills/*.yaml (PR-reviewable, mirrors
-- owners/*.yaml); ingest enriches each into this table from Congress.gov. A bill
-- whose text was carried by another vehicle (e.g. the Save America's Pastime Act,
-- inserted as a division of the 2018 omnibus) links to that carrier via
-- carried_by_bill_id, so the roll call lives on the carrier while the policy lives
-- on the standalone — an honest model of how these fights actually move.
CREATE TABLE IF NOT EXISTS bills (
    bill_id              TEXT PRIMARY KEY,   -- {congress}-{type}-{number}, e.g. 115-hr-5580
    congress             INTEGER NOT NULL,
    bill_type            TEXT NOT NULL,      -- hr, s, hjres, sjres, ...
    number               INTEGER NOT NULL,
    title                TEXT,
    short_title          TEXT,
    introduced_date      TEXT,
    latest_action        TEXT,
    latest_action_date   TEXT,
    enacted              INTEGER NOT NULL DEFAULT 0,
    carried_by_bill_id   TEXT,               -- self-ref to the vehicle that carried this bill's text
    mlb_issue_area       TEXT NOT NULL,      -- curated taxonomy key (legislation/issues.yaml)
    relevance_basis      TEXT NOT NULL,      -- sourced factual reason it is indexed (NOT editorial)
    relevance_source_url TEXT,
    congress_dot_gov_url TEXT,
    source               TEXT,
    raw_payload_path     TEXT,
    fetched_at           TEXT,
    refreshed_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bills_issue_area ON bills(mlb_issue_area);

CREATE TABLE IF NOT EXISTS bill_sponsors (
    bill_id     TEXT NOT NULL,
    bioguide_id TEXT NOT NULL,
    role        TEXT NOT NULL,   -- 'sponsor' / 'cosponsor'
    PRIMARY KEY (bill_id, bioguide_id, role)
);
CREATE INDEX IF NOT EXISTS idx_bill_sponsors_bioguide ON bill_sponsors(bioguide_id);

-- ── Roll-call votes (only on the curated bill set) ──────────────────────────
-- Sourced from the House Clerk XML / Senate roll-call XML (Tier-1 source of
-- record); Congress.gov is a cross-check. bill_id is nullable — a procedural or
-- omnibus vote may not map cleanly to one numbered bill.
CREATE TABLE IF NOT EXISTS votes (
    vote_id          TEXT PRIMARY KEY,   -- {chamber}-{congress}-{session}-{roll}
    bill_id          TEXT,
    chamber          TEXT NOT NULL,      -- 'house' / 'senate'
    congress         INTEGER,
    session          INTEGER,
    roll_number      INTEGER,
    vote_date        TEXT,
    question         TEXT,
    description      TEXT,
    result           TEXT,
    source           TEXT,
    source_url       TEXT,
    raw_payload_path TEXT,
    fetched_at       TEXT,
    refreshed_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_votes_bill ON votes(bill_id);

-- One row per legislator per roll call. Small, because we fetch only the curated
-- votes. position is FEC/Clerk's recorded value verbatim (Yea/Nay/Present/Not Voting).
CREATE TABLE IF NOT EXISTS vote_positions (
    vote_id     TEXT NOT NULL,
    bioguide_id TEXT NOT NULL,
    position    TEXT NOT NULL,
    PRIMARY KEY (vote_id, bioguide_id)
);
CREATE INDEX IF NOT EXISTS idx_vote_positions_bioguide ON vote_positions(bioguide_id);

-- ── Timeline anchors without a clean roll call ──────────────────────────────
-- For sourced facts that are not a recorded vote: a bill's text enacted via a
-- carrier, a committee hearing date, a regulatory/agency action. Keeps the
-- timeline honest where there is no up-or-down vote to point at (the Save
-- America's Pastime Act is exactly this case). Neutral facts only.
CREATE TABLE IF NOT EXISTS policy_events (
    event_id     TEXT PRIMARY KEY,
    bill_id      TEXT,
    event_type   TEXT NOT NULL,   -- 'enacted_via' / 'hearing' / 'regulatory' / 'introduced' / ...
    event_date   TEXT,
    description  TEXT,
    source       TEXT,
    source_url   TEXT,
    refreshed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policy_events_bill ON policy_events(bill_id);

CREATE TABLE IF NOT EXISTS leg_schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

LEG_SCHEMA_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


@contextmanager
def connect(db_path: Path = LEGISLATION_DB) -> Iterator[sqlite3.Connection]:
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


def init(db_path: Path = LEGISLATION_DB) -> None:
    """Create the legislation schema idempotently and record the version row."""
    ensure_data_dirs()
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        existing = conn.execute(
            "SELECT MAX(version) AS v FROM leg_schema_version"
        ).fetchone()
        current = existing["v"] if existing else None
        if current is None or current < LEG_SCHEMA_VERSION:
            conn.execute(
                "INSERT OR IGNORE INTO leg_schema_version (version, applied_at) VALUES (?, ?)",
                (LEG_SCHEMA_VERSION, _utc_now_iso()),
            )


def snapshot(run_id: str, db_path: Path = LEGISLATION_DB) -> Path | None:
    """Copy legislation.db to data/snapshots/<UTC>__<run-id>.db before a gated
    mutation. Returns None if the DB does not exist yet (first run).
    """
    ensure_data_dirs()
    if not db_path.exists():
        return None
    target = SNAPSHOTS_DIR / f"{_utc_now_filename()}__{run_id}.db"
    shutil.copy2(db_path, target)
    return target


def attach_for_join(
    conn: sqlite3.Connection,
    *,
    master_db: Path,
    alias: str = "master",
) -> None:
    """ATTACH master.db (read-only join target) onto a legislation.db connection.

    Lets the neutral owner→donation→legislator→vote query run across both DBs
    without copying donation rows into the legislation index. master.db is never
    written through this connection.
    """
    conn.execute(f"ATTACH DATABASE ? AS {alias}", (str(master_db),))

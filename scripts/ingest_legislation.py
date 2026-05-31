"""Ingest orchestrators + read-only coverage for the Phase 3 legislation index.

`ingest_legislators` writes the FEC-candidate-id → Bioguide crosswalk into
legislation.db. The three crosswalk tables (legislators, legislator_fec_ids,
legislator_terms) are a pure projection of the upstream congress-legislators
data, so the write is a wipe-and-rebuild — idempotent, the same pattern as
db.refresh_entities for owners. Gating (snapshot + PROVENANCE_LOG) happens in the
CLI layer (GOVERNANCE.md §1.6, §2.4).

`donation_legislator_coverage` is read-only: it answers the de-risking question
for the whole phase — of the FEC candidate ids our donations point at, how many
resolve to a legislator in the crosswalk? A low number would mean the join can't
carry the phase and we'd stop to fix the crosswalk before building bills/votes.
"""
from __future__ import annotations

from pathlib import Path

from . import legislation_db
from .paths import LEGISLATION_DB, MASTER_DB


def ingest_legislators(
    legislators: list[dict],
    fec_ids: list[dict],
    terms: list[dict],
    *,
    db_path: Path = LEGISLATION_DB,
) -> dict:
    """Wipe + rebuild the crosswalk tables from parsed rows. Returns counts."""
    legislation_db.init(db_path)
    with legislation_db.connect(db_path) as conn:
        conn.execute("DELETE FROM legislator_terms")
        conn.execute("DELETE FROM legislator_fec_ids")
        conn.execute("DELETE FROM legislators")

        conn.executemany(
            """
            INSERT INTO legislators (
                bioguide_id, icpsr_id, govtrack_id, opensecrets_id,
                full_name, first_name, last_name, current_party, current_state,
                source, raw_payload_path, fetched_at, refreshed_at
            ) VALUES (
                :bioguide_id, :icpsr_id, :govtrack_id, :opensecrets_id,
                :full_name, :first_name, :last_name, :current_party, :current_state,
                :source, :raw_payload_path, :fetched_at, :refreshed_at
            )
            """,
            legislators,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO legislator_fec_ids (fec_candidate_id, bioguide_id) "
            "VALUES (:fec_candidate_id, :bioguide_id)",
            fec_ids,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO legislator_terms (
                bioguide_id, congress, chamber, state, district, party,
                start_date, end_date
            ) VALUES (
                :bioguide_id, :congress, :chamber, :state, :district, :party,
                :start_date, :end_date
            )
            """,
            terms,
        )
    return {
        "legislators": len(legislators),
        "fec_ids": len(fec_ids),
        "terms": len(terms),
    }


def donation_legislator_coverage(
    *,
    master_db: Path = MASTER_DB,
    leg_db: Path = LEGISLATION_DB,
    statuses: tuple[str, ...] = ("CONFIRMED", "PROBABLE"),
    top_unresolved: int = 25,
) -> dict:
    """How many distinct donation recipient_candidate_ids resolve to a legislator?

    Read-only. Joins master.db donations to the legislation.db crosswalk. Reports
    totals and the largest unresolved recipients (by donation count + dollars) so
    a gap is actionable, not just a number.
    """
    with legislation_db.connect(leg_db) as conn:
        legislation_db.attach_for_join(conn, master_db=master_db)
        status_ph = ",".join("?" for _ in statuses)

        totals = conn.execute(
            f"""
            SELECT
                COUNT(*)                                   AS n_candidate_ids,
                SUM(CASE WHEN x.bioguide_id IS NOT NULL THEN 1 ELSE 0 END) AS n_resolved
            FROM (
                SELECT DISTINCT recipient_candidate_id AS cid
                FROM master.donations
                WHERE status IN ({status_ph})
                  AND recipient_candidate_id IS NOT NULL
                  AND recipient_candidate_id <> ''
            ) d
            LEFT JOIN legislator_fec_ids x ON x.fec_candidate_id = d.cid
            """,
            statuses,
        ).fetchone()

        n_total = totals["n_candidate_ids"] or 0
        n_resolved = totals["n_resolved"] or 0

        unresolved = conn.execute(
            f"""
            SELECT
                d.recipient_candidate_id AS cid,
                MAX(d.recipient_candidate_name) AS name,
                COUNT(*)  AS n_donations,
                SUM(d.amount) AS total_amount
            FROM master.donations d
            LEFT JOIN legislator_fec_ids x
                   ON x.fec_candidate_id = d.recipient_candidate_id
            WHERE d.status IN ({status_ph})
              AND d.recipient_candidate_id IS NOT NULL
              AND d.recipient_candidate_id <> ''
              AND x.bioguide_id IS NULL
            GROUP BY d.recipient_candidate_id
            ORDER BY n_donations DESC, total_amount DESC
            LIMIT ?
            """,
            (*statuses, top_unresolved),
        ).fetchall()

    pct = (n_resolved / n_total * 100.0) if n_total else 0.0
    return {
        "statuses": list(statuses),
        "n_candidate_ids": n_total,
        "n_resolved": n_resolved,
        "n_unresolved": n_total - n_resolved,
        "pct_resolved": round(pct, 1),
        "top_unresolved": [dict(r) for r in unresolved],
    }

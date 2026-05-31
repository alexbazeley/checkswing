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

from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import legislation_db
from .paths import LEGISLATION_BILLS_DIR, LEGISLATION_DB, MASTER_DB


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Curated fields that come from the YAML, never from the Congress.gov API.
_CURATED_BILL_FIELDS = (
    "bill_id",
    "congress",
    "bill_type",
    "number",
    "mlb_issue_area",
    "relevance_basis",
    "relevance_source_url",
    "carried_by_bill_id",
)


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


def load_curated_bills(bills_dir: Path = LEGISLATION_BILLS_DIR) -> list[dict]:
    """Load the curated bill specs from legislation/bills/*.yaml (the source of truth
    for which bills are indexed and the sourced curated fields)."""
    specs: list[dict] = []
    for path in sorted(bills_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            specs.append({k: data.get(k) for k in _CURATED_BILL_FIELDS})
    return specs


def ingest_bills(specs: list[dict], client, *, db_path: Path = LEGISLATION_DB) -> dict:
    """Enrich each curated bill spec from Congress.gov and upsert into bills +
    bill_sponsors. Returns counts. Upsert (not wipe-all) keyed by bill_id, so a
    re-run re-enriches in place; bill_sponsors are replaced per bill.

    `client` is a CongressClient (or a stand-in exposing fetch_bill /
    fetch_cosponsors). The curated fields (mlb_issue_area, relevance_basis, …)
    always come from `specs`, never from the API.
    """
    from .fetch_congress import parse_bill, parse_sponsors

    legislation_db.init(db_path)
    now = _utc_now_iso()
    n_bills = 0
    n_sponsors = 0
    errors: list[dict] = []

    with legislation_db.connect(db_path) as conn:
        for spec in specs:
            bill_id = spec["bill_id"]
            try:
                raw_bill, raw_path = client.fetch_bill(
                    spec["congress"], spec["bill_type"], spec["number"]
                )
                cosponsors, _ = client.fetch_cosponsors(
                    spec["congress"], spec["bill_type"], spec["number"]
                )
            except Exception as e:  # noqa: BLE001 — record + continue, don't abort the batch
                errors.append({"bill_id": bill_id, "error": str(e)})
                continue

            from .paths import relpath

            # raw_payload_path is a provenance pointer; real fetches write under
            # the repo (data/raw/legislation/), but tolerate an out-of-repo path.
            try:
                raw_rel = relpath(raw_path)
            except ValueError:
                raw_rel = raw_path.as_posix()
            api_fields = parse_bill(raw_bill, raw_payload_path=raw_rel)
            row = {
                **api_fields,
                # Curated fields win over anything API-derived.
                "bill_id": bill_id,
                "congress": spec["congress"],
                "bill_type": spec["bill_type"],
                "number": spec["number"],
                "mlb_issue_area": spec["mlb_issue_area"],
                "relevance_basis": spec["relevance_basis"],
                "relevance_source_url": spec.get("relevance_source_url"),
                "carried_by_bill_id": spec.get("carried_by_bill_id"),
                "refreshed_at": now,
            }
            conn.execute(
                """
                INSERT INTO bills (
                    bill_id, congress, bill_type, number, title, short_title,
                    introduced_date, latest_action, latest_action_date, enacted,
                    carried_by_bill_id, mlb_issue_area, relevance_basis,
                    relevance_source_url, congress_dot_gov_url, source,
                    raw_payload_path, fetched_at, refreshed_at
                ) VALUES (
                    :bill_id, :congress, :bill_type, :number, :title, :short_title,
                    :introduced_date, :latest_action, :latest_action_date, :enacted,
                    :carried_by_bill_id, :mlb_issue_area, :relevance_basis,
                    :relevance_source_url, :congress_dot_gov_url, :source,
                    :raw_payload_path, :fetched_at, :refreshed_at
                )
                ON CONFLICT(bill_id) DO UPDATE SET
                    title=excluded.title, introduced_date=excluded.introduced_date,
                    latest_action=excluded.latest_action,
                    latest_action_date=excluded.latest_action_date,
                    enacted=excluded.enacted,
                    carried_by_bill_id=excluded.carried_by_bill_id,
                    mlb_issue_area=excluded.mlb_issue_area,
                    relevance_basis=excluded.relevance_basis,
                    relevance_source_url=excluded.relevance_source_url,
                    congress_dot_gov_url=excluded.congress_dot_gov_url,
                    source=excluded.source, raw_payload_path=excluded.raw_payload_path,
                    fetched_at=excluded.fetched_at, refreshed_at=excluded.refreshed_at
                """,
                {"short_title": None, **row},
            )
            n_bills += 1

            sponsors = parse_sponsors(raw_bill, cosponsors)
            conn.execute("DELETE FROM bill_sponsors WHERE bill_id = ?", (bill_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO bill_sponsors (bill_id, bioguide_id, role) "
                "VALUES (?, ?, ?)",
                [(bill_id, s["bioguide_id"], s["role"]) for s in sponsors],
            )
            n_sponsors += len(sponsors)

    return {"bills": n_bills, "sponsors": n_sponsors, "errors": errors}


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

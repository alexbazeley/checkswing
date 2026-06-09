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
                bioguide_id, icpsr_id, govtrack_id, opensecrets_id, lis_id,
                full_name, first_name, last_name, current_party, current_state,
                source, raw_payload_path, fetched_at, refreshed_at
            ) VALUES (
                :bioguide_id, :icpsr_id, :govtrack_id, :opensecrets_id, :lis_id,
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
    from .fetch_congress import parse_bill, parse_bill_committees, parse_sponsors

    legislation_db.init(db_path)
    now = _utc_now_iso()
    n_bills = 0
    n_sponsors = 0
    n_committees = 0
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

            # Committee(s) of referral — best-effort. A failure here must not
            # lose the bill/sponsor work already written, so it's caught
            # separately and recorded as a soft error.
            if hasattr(client, "fetch_bill_committees"):
                try:
                    raw_committees, _ = client.fetch_bill_committees(
                        spec["congress"], spec["bill_type"], spec["number"]
                    )
                    com_rows = parse_bill_committees(raw_committees)
                    conn.execute("DELETE FROM bill_committees WHERE bill_id = ?", (bill_id,))
                    conn.executemany(
                        "INSERT OR IGNORE INTO bill_committees "
                        "(bill_id, system_code, thomas_id, chamber, name) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [
                            (bill_id, c["system_code"], c["thomas_id"], c["chamber"], c["name"])
                            for c in com_rows
                        ],
                    )
                    n_committees += len(com_rows)
                except Exception as e:  # noqa: BLE001 — soft-fail the committee step only
                    errors.append({"bill_id": bill_id, "error": f"committees: {e}"})

    return {"bills": n_bills, "sponsors": n_sponsors, "committees": n_committees, "errors": errors}


def _current_congress(conn) -> int:
    """The congress the committee-membership snapshot represents.

    Derived from the crosswalk (max congress in legislator_terms) so it tracks
    the data rather than a hardcoded number; falls back to a date computation if
    terms aren't loaded yet (a Congress convenes Jan of 1789 + 2*(N-1))."""
    row = conn.execute("SELECT MAX(congress) AS c FROM legislator_terms").fetchone()
    if row and row["c"]:
        return int(row["c"])
    year = datetime.now(timezone.utc).year
    base_odd = year if year % 2 == 1 else year - 1
    return (base_odd - 1789) // 2 + 1


def ingest_committees(
    committee_rows: list[dict],
    membership_rows: list[dict],
    *,
    raw_payload_path: str | None = None,
    db_path: Path = LEGISLATION_DB,
) -> dict:
    """Wipe + rebuild committees + committee_memberships (current snapshot).

    Membership has no upstream history, so this is the current-congress roster;
    `committees.congress` records which congress it represents so the
    committee→donation join can guard on it. Returns counts.
    """
    legislation_db.init(db_path)
    now = _utc_now_iso()
    with legislation_db.connect(db_path) as conn:
        congress = _current_congress(conn)
        conn.execute("DELETE FROM committee_memberships")
        conn.execute("DELETE FROM committees")
        conn.executemany(
            """
            INSERT INTO committees (
                thomas_id, congress, chamber, name, source, source_url,
                raw_payload_path, fetched_at, refreshed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c["thomas_id"], congress, c.get("chamber"), c.get("name"),
                    "unitedstates/congress-legislators",
                    "https://unitedstates.github.io/congress-legislators/committees-current.yaml",
                    raw_payload_path, now, now,
                )
                for c in committee_rows
            ],
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO committee_memberships (
                thomas_id, bioguide_id, rank, title, party
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (m["thomas_id"], m["bioguide_id"], m.get("rank"), m.get("title"), m.get("party"))
                for m in membership_rows
            ],
        )
    return {
        "committees": len(committee_rows),
        "memberships": len(membership_rows),
        "congress": congress,
    }


def load_curated_roll_calls(bills_dir: Path = LEGISLATION_BILLS_DIR) -> list[dict]:
    """Load the roll-call specs declared under each bill YAML's `roll_calls` block.

    Each returned spec carries its parent bill_id so the vote can be linked.
    """
    specs: list[dict] = []
    for path in sorted(bills_dir.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        for rc in data.get("roll_calls") or []:
            if isinstance(rc, dict):
                specs.append({"bill_id": data.get("bill_id"), **rc})
    return specs


def ingest_votes(specs: list[dict], fetcher, *, db_path: Path = LEGISLATION_DB) -> dict:
    """Fetch + parse roll-call votes and write votes + vote_positions.

    `fetcher` exposes fetch_house_vote(year, roll) and
    fetch_senate_vote(congress, session, roll), each returning (xml_text, raw_path).
    House positions key on Bioguide directly; Senate positions key on LIS id and
    are mapped to Bioguide via legislators.lis_id (a senator who voted but is
    absent from the FEC-filtered crosswalk is counted as unmapped and skipped —
    they are not a donation recipient our join could reach anyway).

    Upsert keyed by vote_id; vote_positions replaced per vote. Returns counts.
    """
    from .fetch_votes import HOUSE_URL, SENATE_URL, parse_house_vote, parse_senate_vote
    from .paths import relpath

    legislation_db.init(db_path)
    now = _utc_now_iso()
    n_votes = 0
    n_positions = 0
    n_unmapped = 0
    errors: list[dict] = []

    with legislation_db.connect(db_path) as conn:
        lis_to_bioguide = {
            r["lis_id"]: r["bioguide_id"]
            for r in conn.execute(
                "SELECT lis_id, bioguide_id FROM legislators WHERE lis_id IS NOT NULL"
            )
        }

        for spec in specs:
            chamber = spec.get("chamber")
            congress = spec.get("congress")
            session_no = spec.get("session")
            roll = spec.get("roll")
            try:
                if chamber == "house":
                    text, raw_path = fetcher.fetch_house_vote(spec["year"], roll)
                    meta, raw_positions = parse_house_vote(text)
                    positions = raw_positions  # already bioguide-keyed
                elif chamber == "senate":
                    text, raw_path = fetcher.fetch_senate_vote(congress, session_no, roll)
                    meta, raw_positions = parse_senate_vote(text)
                    positions = []
                    for p in raw_positions:
                        bid = lis_to_bioguide.get(p["lis_member_id"])
                        if bid is None:
                            n_unmapped += 1
                            continue
                        positions.append({"bioguide_id": bid, "position": p["position"]})
                else:
                    errors.append({"spec": spec, "error": f"unknown chamber {chamber!r}"})
                    continue
            except Exception as e:  # noqa: BLE001
                errors.append({"spec": spec, "error": str(e)})
                continue

            vote_id = f"{chamber}-{congress}-{session_no}-{roll}"
            try:
                raw_rel = relpath(raw_path)
            except ValueError:
                raw_rel = raw_path.as_posix()

            conn.execute(
                """
                INSERT INTO votes (
                    vote_id, bill_id, chamber, congress, session, roll_number,
                    vote_date, question, description, result, source, source_url,
                    raw_payload_path, fetched_at, refreshed_at
                ) VALUES (
                    :vote_id, :bill_id, :chamber, :congress, :session, :roll_number,
                    :vote_date, :question, :description, :result, :source, :source_url,
                    :raw_payload_path, :fetched_at, :refreshed_at
                )
                ON CONFLICT(vote_id) DO UPDATE SET
                    bill_id=excluded.bill_id, vote_date=excluded.vote_date,
                    question=excluded.question, description=excluded.description,
                    result=excluded.result, source_url=excluded.source_url,
                    raw_payload_path=excluded.raw_payload_path,
                    refreshed_at=excluded.refreshed_at
                """,
                {
                    "vote_id": vote_id,
                    "bill_id": spec.get("bill_id"),
                    "chamber": chamber,
                    "congress": congress,
                    "session": session_no,
                    "roll_number": roll,
                    "vote_date": meta.get("vote_date"),
                    "question": spec.get("question") or meta.get("question"),
                    "description": meta.get("description"),
                    "result": meta.get("result"),
                    "source": "clerk.house.gov" if chamber == "house" else "senate.gov",
                    "source_url": (
                        HOUSE_URL.format(year=spec.get("year"), roll=roll)
                        if chamber == "house"
                        else SENATE_URL.format(congress=congress, session=session_no, roll=roll)
                    ),
                    "raw_payload_path": raw_rel,
                    "fetched_at": now,
                    "refreshed_at": now,
                },
            )
            conn.execute("DELETE FROM vote_positions WHERE vote_id = ?", (vote_id,))
            conn.executemany(
                "INSERT OR IGNORE INTO vote_positions (vote_id, bioguide_id, position) "
                "VALUES (?, ?, ?)",
                [(vote_id, p["bioguide_id"], p["position"]) for p in positions],
            )
            n_votes += 1
            n_positions += len(positions)

    return {
        "votes": n_votes,
        "positions": n_positions,
        "senate_unmapped": n_unmapped,
        "errors": errors,
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

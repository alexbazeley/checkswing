"""The neutral owner→donation→legislator→vote join (Phase 3, read-only).

This module computes neutral, sourced facts: which owner donations went to
legislators who voted on (or sponsored) a given bill, and the signed day-delta
between each donation and the vote. It writes a reproducible CSV + JSON to
reports/data/ that a brief in reports/ is built on top of.

Neutrality (project CLAUDE.md §2, GOVERNANCE.md §6): every field here is a fact —
a donation occurred, a legislator cast a position, a vote happened on a date.
`days_before_vote` is neutral arithmetic. NOTHING here asserts that a donation
caused a vote; that interpretation lives only in the brief in reports/, clearly
labeled. The query is read-only and never writes to master.db.
"""
from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

from . import legislation_db
from .paths import LEGISLATION_DB, MASTER_DB, REPORTS_DATA_DIR


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(d: str | None) -> date | None:
    if not d:
        return None
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _days_before(donation_date: str | None, vote_date: str | None) -> int | None:
    """Signed days from donation to vote: positive = donation BEFORE the vote."""
    dd, vd = _parse_iso(donation_date), _parse_iso(vote_date)
    if dd is None or vd is None:
        return None
    return (vd - dd).days


def vote_donation_rows(
    *,
    bill_ids: list[str],
    master_db: Path = MASTER_DB,
    leg_db: Path = LEGISLATION_DB,
    statuses: tuple[str, ...] = ("CONFIRMED", "PROBABLE"),
) -> list[dict]:
    """Owner donations to legislators who cast a recorded position on a vote on
    any of `bill_ids`. One row per (donation, vote). Read-only across both DBs.
    """
    if not bill_ids:
        return []
    with legislation_db.connect(leg_db) as conn:
        legislation_db.attach_for_join(conn, master_db=master_db)
        status_ph = ",".join("?" for _ in statuses)
        bill_ph = ",".join("?" for _ in bill_ids)
        sql = f"""
            SELECT
                d.entity_slug                AS owner_slug,
                e.name                       AS owner_name,
                e.team                       AS owner_team,
                d.transaction_id             AS transaction_id,
                d.amount                     AS amount,
                d.date                       AS donation_date,
                d.status                     AS donation_status,
                d.recipient_committee_name   AS recipient_committee_name,
                d.recipient_candidate_id     AS recipient_candidate_id,
                d.recipient_candidate_name   AS recipient_candidate_name,
                l.bioguide_id                AS legislator_bioguide,
                l.full_name                  AS legislator_name,
                l.current_party              AS legislator_party,
                l.current_state              AS legislator_state,
                v.bill_id                    AS vote_bill_id,
                v.vote_id                    AS vote_id,
                v.chamber                    AS vote_chamber,
                v.vote_date                  AS vote_date,
                v.question                   AS vote_question,
                v.result                     AS vote_result,
                v.source_url                 AS vote_source_url,
                vp.position                  AS legislator_position
            FROM master.donations d
            JOIN legislator_fec_ids x ON x.fec_candidate_id = d.recipient_candidate_id
            JOIN legislators l        ON l.bioguide_id = x.bioguide_id
            JOIN vote_positions vp    ON vp.bioguide_id = l.bioguide_id
            JOIN votes v              ON v.vote_id = vp.vote_id
            LEFT JOIN master.entities e ON e.slug = d.entity_slug
            WHERE d.status IN ({status_ph})
              AND v.bill_id IN ({bill_ph})
            ORDER BY d.amount DESC, d.date
        """
        cur = conn.execute(sql, (*statuses, *bill_ids))
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["days_before_vote"] = _days_before(r["donation_date"], r["vote_date"])
    return rows


def sponsor_donation_rows(
    *,
    bill_ids: list[str],
    master_db: Path = MASTER_DB,
    leg_db: Path = LEGISLATION_DB,
    statuses: tuple[str, ...] = ("CONFIRMED", "PROBABLE"),
) -> list[dict]:
    """Owner donations to the sponsors/cosponsors of any of `bill_ids` — the
    tightest tie (money to the people who actually authored the bill). One row per
    (donation, bill_sponsor). Read-only.
    """
    if not bill_ids:
        return []
    with legislation_db.connect(leg_db) as conn:
        legislation_db.attach_for_join(conn, master_db=master_db)
        status_ph = ",".join("?" for _ in statuses)
        bill_ph = ",".join("?" for _ in bill_ids)
        sql = f"""
            SELECT
                d.entity_slug              AS owner_slug,
                e.name                     AS owner_name,
                e.team                     AS owner_team,
                d.transaction_id           AS transaction_id,
                d.amount                   AS amount,
                d.date                     AS donation_date,
                d.status                   AS donation_status,
                d.recipient_committee_name AS recipient_committee_name,
                d.recipient_candidate_name AS recipient_candidate_name,
                bs.bill_id                 AS bill_id,
                bs.role                    AS sponsor_role,
                l.bioguide_id              AS legislator_bioguide,
                l.full_name                AS legislator_name,
                l.current_party            AS legislator_party,
                l.current_state            AS legislator_state
            FROM master.donations d
            JOIN legislator_fec_ids x ON x.fec_candidate_id = d.recipient_candidate_id
            JOIN legislators l        ON l.bioguide_id = x.bioguide_id
            JOIN bill_sponsors bs     ON bs.bioguide_id = l.bioguide_id
            LEFT JOIN master.entities e ON e.slug = d.entity_slug
            WHERE d.status IN ({status_ph})
              AND bs.bill_id IN ({bill_ph})
            ORDER BY d.amount DESC, d.date
        """
        cur = conn.execute(sql, (*statuses, *bill_ids))
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def committee_donation_rows(
    *,
    bill_ids: list[str],
    master_db: Path = MASTER_DB,
    leg_db: Path = LEGISLATION_DB,
    statuses: tuple[str, ...] = ("CONFIRMED", "PROBABLE"),
) -> list[dict]:
    """Owner donations to CURRENT members of a committee any of `bill_ids` was
    referred to — the "owner money met the committee that holds this bill"
    surface that sponsorship alone misses. One row per (donation, committee-member).

    HONESTY GUARD: committee membership is the current-congress snapshot only, so
    this join is restricted to bills in that same congress (`b.congress =
    c.congress`). A donation to a present-day committee member is never tied to a
    historical bill whose committee had different members.

    One row per (donation, committee): when several of `bill_ids` share a committee
    of referral, the matching bills are aggregated into `bill_ids` rather than
    emitting a duplicate row per bill — so a single gift to a committee member is
    never counted once per bill it happens to gate. Read-only.
    """
    if not bill_ids:
        return []
    with legislation_db.connect(leg_db) as conn:
        legislation_db.attach_for_join(conn, master_db=master_db)
        status_ph = ",".join("?" for _ in statuses)
        bill_ph = ",".join("?" for _ in bill_ids)
        sql = f"""
            SELECT
                d.entity_slug              AS owner_slug,
                e.name                     AS owner_name,
                e.team                     AS owner_team,
                d.transaction_id           AS transaction_id,
                d.amount                   AS amount,
                d.date                     AS donation_date,
                d.status                   AS donation_status,
                d.recipient_committee_name AS recipient_committee_name,
                d.recipient_candidate_name AS recipient_candidate_name,
                GROUP_CONCAT(DISTINCT bc.bill_id) AS bill_ids,
                c.thomas_id                AS committee_id,
                c.name                     AS committee_name,
                cm.title                   AS member_title,
                l.bioguide_id              AS legislator_bioguide,
                l.full_name                AS legislator_name,
                l.current_party            AS legislator_party,
                l.current_state            AS legislator_state
            FROM master.donations d
            JOIN legislator_fec_ids x  ON x.fec_candidate_id = d.recipient_candidate_id
            JOIN legislators l         ON l.bioguide_id = x.bioguide_id
            JOIN committee_memberships cm ON cm.bioguide_id = l.bioguide_id
            JOIN committees c          ON c.thomas_id = cm.thomas_id
            JOIN bill_committees bc    ON bc.thomas_id = cm.thomas_id
            JOIN bills b               ON b.bill_id = bc.bill_id
            LEFT JOIN master.entities e ON e.slug = d.entity_slug
            WHERE d.status IN ({status_ph})
              AND bc.bill_id IN ({bill_ph})
              AND b.congress = c.congress
            GROUP BY d.transaction_id, c.thomas_id
            ORDER BY d.amount DESC, d.date
        """
        cur = conn.execute(sql, (*statuses, *bill_ids))
        rows = [dict(r) for r in cur.fetchall()]
    return rows


def summarize_by_owner(rows: list[dict]) -> list[dict]:
    """Neutral per-owner rollup of vote_donation_rows: totals + vote breakdown."""
    agg: dict[str, dict] = {}
    for r in rows:
        slug = r["owner_slug"]
        a = agg.setdefault(
            slug,
            {
                "owner_slug": slug,
                "owner_name": r.get("owner_name"),
                "owner_team": r.get("owner_team"),
                "n_donations": 0,
                "total_amount": 0.0,
                "legislators": set(),
                "to_yea_amount": 0.0,
                "to_nay_amount": 0.0,
            },
        )
        a["n_donations"] += 1
        a["total_amount"] += r["amount"] or 0.0
        a["legislators"].add(r["legislator_bioguide"])
        pos = (r.get("legislator_position") or "").lower()
        if pos == "yea":
            a["to_yea_amount"] += r["amount"] or 0.0
        elif pos == "nay":
            a["to_nay_amount"] += r["amount"] or 0.0
    out = []
    for a in agg.values():
        a["n_legislators"] = len(a.pop("legislators"))
        out.append(a)
    out.sort(key=lambda x: x["total_amount"], reverse=True)
    return out


def write_outputs(
    rows: list[dict],
    *,
    basename: str,
    meta: dict,
    out_dir: Path = REPORTS_DATA_DIR,
) -> dict:
    """Write rows to reports/data/<basename>.csv and .json (with a meta header).

    Returns the written paths. CSV is the flat neutral rows; JSON wraps them with
    a provenance/meta header so the output is self-describing and reproducible.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{basename}.csv"
    json_path = out_dir / f"{basename}.json"

    fieldnames = list(rows[0].keys()) if rows else []
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    payload = {
        "_meta": {
            "generated_at": _utc_now_iso(),
            "n_rows": len(rows),
            "neutrality_note": (
                "Neutral sourced facts only (GOVERNANCE.md §6). days_before_vote is "
                "arithmetic, not a causal claim. Interpretation lives in the brief."
            ),
            **meta,
        },
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path)}

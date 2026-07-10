"""Earmark/conduit dedup — the derived `counted` flag (schema v8, §1.1).

An earmarked contribution routed through a conduit (ActBlue/WinRed) is reported
to the FEC twice — the conduit's pass-through leg (is_individual=0) and the
ultimate recipient's own report (is_individual=1) — under distinct
transaction_ids. db.recompute_counted marks the pass-through leg counted=0 ONLY
when a countable sibling exists in the same (owner, donor, date, amount) group,
so genuine double-counts collapse while a lone conduit leg (the sole record of a
real gift) is preserved.
"""
from __future__ import annotations

import pytest

from scripts import db


def _row(txn: str, *, amount=5000.0, date="2018-11-06", recipient="ACTBLUE",
         is_individual=None, status="CONFIRMED", donor="ANGELOS, JOHN") -> dict:
    return {
        "transaction_id": txn,
        "entity_slug": "angelos-john-p",
        "entity_kind": "owner",
        "parent_owner_slug": None,
        "status": status,
        "status_reason": "two confirming signals",
        "signals_matched": "[]",
        "contributor_name_raw": donor,
        "contributor_employer_raw": "",
        "contributor_occupation_raw": "",
        "contributor_city": "Baltimore",
        "contributor_state": "MD",
        "contributor_zip": "21201",
        "recipient_committee_id": "C001",
        "recipient_committee_name": recipient,
        "recipient_candidate_id": "",
        "recipient_candidate_name": "",
        "recipient_party": "DEM",
        "recipient_office": None,
        "amount": amount,
        "date": date,
        "election_cycle": 2018,
        "report_type": None,
        "filing_id": "F100",
        "raw_payload_path": "data/raw/angelos-john-p/x.json",
        "ingested_at": "2026-05-28T00:00:00Z",
        "is_individual": is_individual,
    }


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test.db"
    db.init(p)
    return p


def _counted(conn, txn) -> int:
    return conn.execute(
        "SELECT counted FROM donations WHERE transaction_id = ?", (txn,)
    ).fetchone()[0]


def test_v8_columns_exist(db_path):
    with db.connect(db_path) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(donations)")}
    assert {"memo_code", "memo_text", "is_individual", "counted"} <= cols


def test_paired_conduit_leg_excluded(db_path):
    """Conduit pass-through (is_individual=0) + recipient leg (is_individual=1),
    same donor/date/amount → the conduit leg is marked counted=0, recipient kept."""
    with db.connect(db_path) as conn:
        db.insert_donation(conn, _row("CONDUIT1", recipient="ACTBLUE", is_individual=0))
        db.insert_donation(conn, _row("RECIP1", recipient="DEMOCRACY SUMMER", is_individual=1))
        excluded = db.recompute_counted(conn, "angelos-john-p")
        assert excluded == 1
        assert _counted(conn, "CONDUIT1") == 0
        assert _counted(conn, "RECIP1") == 1
        # The counted SUM sees the contribution exactly once.
        total = conn.execute(
            "SELECT SUM(amount) FROM donations WHERE counted = 1 "
            "AND status IN ('CONFIRMED','PROBABLE')"
        ).fetchone()[0]
        assert total == 5000.0


def test_lone_conduit_leg_preserved(db_path):
    """A conduit leg (is_individual=0) with NO countable sibling is the sole record
    of a real gift and must stay counted=1 (never silently dropped)."""
    with db.connect(db_path) as conn:
        db.insert_donation(conn, _row("LONE1", recipient="WINRED",
                                      is_individual=0, amount=92800.0, date="2022-05-03"))
        excluded = db.recompute_counted(conn, "angelos-john-p")
        assert excluded == 0
        assert _counted(conn, "LONE1") == 1


def test_two_distinct_gifts_same_day_amount(db_path):
    """Two $5000 gifts on the same day, each earmarked to a different recipient
    (2 conduit + 2 recipient legs) → both conduit legs excluded, both recipient
    legs kept; the counted total is $10,000 (each gift counted once)."""
    with db.connect(db_path) as conn:
        db.insert_donation(conn, _row("C_A", recipient="ACTBLUE", is_individual=0))
        db.insert_donation(conn, _row("C_B", recipient="ACTBLUE", is_individual=0))
        db.insert_donation(conn, _row("R_A", recipient="DEMOCRACY SUMMER", is_individual=1))
        db.insert_donation(conn, _row("R_B", recipient="US CAMPAIGN FUND", is_individual=1))
        excluded = db.recompute_counted(conn, "angelos-john-p")
        assert excluded == 2
        total = conn.execute(
            "SELECT SUM(amount) FROM donations WHERE counted = 1 "
            "AND status IN ('CONFIRMED','PROBABLE')"
        ).fetchone()[0]
        assert total == 10000.0


def test_null_is_individual_sibling_treated_countable(db_path):
    """A conduit leg whose only sibling has unknown is_individual (NULL — raw
    payload gone) is still excluded: an unknown sibling is treated as countable,
    so a real recipient leg is never mistaken for a passthrough and lost."""
    with db.connect(db_path) as conn:
        db.insert_donation(conn, _row("CONDUIT2", recipient="ACTBLUE", is_individual=0))
        db.insert_donation(conn, _row("RECIP2", recipient="SOME PAC", is_individual=None))
        db.recompute_counted(conn, "angelos-john-p")
        assert _counted(conn, "CONDUIT2") == 0
        assert _counted(conn, "RECIP2") == 1


def test_recompute_is_idempotent(db_path):
    with db.connect(db_path) as conn:
        db.insert_donation(conn, _row("CONDUIT1", recipient="ACTBLUE", is_individual=0))
        db.insert_donation(conn, _row("RECIP1", recipient="DEMOCRACY SUMMER", is_individual=1))
        first = db.recompute_counted(conn, "angelos-john-p")
        second = db.recompute_counted(conn, "angelos-john-p")
        assert first == second == 1

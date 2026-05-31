"""Schema + helper tests for the Phase 3 legislation index (scripts/legislation_db.py).

Mirrors tests/test_db.py: temp DBs via tmp_path, idempotent init, version trail,
and the ATTACH-for-join helper that bridges legislation.db ↔ master.db.
"""
from __future__ import annotations

import sqlite3

import pytest

from scripts import db, legislation_db


@pytest.fixture
def leg_db(tmp_path):
    p = tmp_path / "legislation.db"
    legislation_db.init(p)
    return p


EXPECTED_TABLES = {
    "legislators",
    "legislator_fec_ids",
    "legislator_terms",
    "bills",
    "bill_sponsors",
    "votes",
    "vote_positions",
    "policy_events",
    "leg_schema_version",
}


def _tables(p) -> set[str]:
    with legislation_db.connect(p) as conn:
        return {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


class TestSchema:
    def test_creates_all_tables(self, leg_db):
        assert EXPECTED_TABLES.issubset(_tables(leg_db))

    def test_records_schema_version(self, leg_db):
        with legislation_db.connect(leg_db) as conn:
            v = conn.execute("SELECT MAX(version) AS v FROM leg_schema_version").fetchone()["v"]
        assert v == legislation_db.LEG_SCHEMA_VERSION

    def test_init_is_idempotent(self, leg_db):
        # Re-init must not error or add a duplicate version row.
        legislation_db.init(leg_db)
        legislation_db.init(leg_db)
        with legislation_db.connect(leg_db) as conn:
            n = conn.execute("SELECT COUNT(*) FROM leg_schema_version").fetchone()[0]
        assert n == 1

    def test_bills_carried_by_self_reference(self, leg_db):
        # A standalone bill whose text was carried by an omnibus vehicle.
        with legislation_db.connect(leg_db) as conn:
            conn.execute(
                "INSERT INTO bills (bill_id, congress, bill_type, number, "
                "mlb_issue_area, relevance_basis, refreshed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("115-hr-1625", 115, "hr", 1625, "minor_league_pay",
                 "carrier vehicle (Consolidated Appropriations Act, 2018)", "2026-05-31T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO bills (bill_id, congress, bill_type, number, "
                "carried_by_bill_id, mlb_issue_area, relevance_basis, refreshed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("115-hr-5580", 115, "hr", 5580, "115-hr-1625", "minor_league_pay",
                 "exempts MiLB players from FLSA §13(a)", "2026-05-31T00:00:00Z"),
            )
        with legislation_db.connect(leg_db) as conn:
            row = conn.execute(
                "SELECT carried_by_bill_id FROM bills WHERE bill_id='115-hr-5580'"
            ).fetchone()
        assert row["carried_by_bill_id"] == "115-hr-1625"

    def test_vote_positions_primary_key_dedups(self, leg_db):
        with legislation_db.connect(leg_db) as conn:
            conn.execute(
                "INSERT INTO votes (vote_id, chamber, refreshed_at) VALUES (?, ?, ?)",
                ("house-115-2-127", "house", "2026-05-31T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO vote_positions (vote_id, bioguide_id, position) VALUES (?, ?, ?)",
                ("house-115-2-127", "B000001", "Yea"),
            )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO vote_positions (vote_id, bioguide_id, position) VALUES (?, ?, ?)",
                    ("house-115-2-127", "B000001", "Nay"),
                )


class TestSnapshot:
    def test_snapshot_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(legislation_db, "SNAPSHOTS_DIR", tmp_path / "snaps")
        (tmp_path / "snaps").mkdir()
        assert legislation_db.snapshot("run-x", db_path=tmp_path / "nope.db") is None

    def test_snapshot_copies_when_present(self, leg_db, tmp_path, monkeypatch):
        snaps = tmp_path / "snaps"
        snaps.mkdir()
        monkeypatch.setattr(legislation_db, "SNAPSHOTS_DIR", snaps)
        out = legislation_db.snapshot("run-x", db_path=leg_db)
        assert out is not None and out.exists()


class TestAttachForJoin:
    def test_attach_master_enables_cross_db_query(self, tmp_path):
        # Build a tiny master.db with one candidate donation, and a legislation.db
        # whose crosswalk maps that FEC candidate id to a bioguide id. The ATTACH
        # join is the spine of the Phase 3 owner→donation→legislator chain.
        master = tmp_path / "master.db"
        db.init(master)
        with db.connect(master) as conn:
            conn.execute(
                "INSERT INTO donations (transaction_id, entity_slug, entity_kind, "
                "status, contributor_name_raw, recipient_committee_id, "
                "recipient_committee_name, recipient_candidate_id, amount, date, "
                "filing_id, raw_payload_path, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("T1", "owner-x", "owner", "CONFIRMED", "John Doe", "C1", "Cmte",
                 "H8XX00001", 2800.0, "2018-02-01", "F1", "data/raw/x.json",
                 "2026-05-31T00:00:00Z"),
            )

        leg = tmp_path / "legislation.db"
        legislation_db.init(leg)
        with legislation_db.connect(leg) as conn:
            conn.execute(
                "INSERT INTO legislators (bioguide_id, full_name, source, refreshed_at) "
                "VALUES (?, ?, ?, ?)",
                ("D000001", "Rep. Example", "congress-legislators", "2026-05-31T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO legislator_fec_ids (fec_candidate_id, bioguide_id) VALUES (?, ?)",
                ("H8XX00001", "D000001"),
            )

        with legislation_db.connect(leg) as conn:
            legislation_db.attach_for_join(conn, master_db=master)
            rows = conn.execute(
                """
                SELECT d.transaction_id, l.full_name
                FROM master.donations d
                JOIN legislator_fec_ids x ON x.fec_candidate_id = d.recipient_candidate_id
                JOIN legislators l ON l.bioguide_id = x.bioguide_id
                """
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["full_name"] == "Rep. Example"

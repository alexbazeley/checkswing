"""Tests for the neutral owner→donation→legislator→vote join (scripts/policy_join.py)."""
from __future__ import annotations

import json

from scripts import db, legislation_db
from scripts.policy_join import (
    _days_before,
    committee_donation_rows,
    sponsor_donation_rows,
    summarize_by_owner,
    vote_donation_rows,
    write_outputs,
)


def _build(tmp_path):
    master = tmp_path / "master.db"
    db.init(master)
    with db.connect(master) as conn:
        conn.execute(
            "INSERT INTO entities (slug, kind, name, team, yaml_path, yaml_sha256, refreshed_at) "
            "VALUES ('owner-x','owner','Owner X','Test Team','owners/x.yaml','abc','2026-05-31T00:00:00Z')"
        )
        for txn, cid, amt, ddate in [
            ("T1", "H_REP1", 5000.0, "2018-01-01"),  # 80 days before the 2018-03-22 House vote
            ("T2", "S_SEN1", 2000.0, "2019-06-01"),  # after the Senate vote
        ]:
            conn.execute(
                "INSERT INTO donations (transaction_id, entity_slug, entity_kind, status, "
                "contributor_name_raw, recipient_committee_id, recipient_committee_name, "
                "recipient_candidate_id, recipient_candidate_name, amount, date, filing_id, "
                "raw_payload_path, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (txn, "owner-x", "owner", "CONFIRMED", "Owner X", "C1", "Cmte", cid,
                 "Recip", amt, ddate, "F1", "data/raw/x.json", "2026-05-31T00:00:00Z"),
            )

    leg = tmp_path / "legislation.db"
    legislation_db.init(leg)
    with legislation_db.connect(leg) as conn:
        for bio, fec, name, party, state in [
            ("R000001", "H_REP1", "Rep One", "Democrat", "CA"),
            ("S000001", "S_SEN1", "Sen One", "Republican", "TX"),
        ]:
            conn.execute(
                "INSERT INTO legislators (bioguide_id, full_name, current_party, current_state, source, refreshed_at) "
                "VALUES (?,?,?,?,?,?)",
                (bio, name, party, state, "test", "2026-05-31T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO legislator_fec_ids (fec_candidate_id, bioguide_id) VALUES (?,?)",
                (fec, bio),
            )
        conn.execute(
            "INSERT INTO bills (bill_id, congress, bill_type, number, mlb_issue_area, "
            "relevance_basis, refreshed_at) VALUES "
            "('115-hr-1625',115,'hr',1625,'minor_league_pay','carrier','2026-05-31T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO bill_sponsors (bill_id, bioguide_id, role) VALUES ('115-hr-1625','R000001','cosponsor')"
        )
        for vid, chamber, vdate in [
            ("house-115-2-127", "house", "2018-03-22"),
            ("senate-115-2-63", "senate", "2018-03-23"),
        ]:
            conn.execute(
                "INSERT INTO votes (vote_id, bill_id, chamber, vote_date, result, refreshed_at) "
                "VALUES (?,?,?,?,?,?)",
                (vid, "115-hr-1625", chamber, vdate, "Passed", "2026-05-31T00:00:00Z"),
            )
        conn.execute(
            "INSERT INTO vote_positions (vote_id, bioguide_id, position) VALUES ('house-115-2-127','R000001','Yea')"
        )
        conn.execute(
            "INSERT INTO vote_positions (vote_id, bioguide_id, position) VALUES ('senate-115-2-63','S000001','Nay')"
        )

        # ── Committee join fixtures ──────────────────────────────────────────
        # Current-congress (119) committee snapshot: House Ways & Means (HSWM),
        # with Rep One (R000001, who received donation T1) as a member.
        conn.execute(
            "INSERT INTO committees (thomas_id, congress, chamber, name, source, refreshed_at) "
            "VALUES ('HSWM',119,'house','House Committee on Ways and Means','test','2026-05-31T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO committee_memberships (thomas_id, bioguide_id, title) "
            "VALUES ('HSWM','R000001','Member')"
        )
        # A current-congress (119) bill referred to HSWM → SHOULD join.
        conn.execute(
            "INSERT INTO bills (bill_id, congress, bill_type, number, mlb_issue_area, "
            "relevance_basis, refreshed_at) VALUES "
            "('119-hr-9',119,'hr',9,'stadium_financing','current','2026-05-31T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO bill_committees (bill_id, system_code, thomas_id, chamber, name) "
            "VALUES ('119-hr-9','hswm00','HSWM','house','House Committee on Ways and Means')"
        )
        # A SECOND current-congress bill referred to the SAME committee (HSWM).
        # A single gift to a HSWM member must not be counted once per bill.
        conn.execute(
            "INSERT INTO bills (bill_id, congress, bill_type, number, mlb_issue_area, "
            "relevance_basis, refreshed_at) VALUES "
            "('119-hr-10',119,'hr',10,'stadium_financing','current','2026-05-31T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO bill_committees (bill_id, system_code, thomas_id, chamber, name) "
            "VALUES ('119-hr-10','hswm00','HSWM','house','House Committee on Ways and Means')"
        )
        # The HISTORICAL (115) bill is ALSO referred to HSWM — but must NOT join
        # against the current (119) membership snapshot (the honesty guard).
        conn.execute(
            "INSERT INTO bill_committees (bill_id, system_code, thomas_id, chamber, name) "
            "VALUES ('115-hr-1625','hswm00','HSWM','house','House Committee on Ways and Means')"
        )
    return master, leg


class TestDaysBefore:
    def test_positive_when_before(self):
        assert _days_before("2018-01-01", "2018-03-22") == 80

    def test_negative_when_after(self):
        assert _days_before("2019-06-01", "2018-03-23") < 0

    def test_none_on_bad_dates(self):
        assert _days_before(None, "2018-03-22") is None


class TestVoteJoin:
    def test_rows_and_day_delta(self, tmp_path):
        master, leg = _build(tmp_path)
        rows = vote_donation_rows(bill_ids=["115-hr-1625"], master_db=master, leg_db=leg)
        assert len(rows) == 2
        by_txn = {r["transaction_id"]: r for r in rows}
        assert by_txn["T1"]["legislator_position"] == "Yea"
        assert by_txn["T1"]["days_before_vote"] == 80
        assert by_txn["T1"]["owner_name"] == "Owner X"  # entities join
        assert by_txn["T2"]["legislator_position"] == "Nay"
        assert by_txn["T2"]["days_before_vote"] < 0  # donation after the vote

    def test_summarize_splits_yea_nay(self, tmp_path):
        master, leg = _build(tmp_path)
        rows = vote_donation_rows(bill_ids=["115-hr-1625"], master_db=master, leg_db=leg)
        summary = summarize_by_owner(rows)
        assert len(summary) == 1
        s = summary[0]
        assert s["total_amount"] == 7000.0
        assert s["to_yea_amount"] == 5000.0
        assert s["to_nay_amount"] == 2000.0
        assert s["n_legislators"] == 2

    def test_empty_bill_ids(self, tmp_path):
        master, leg = _build(tmp_path)
        assert vote_donation_rows(bill_ids=[], master_db=master, leg_db=leg) == []


class TestSponsorJoin:
    def test_donation_to_cosponsor(self, tmp_path):
        master, leg = _build(tmp_path)
        rows = sponsor_donation_rows(bill_ids=["115-hr-1625"], master_db=master, leg_db=leg)
        assert len(rows) == 1
        assert rows[0]["sponsor_role"] == "cosponsor"
        assert rows[0]["legislator_name"] == "Rep One"


class TestCommitteeJoin:
    def test_current_congress_bill_joins_committee_member(self, tmp_path):
        master, leg = _build(tmp_path)
        rows = committee_donation_rows(bill_ids=["119-hr-9"], master_db=master, leg_db=leg)
        # Rep One sits on Ways & Means and received donation T1 ($5000).
        assert len(rows) == 1
        r = rows[0]
        assert r["owner_slug"] == "owner-x"
        assert r["bill_ids"] == "119-hr-9"
        assert r["committee_id"] == "HSWM"
        assert r["legislator_name"] == "Rep One"
        assert r["amount"] == 5000.0

    def test_bills_sharing_a_committee_do_not_double_count(self, tmp_path):
        """119-hr-9 and 119-hr-10 are both referred to Ways & Means. A single
        $5000 gift to a W&M member must produce ONE row (not one per bill), with
        both bills aggregated into bill_ids."""
        master, leg = _build(tmp_path)
        rows = committee_donation_rows(
            bill_ids=["119-hr-9", "119-hr-10"], master_db=master, leg_db=leg
        )
        assert len(rows) == 1
        assert rows[0]["amount"] == 5000.0  # NOT $10,000
        assert set(rows[0]["bill_ids"].split(",")) == {"119-hr-9", "119-hr-10"}

    def test_historical_bill_does_not_join_current_membership(self, tmp_path):
        """The honesty guard: a 115th-Congress bill referred to the same committee
        must NOT join against the current (119th) membership snapshot."""
        master, leg = _build(tmp_path)
        rows = committee_donation_rows(bill_ids=["115-hr-1625"], master_db=master, leg_db=leg)
        assert rows == []

    def test_mixed_set_only_returns_current(self, tmp_path):
        master, leg = _build(tmp_path)
        rows = committee_donation_rows(
            bill_ids=["119-hr-9", "115-hr-1625"], master_db=master, leg_db=leg
        )
        assert {r["bill_ids"] for r in rows} == {"119-hr-9"}

    def test_empty_bill_ids(self, tmp_path):
        master, leg = _build(tmp_path)
        assert committee_donation_rows(bill_ids=[], master_db=master, leg_db=leg) == []


class TestWriteOutputs:
    def test_writes_csv_and_json(self, tmp_path):
        master, leg = _build(tmp_path)
        rows = vote_donation_rows(bill_ids=["115-hr-1625"], master_db=master, leg_db=leg)
        out = write_outputs(rows, basename="test-join", meta={"join": "x"}, out_dir=tmp_path / "rd")
        assert (tmp_path / "rd" / "test-join.csv").exists()
        payload = json.loads((tmp_path / "rd" / "test-join.json").read_text())
        assert payload["_meta"]["n_rows"] == 2
        assert "neutrality_note" in payload["_meta"]
        assert len(payload["rows"]) == 2

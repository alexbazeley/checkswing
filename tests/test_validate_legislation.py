"""Tests for the §5.6 legislation DB-vs-YAML validator additions."""
from __future__ import annotations

import sqlite3

import pytest

from scripts import legislation_db
from scripts.validate_legislation import validate_legislation_db


@pytest.fixture
def leg_env(tmp_path):
    db_path = tmp_path / "legislation.db"
    legislation_db.init(db_path)
    bills_dir = tmp_path / "bills"
    bills_dir.mkdir()
    return db_path, bills_dir


def _add_bill(db_path, bill_id):
    cong, btype, num = bill_id.split("-")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO bills (bill_id, congress, bill_type, number, enacted, "
            "mlb_issue_area, relevance_basis, refreshed_at) "
            "VALUES (?, ?, ?, ?, 0, 'x', 'x', 't')",
            (bill_id, int(cong), btype, int(num)),
        )


def _add_vote(db_path, vote_id, chamber, bill_id, n_positions):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO votes (vote_id, bill_id, chamber, congress, session, roll_number, refreshed_at) "
            "VALUES (?, ?, ?, 1, 1, 1, 't')",
            (vote_id, bill_id, chamber),
        )
        for i in range(n_positions):
            conn.execute(
                "INSERT INTO vote_positions (vote_id, bioguide_id, position) VALUES (?, ?, 'Yea')",
                (vote_id, f"B{i:04d}"),
            )


def _add_vote_with_raw(db_path, vote_id, chamber, bill_id, congress, session, raw_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO votes (vote_id, bill_id, chamber, congress, session, roll_number, "
            "raw_payload_path, refreshed_at) VALUES (?, ?, ?, ?, ?, 1, ?, 't')",
            (vote_id, bill_id, chamber, congress, session, str(raw_path) if raw_path else None),
        )


def _house_xml(congress, session_ord, roll=1):
    return (
        f"<rollcall-vote><vote-metadata><congress>{congress}</congress>"
        f"<session>{session_ord}</session><rollcall-num>{roll}</rollcall-num>"
        f"</vote-metadata></rollcall-vote>"
    )


def _senate_xml(congress, session):
    return f"<roll_call_vote><congress>{congress}</congress><session>{session}</session></roll_call_vote>"


def _warns(results, ident):
    for r in results:
        if r.ident == ident:
            return r.warnings
    return None


def _errs(results, ident):
    for r in results:
        if r.ident == ident:
            return r.errors
    return None


class TestBillParity:
    def test_orphaned_db_bill_warns(self, leg_env):
        db_path, bills_dir = leg_env
        _add_bill(db_path, "119-s-1")            # has a YAML
        _add_bill(db_path, "999-hr-42")          # ORPHAN — no YAML
        (bills_dir / "119-s-1.yaml").write_text("bill_id: 119-s-1\n", encoding="utf-8")

        warns = _warns(validate_legislation_db(bills_dir, db_path), "db-yaml-parity")
        assert any("999-hr-42" in w for w in warns)
        assert not any("119-s-1" in w for w in warns)

    def test_all_bills_have_yaml_no_warn(self, leg_env):
        db_path, bills_dir = leg_env
        _add_bill(db_path, "119-s-1")
        (bills_dir / "119-s-1.yaml").write_text("bill_id: 119-s-1\n", encoding="utf-8")
        assert _warns(validate_legislation_db(bills_dir, db_path), "db-yaml-parity") == []


class TestVoteCompleteness:
    def test_incomplete_senate_vote_warns(self, leg_env):
        db_path, bills_dir = leg_env
        _add_vote(db_path, "senate-102-2-111", "senate", "102-s-474", 55)   # PASPA-like
        _add_vote(db_path, "senate-115-2-63", "senate", "115-hr-1625", 100)  # complete
        warns = _warns(validate_legislation_db(bills_dir, db_path), "vote-completeness")
        assert any("senate-102-2-111" in w and "55" in w for w in warns)
        assert not any("senate-115-2-63" in w for w in warns)

    def test_full_house_vote_no_warn(self, leg_env):
        db_path, bills_dir = leg_env
        _add_vote(db_path, "house-115-2-127", "house", "115-hr-1625", 430)
        assert _warns(validate_legislation_db(bills_dir, db_path), "vote-completeness") == []


class TestVoteXmlCongressSession:
    def test_matching_house_xml_no_error(self, leg_env, tmp_path):
        db_path, bills_dir = leg_env
        raw = tmp_path / "house.xml"
        raw.write_text(_house_xml(115, "2nd", 127), encoding="utf-8")
        _add_vote_with_raw(db_path, "house-115-2-127", "house", "115-hr-1625", 115, 2, raw)
        assert _errs(validate_legislation_db(bills_dir, db_path), "vote-xml-congress-session") == []

    def test_mismatched_congress_errors(self, leg_env, tmp_path):
        db_path, bills_dir = leg_env
        raw = tmp_path / "house.xml"
        raw.write_text(_house_xml(115, "2nd", 127), encoding="utf-8")  # XML says 115
        _add_vote_with_raw(db_path, "house-116-2-127", "house", "115-hr-1625", 116, 2, raw)  # YAML says 116
        errs = _errs(validate_legislation_db(bills_dir, db_path), "vote-xml-congress-session")
        assert any("congress 116" in e and "115" in e for e in errs)

    def test_mismatched_session_errors(self, leg_env, tmp_path):
        db_path, bills_dir = leg_env
        raw = tmp_path / "sen.xml"
        raw.write_text(_senate_xml(102, 2), encoding="utf-8")  # XML says session 2
        _add_vote_with_raw(db_path, "senate-102-1-111", "senate", "102-s-474", 102, 1, raw)  # YAML says 1
        errs = _errs(validate_legislation_db(bills_dir, db_path), "vote-xml-congress-session")
        assert any("session 1" in e and "2" in e for e in errs)

    def test_missing_raw_file_skipped(self, leg_env, tmp_path):
        db_path, bills_dir = leg_env
        # raw_payload_path points nowhere (e.g. CI, where data/raw is gitignored).
        _add_vote_with_raw(db_path, "house-116-2-127", "house", "115-hr-1625", 116, 2,
                           tmp_path / "does-not-exist.xml")
        assert _errs(validate_legislation_db(bills_dir, db_path), "vote-xml-congress-session") == []

    def test_null_raw_path_skipped(self, leg_env):
        db_path, bills_dir = leg_env
        _add_vote_with_raw(db_path, "house-116-2-127", "house", "115-hr-1625", 116, 2, None)
        assert _errs(validate_legislation_db(bills_dir, db_path), "vote-xml-congress-session") == []


def test_missing_db_returns_empty(tmp_path):
    # No legislation.db yet → skipped cleanly, no crash.
    assert validate_legislation_db(tmp_path / "bills", tmp_path / "nope.db") == []

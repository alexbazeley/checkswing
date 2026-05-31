"""Tests for Phase 3 roll-call vote parsing + ingest (no network)."""
from __future__ import annotations

from pathlib import Path

from scripts import legislation_db
from scripts.fetch_votes import parse_house_vote, parse_senate_vote
from scripts.ingest_legislation import ingest_votes


HOUSE_XML = """<?xml version="1.0"?>
<rollcall-vote>
  <vote-metadata>
    <congress>115</congress>
    <session>2nd</session>
    <rollcall-num>127</rollcall-num>
    <legis-num>H R 1625</legis-num>
    <vote-question>On Passage</vote-question>
    <vote-result>Passed</vote-result>
    <action-date>22-Mar-2018</action-date>
  </vote-metadata>
  <vote-data>
    <recorded-vote><legislator name-id="A000001">Rep A</legislator><vote>Yea</vote></recorded-vote>
    <recorded-vote><legislator name-id="B000002">Rep B</legislator><vote>Nay</vote></recorded-vote>
  </vote-data>
</rollcall-vote>
"""

SENATE_XML = """<?xml version="1.0"?>
<roll_call_vote>
  <congress>115</congress>
  <session>2</session>
  <vote_number>00063</vote_number>
  <vote_date>March 23, 2018, 12:34 AM</vote_date>
  <question>On Passage of the Bill</question>
  <vote_result>Motion Agreed to</vote_result>
  <document><document_name>H.R. 1625</document_name></document>
  <members>
    <member><lis_member_id>S289</lis_member_id><vote_cast>Yea</vote_cast></member>
    <member><lis_member_id>S307</lis_member_id><vote_cast>Nay</vote_cast></member>
    <member><lis_member_id>S999</lis_member_id><vote_cast>Yea</vote_cast></member>
  </members>
</roll_call_vote>
"""


class FakeFetcher:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path

    def fetch_house_vote(self, year, roll):
        p = self.tmp / f"house-{year}-{roll}.xml"
        p.write_text(HOUSE_XML, encoding="utf-8")
        return HOUSE_XML, p

    def fetch_senate_vote(self, congress, session, roll):
        p = self.tmp / f"senate-{congress}-{session}-{roll}.xml"
        p.write_text(SENATE_XML, encoding="utf-8")
        return SENATE_XML, p


class TestParseHouse:
    def test_metadata_and_date(self):
        meta, positions = parse_house_vote(HOUSE_XML)
        assert meta["chamber"] == "house"
        assert meta["congress"] == 115
        assert meta["session"] == 2
        assert meta["roll_number"] == 127
        assert meta["vote_date"] == "2018-03-22"
        assert meta["result"] == "Passed"

    def test_positions_bioguide_keyed(self):
        _, positions = parse_house_vote(HOUSE_XML)
        assert {p["bioguide_id"]: p["position"] for p in positions} == {
            "A000001": "Yea",
            "B000002": "Nay",
        }


class TestParseSenate:
    def test_metadata_and_date(self):
        meta, positions = parse_senate_vote(SENATE_XML)
        assert meta["chamber"] == "senate"
        assert meta["session"] == 2
        assert meta["roll_number"] == 63
        assert meta["vote_date"] == "2018-03-23"

    def test_positions_lis_keyed(self):
        _, positions = parse_senate_vote(SENATE_XML)
        assert {p["lis_member_id"]: p["position"] for p in positions} == {
            "S289": "Yea",
            "S307": "Nay",
            "S999": "Yea",
        }


class TestSchemaV2:
    def test_legislators_has_lis_id(self, tmp_path):
        p = tmp_path / "legislation.db"
        legislation_db.init(p)
        with legislation_db.connect(p) as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(legislators)")}
            v = conn.execute("SELECT MAX(version) v FROM leg_schema_version").fetchone()["v"]
        assert "lis_id" in cols
        assert v == legislation_db.LEG_SCHEMA_VERSION >= 2


class TestIngestVotes:
    def _seed_legislators(self, leg):
        # Two of three senators carry a crosswalk lis_id; S999 does not.
        with legislation_db.connect(leg) as conn:
            for bio, lis in [("S000289", "S289"), ("S000307", "S307")]:
                conn.execute(
                    "INSERT INTO legislators (bioguide_id, lis_id, full_name, source, refreshed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (bio, lis, f"Sen {bio}", "test", "2026-05-31T00:00:00Z"),
                )

    def test_house_and_senate_ingest(self, tmp_path):
        leg = tmp_path / "legislation.db"
        legislation_db.init(leg)
        self._seed_legislators(leg)
        specs = [
            {"bill_id": "115-hr-1625", "chamber": "house", "congress": 115, "session": 2, "roll": 127, "year": 2018},
            {"bill_id": "115-hr-1625", "chamber": "senate", "congress": 115, "session": 2, "roll": 63},
        ]
        counts = ingest_votes(specs, FakeFetcher(tmp_path), db_path=leg)
        assert counts["votes"] == 2
        # House: 2 positions; Senate: 2 mapped (S289,S307), 1 unmapped (S999).
        assert counts["positions"] == 4
        assert counts["senate_unmapped"] == 1

        with legislation_db.connect(leg) as conn:
            vote_ids = {r["vote_id"] for r in conn.execute("SELECT vote_id FROM votes")}
            assert vote_ids == {"house-115-2-127", "senate-115-2-63"}
            # Senate positions stored bioguide-keyed via the lis→bioguide map.
            senate_bios = {
                r["bioguide_id"]
                for r in conn.execute(
                    "SELECT bioguide_id FROM vote_positions WHERE vote_id='senate-115-2-63'"
                )
            }
            assert senate_bios == {"S000289", "S000307"}  # S999 dropped (unmapped)

    def test_idempotent_reingest(self, tmp_path):
        leg = tmp_path / "legislation.db"
        legislation_db.init(leg)
        self._seed_legislators(leg)
        specs = [{"bill_id": "115-hr-1625", "chamber": "house", "congress": 115, "session": 2, "roll": 127, "year": 2018}]
        ingest_votes(specs, FakeFetcher(tmp_path), db_path=leg)
        ingest_votes(specs, FakeFetcher(tmp_path), db_path=leg)
        with legislation_db.connect(leg) as conn:
            assert conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM vote_positions").fetchone()[0] == 2

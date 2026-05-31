"""Tests for Phase 3 bill enrichment: parse, validate, and ingest (no network)."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import legislation_db
from scripts.fetch_congress import parse_bill, parse_sponsors
from scripts.ingest_legislation import ingest_bills
from scripts.validate_legislation import validate_all, validate_bill_file, validate_issues_file


# ── Fixture Congress.gov responses ──────────────────────────────────────────
RAW_OMNIBUS = {
    "bill": {
        "congress": 115,
        "type": "HR",
        "number": "1625",
        "title": "Consolidated Appropriations Act, 2018",
        "introducedDate": "2017-03-20",
        "latestAction": {"actionDate": "2018-03-23", "text": "Became Public Law No: 115-141."},
        "laws": [{"type": "Public Law", "number": "115-141"}],
        "sponsors": [{"bioguideId": "R000487", "fullName": "Rep. Royce, Edward R."}],
    }
}
COSPONSORS_OMNIBUS = [
    {"bioguideId": "A000001"},
    {"bioguideId": "B000002"},
    {"bioguideId": "R000487"},  # also the sponsor — must not double-count
]


class FakeClient:
    """Stand-in for CongressClient: serves fixtures, records calls, writes a raw file."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path

    def fetch_bill(self, congress, bill_type, number):
        p = self.tmp / f"raw-bill-{congress}-{bill_type}-{number}.json"
        p.write_text("{}", encoding="utf-8")
        return RAW_OMNIBUS, p

    def fetch_cosponsors(self, congress, bill_type, number):
        p = self.tmp / f"raw-cos-{congress}-{bill_type}-{number}.json"
        p.write_text("[]", encoding="utf-8")
        return COSPONSORS_OMNIBUS, p


class TestParseBill:
    def test_extracts_core_fields(self):
        row = parse_bill(RAW_OMNIBUS, raw_payload_path="data/raw/legislation/x.json")
        assert row["congress"] == 115
        assert row["bill_type"] == "hr"
        assert row["number"] == 1625
        assert row["enacted"] == 1  # has laws
        assert row["title"] == "Consolidated Appropriations Act, 2018"
        assert row["latest_action_date"] == "2018-03-23"

    def test_builds_web_url(self):
        row = parse_bill(RAW_OMNIBUS)
        assert row["congress_dot_gov_url"] == (
            "https://www.congress.gov/bill/115th-congress/house-bill/1625"
        )

    def test_not_enacted_when_no_laws(self):
        raw = {"bill": {"congress": 114, "type": "HR", "number": "5580", "title": "X"}}
        assert parse_bill(raw)["enacted"] == 0


class TestParseSponsors:
    def test_sponsor_and_cosponsors_deduped(self):
        rows = parse_sponsors(RAW_OMNIBUS, COSPONSORS_OMNIBUS)
        roles = {(r["bioguide_id"], r["role"]) for r in rows}
        assert ("R000487", "sponsor") in roles
        assert ("A000001", "cosponsor") in roles
        # The sponsor appearing again in the cosponsor list is not also a cosponsor.
        assert ("R000487", "cosponsor") not in roles
        assert len(rows) == 3


class TestValidateLegislation:
    def _spec_yaml(self) -> str:
        return (
            "bill_id: 115-hr-1625\ncongress: 115\nbill_type: hr\nnumber: 1625\n"
            "mlb_issue_area: minor_league_pay\nrelevance_basis: 'carrier vehicle'\n"
            "sources:\n  - description: d\n    url: http://x\n"
            "change_log:\n  - date: 2026-05-31\n    change: indexed\n"
        )

    def test_good_bill_passes(self, tmp_path):
        p = tmp_path / "115-hr-1625.yaml"
        p.write_text(self._spec_yaml(), encoding="utf-8")
        res = validate_bill_file(p, {"minor_league_pay"}, {"115-hr-1625"})
        assert res.ok, res.errors

    def test_bill_id_must_match_filename_and_parts(self, tmp_path):
        p = tmp_path / "999-hr-1.yaml"
        p.write_text(self._spec_yaml(), encoding="utf-8")  # bill_id says 115-hr-1625
        res = validate_bill_file(p, {"minor_league_pay"}, set())
        assert not res.ok
        assert any("filename stem" in e for e in res.errors)

    def test_unknown_issue_area_fails(self, tmp_path):
        p = tmp_path / "115-hr-1625.yaml"
        p.write_text(self._spec_yaml(), encoding="utf-8")
        res = validate_bill_file(p, {"antitrust_exemption"}, {"115-hr-1625"})
        assert not res.ok
        assert any("mlb_issue_area" in e for e in res.errors)

    def test_repo_legislation_validates(self):
        # The actual checked-in legislation/ must pass.
        results = validate_all()
        assert all(r.ok for r in results), [
            (r.yaml_path.name, r.errors) for r in results if not r.ok
        ]

    def test_issues_file_present(self):
        assert validate_issues_file().ok


class TestIngestBills:
    def test_ingest_writes_bills_and_sponsors(self, tmp_path):
        leg = tmp_path / "legislation.db"
        legislation_db.init(leg)
        specs = [
            {
                "bill_id": "115-hr-1625",
                "congress": 115,
                "bill_type": "hr",
                "number": 1625,
                "mlb_issue_area": "minor_league_pay",
                "relevance_basis": "carrier vehicle for SAPA",
                "relevance_source_url": "http://x",
                "carried_by_bill_id": None,
            }
        ]
        counts = ingest_bills(specs, FakeClient(tmp_path), db_path=leg)
        assert counts["bills"] == 1
        assert counts["sponsors"] == 3
        assert counts["errors"] == []

        with legislation_db.connect(leg) as conn:
            b = conn.execute("SELECT * FROM bills WHERE bill_id='115-hr-1625'").fetchone()
            # Curated field preserved; API field enriched.
            assert b["mlb_issue_area"] == "minor_league_pay"
            assert b["relevance_basis"] == "carrier vehicle for SAPA"
            assert b["enacted"] == 1
            assert b["title"] == "Consolidated Appropriations Act, 2018"
            n_sp = conn.execute(
                "SELECT COUNT(*) FROM bill_sponsors WHERE bill_id='115-hr-1625'"
            ).fetchone()[0]
            assert n_sp == 3

    def test_ingest_is_idempotent_upsert(self, tmp_path):
        leg = tmp_path / "legislation.db"
        legislation_db.init(leg)
        specs = [
            {
                "bill_id": "115-hr-1625", "congress": 115, "bill_type": "hr",
                "number": 1625, "mlb_issue_area": "minor_league_pay",
                "relevance_basis": "x", "relevance_source_url": None,
                "carried_by_bill_id": None,
            }
        ]
        ingest_bills(specs, FakeClient(tmp_path), db_path=leg)
        ingest_bills(specs, FakeClient(tmp_path), db_path=leg)  # rerun
        with legislation_db.connect(leg) as conn:
            n_bills = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
            n_sp = conn.execute("SELECT COUNT(*) FROM bill_sponsors").fetchone()[0]
        assert n_bills == 1 and n_sp == 3  # no duplication

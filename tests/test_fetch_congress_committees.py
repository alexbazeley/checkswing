"""Tests for the pure parsers in scripts/fetch_congress_committees.py and the
Congress.gov bill-committee parser (no network)."""
from __future__ import annotations

from scripts.fetch_congress import _system_code_to_thomas_id, parse_bill_committees
from scripts.fetch_congress_committees import parse_committees, parse_memberships


_COMMITTEES_YAML = """
- type: senate
  name: Senate Committee on the Judiciary
  thomas_id: SSJU
  subcommittees:
    - name: Subcommittee on the Constitution
      thomas_id: "01"
- type: house
  name: House Committee on Ways and Means
  thomas_id: HSWM
- type: joint
  name: Joint Economic Committee
  thomas_id: JSEC
- name: No thomas id here
"""

_MEMBERSHIP_YAML = """
SSJU:
  - name: Sen A
    party: majority
    rank: 1
    title: Chair
    bioguide: A000001
  - name: Sen B
    party: minority
    rank: 1
    bioguide: B000002
SSJU01:        # subcommittee — must be skipped
  - name: Sen A
    bioguide: A000001
HSWM:
  - name: Rep C
    party: majority
    rank: 2
    bioguide: C000003
ZZZZ:          # committee not in committees-current — must be skipped
  - name: Ghost
    bioguide: X000009
"""


class TestParseCommittees:
    def test_only_full_committees_with_thomas_id(self):
        out = parse_committees(_COMMITTEES_YAML)
        assert set(out) == {"SSJU", "HSWM", "JSEC"}
        assert out["SSJU"]["chamber"] == "senate"
        assert out["HSWM"]["name"] == "House Committee on Ways and Means"
        assert out["JSEC"]["chamber"] == "joint"


class TestParseMemberships:
    def test_keeps_only_valid_full_committee_keys(self):
        valid = set(parse_committees(_COMMITTEES_YAML))  # SSJU, HSWM, JSEC
        rows = parse_memberships(_MEMBERSHIP_YAML, valid)
        codes = {r["thomas_id"] for r in rows}
        assert codes == {"SSJU", "HSWM"}          # SSJU01 (sub) and ZZZZ (unknown) dropped
        assert len(rows) == 3
        chair = next(r for r in rows if r["bioguide_id"] == "A000001")
        assert chair["title"] == "Chair"
        assert chair["rank"] == 1
        assert chair["party"] == "majority"

    def test_member_without_bioguide_skipped(self):
        rows = parse_memberships(
            "SSJU:\n  - name: No Bioguide\n    rank: 1\n", {"SSJU"}
        )
        assert rows == []


class TestSystemCodeMapping:
    def test_full_committee_code(self):
        assert _system_code_to_thomas_id("ssju00") == "SSJU"
        assert _system_code_to_thomas_id("hswm00") == "HSWM"

    def test_subcommittee_maps_to_parent(self):
        assert _system_code_to_thomas_id("ssju14") == "SSJU"

    def test_none_and_short(self):
        assert _system_code_to_thomas_id(None) is None
        assert _system_code_to_thomas_id("x") is None


class TestParseBillCommittees:
    def test_maps_system_codes_and_dedupes(self):
        raw = [
            {"systemCode": "ssju00", "chamber": "Senate", "name": "Judiciary Committee"},
            {"systemCode": "ssju00", "chamber": "Senate", "name": "Judiciary Committee"},  # dup
            {"systemCode": "hswm00", "chamber": "House", "name": "Ways and Means Committee"},
            {"name": "no system code"},  # skipped
        ]
        rows = parse_bill_committees(raw)
        assert len(rows) == 2
        by_sc = {r["system_code"]: r for r in rows}
        assert by_sc["ssju00"]["thomas_id"] == "SSJU"
        assert by_sc["ssju00"]["chamber"] == "senate"
        assert by_sc["hswm00"]["thomas_id"] == "HSWM"

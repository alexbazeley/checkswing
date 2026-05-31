"""Tests for the legislator crosswalk: parse, ingest, and donation coverage."""
from __future__ import annotations

from scripts import db, legislation_db
from scripts.fetch_legislators import _congress_from_start, parse_legislators
from scripts.ingest_legislation import donation_legislator_coverage, ingest_legislators


# Two legislators: one with FEC ids + house→senate terms, one with NO fec id.
FIXTURE_YAML = """
- id:
    bioguide: B000944
    icpsr: 29389
    govtrack: 400050
    opensecrets: N00003535
    fec:
    - H2OH13033
    - S6OH00163
  name:
    first: Sherrod
    last: Brown
    official_full: Sherrod Brown
  terms:
  - type: rep
    start: '1993-01-05'
    end: '1995-01-03'
    state: OH
    district: 13
    party: Democrat
  - type: sen
    start: '2007-01-04'
    end: '2013-01-03'
    state: OH
    party: Democrat
- id:
    bioguide: Z999999
    icpsr: 11111
  name:
    first: No
    last: Fec
    official_full: No Fec
  terms:
  - type: rep
    start: '1801-12-07'
    end: '1803-03-03'
    state: VA
    district: 5
    party: Republican
"""


class TestCongressDerivation:
    def test_odd_year_start(self):
        assert _congress_from_start("1993-01-05") == 103

    def test_even_year_appointment_rolls_back(self):
        # A 1994 mid-term appointment still belongs to the 103rd Congress.
        assert _congress_from_start("1994-06-01") == 103

    def test_first_congress(self):
        assert _congress_from_start("1789-03-04") == 1

    def test_none_and_garbage(self):
        assert _congress_from_start(None) is None
        assert _congress_from_start("x") is None


class TestParse:
    def test_only_with_fec_filters(self):
        legis, fec, terms = parse_legislators(FIXTURE_YAML, only_with_fec=True)
        bioguides = {row["bioguide_id"] for row in legis}
        assert bioguides == {"B000944"}  # Z999999 dropped (no fec id)

    def test_all_legislators_when_flag_off(self):
        legis, _, _ = parse_legislators(FIXTURE_YAML, only_with_fec=False)
        assert {row["bioguide_id"] for row in legis} == {"B000944", "Z999999"}

    def test_fec_ids_unioned(self):
        _, fec, _ = parse_legislators(FIXTURE_YAML, only_with_fec=True)
        pairs = {(r["fec_candidate_id"], r["bioguide_id"]) for r in fec}
        assert pairs == {("H2OH13033", "B000944"), ("S6OH00163", "B000944")}

    def test_current_party_state_from_last_term(self):
        legis, _, _ = parse_legislators(FIXTURE_YAML, only_with_fec=True)
        row = next(r for r in legis if r["bioguide_id"] == "B000944")
        assert row["current_party"] == "Democrat"
        assert row["current_state"] == "OH"

    def test_terms_mapped_to_chambers(self):
        _, _, terms = parse_legislators(FIXTURE_YAML, only_with_fec=True)
        chambers = sorted(t["chamber"] for t in terms)
        assert chambers == ["house", "senate"]


class TestIngest:
    def test_ingest_then_coverage(self, tmp_path):
        leg = tmp_path / "legislation.db"
        legislation_db.init(leg)
        legislators, fec_ids, terms = parse_legislators(FIXTURE_YAML, only_with_fec=True)
        counts = ingest_legislators(legislators, fec_ids, terms, db_path=leg)
        assert counts == {"legislators": 1, "fec_ids": 2, "terms": 2}

        # Build a master.db: one donation to a resolvable candidate, one not.
        master = tmp_path / "master.db"
        db.init(master)
        with db.connect(master) as conn:
            for txn, cid, name, amt in [
                ("T1", "H2OH13033", "BROWN, SHERROD", 2800.0),  # resolves
                ("T2", "H0XX99999", "STRANGER, AL", 500.0),     # does not
            ]:
                conn.execute(
                    "INSERT INTO donations (transaction_id, entity_slug, entity_kind, "
                    "status, contributor_name_raw, recipient_committee_id, "
                    "recipient_committee_name, recipient_candidate_id, "
                    "recipient_candidate_name, amount, date, filing_id, "
                    "raw_payload_path, ingested_at) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (txn, "owner-x", "owner", "CONFIRMED", "Owner X", "C1", "Cmte",
                     cid, name, amt, "2018-02-01", "F1", "data/raw/x.json",
                     "2026-05-31T00:00:00Z"),
                )

        cov = donation_legislator_coverage(master_db=master, leg_db=leg)
        assert cov["n_candidate_ids"] == 2
        assert cov["n_resolved"] == 1
        assert cov["n_unresolved"] == 1
        assert cov["pct_resolved"] == 50.0
        assert cov["top_unresolved"][0]["cid"] == "H0XX99999"

    def test_ingest_is_idempotent(self, tmp_path):
        leg = tmp_path / "legislation.db"
        legislation_db.init(leg)
        legislators, fec_ids, terms = parse_legislators(FIXTURE_YAML, only_with_fec=True)
        ingest_legislators(legislators, fec_ids, terms, db_path=leg)
        ingest_legislators(legislators, fec_ids, terms, db_path=leg)  # rerun
        with legislation_db.connect(leg) as conn:
            n_legis = conn.execute("SELECT COUNT(*) FROM legislators").fetchone()[0]
            n_fec = conn.execute("SELECT COUNT(*) FROM legislator_fec_ids").fetchone()[0]
        assert n_legis == 1 and n_fec == 2  # no duplication

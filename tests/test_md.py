"""Tests for the Maryland (MDCRIS) source — adapter + fetcher builders.

Fixtures are shaped from a live MDCRIS response captured 2026-07-19, not invented,
so the field names here are the ones the portal actually returns.
"""
from __future__ import annotations

import pytest

from scripts import fetch_md, md_adapter
from scripts.state_sources import REGISTRY, get_source


# One real Angelos row, trimmed to the fields the pipeline touches.
ROW = {
    "transactionId": 1268147,
    "transactionGuid": "db9ea6a0-1278-4073-a4d0-df44b0006523",
    "reportId": 23839,
    "reportGuid": "502af659-a823-4905-b1a6-a6b7c39450f4",
    "report": "2023 Annual",
    "filingEntityId": 13013301,
    "committeeName": "A Safer, Stronger Baltimore PAC",
    "committeeType": "Super PAC",
    "committeeTypeCode": "SPAC",
    "contributorName": "Angelos, John",
    "contributorType": "Individual",
    "contributorTypeCode": "TIND",
    "contributorAddressLine1": "333 West Camden Street",
    "contributorCity": "Baltimore",
    "contributorState": "MD",
    "contributorZipCode": "21201-2435",
    "transactionDate": "2022-07-18T00:00:00",
    "transactionAmount": 10000.0,
    "electionCycleId": 12023,
    "isReturn": False,
}


class TestParsing:
    def test_date_strips_the_time_component(self):
        assert md_adapter.parse_md_date("2022-07-18T00:00:00") == "2022-07-18"
        assert md_adapter.parse_md_date("") is None
        assert md_adapter.parse_md_date(None) is None

    def test_zip_plus_four_is_normalized_to_five(self):
        # Signals are matched on 5-digit ZIPs; MD files ZIP+4.
        assert md_adapter.normalize_zip("21201-2435") == "21201"
        assert md_adapter.normalize_zip("20852") == "20852"
        assert md_adapter.normalize_zip(None) == ""

    def test_cycle_comes_from_the_date_not_the_surrogate_id(self):
        # electionCycleId is 12023 — an internal surrogate, not a year.
        assert md_adapter.election_cycle(ROW, "2022-07-18") == 2022

    def test_name_splits_last_comma_first(self):
        assert md_adapter._split_name("Angelos, John") == ("Angelos", "John", "")
        assert md_adapter._split_name("Lerner, Mark Judy") == ("Lerner", "Mark", "Judy")
        # entity/hand-entered rows arrive without a comma
        assert md_adapter._split_name("The Baltimore Orioles L.P.")[0] == "L.P."


class TestClassifierRecord:
    def test_employer_and_occupation_are_always_empty(self):
        """MD collects neither. This is the defining property of the source."""
        rec = md_adapter.to_classifier_record(ROW)
        assert rec["contributor_employer"] == ""
        assert rec["contributor_occupation"] == ""

    def test_address_fields_carry_the_matching_burden(self):
        rec = md_adapter.to_classifier_record(ROW)
        assert rec["contributor_city"] == "Baltimore"
        assert rec["contributor_state"] == "MD"
        assert rec["contributor_zip"] == "21201"   # normalized, matchable
        assert rec["contributor_last_name"] == "Angelos"

    def test_individual_flag_distinguishes_people_from_entities(self):
        assert md_adapter.is_individual(ROW) is True
        assert md_adapter.is_individual({**ROW, "contributorTypeCode": "TENT",
                                         "contributorType": "Entity"}) is False


class TestRecipientType:
    def test_ballot_issue_maps_to_ballot_measure_not_committee(self):
        # Ballot-measure money is a first-class category (cf. Fisher/CA Prop 30);
        # it must not fall through to the generic 'committee'.
        assert md_adapter._recipient_type(
            {"committeeTypeCode": "BALC", "committeeType": "Ballot Issue"}) == "ballot_measure"

    def test_candidate_and_committee(self):
        assert md_adapter._recipient_type({"committeeType": "Candidate"}) == "candidate"
        assert md_adapter._recipient_type(ROW) == "committee"      # Super PAC
        assert md_adapter._recipient_type({}) is None


class TestIds:
    def test_prefers_stable_guids_over_version_scoped_ints(self):
        assert md_adapter.tran_id_of(ROW) == ROW["transactionGuid"]
        assert md_adapter.filing_id_of(ROW) == ROW["reportGuid"]

    def test_falls_back_when_guid_absent(self):
        assert md_adapter.tran_id_of({"transactionId": 7}) == "7"
        assert md_adapter.filing_id_of({"reportId": 9}) == "9"


class TestStateDonationRow:
    def test_projects_the_full_row(self):
        out = md_adapter.to_state_donation_row(
            ROW, state_txn_id="MD:x", status="CONFIRMED", status_reason="r",
            signals_matched_json="[]", entity_slug="angelos-john-p", entity_kind="owner",
            parent_owner_slug=None, recipient_filer_id=None,
            recipient_name="", recipient_type=None,
            raw_payload_path="data/raw/state/md/x.json", ingested_at="t",
        )
        assert out["jurisdiction"] == "MD" and out["source"] == "MD-SBE"
        assert out["amount"] == 10000.0
        assert out["date"] == "2022-07-18"
        assert out["election_cycle"] == 2022
        assert out["recipient_name"] == "A Safer, Stronger Baltimore PAC"
        assert out["recipient_type"] == "committee"
        assert out["contributor_zip"] == "21201"
        # Explicitly None, never a placeholder string — the archive must not
        # imply MD disclosed something it does not collect.
        assert out["contributor_employer_raw"] is None
        assert out["contributor_occupation_raw"] is None


class TestFetcherBuilders:
    def test_surnames_from_both_name_orders(self):
        owner = {"name_variants": ["John P. Angelos", "Angelos, John", "Angelos, John P"]}
        assert fetch_md.surnames_of(owner) == {"angelos"}

    def test_payload_uses_the_one_parameter_the_api_honours(self):
        # search/searchText/filter are silently ignored by MDCRIS — a wrong key
        # returns 200 and scans the whole corpus, so this shape is load-bearing.
        p = fetch_md.build_payload("lerner", 2, 100)
        assert p == {"contributorName": "lerner", "pageNumber": 2, "pageSize": 100}

    def test_parse_response_reads_the_envelope(self):
        items, total = fetch_md.parse_response(
            {"data": {"items": [ROW], "totalItems": 155}, "succeeded": True})
        assert total == 155 and items[0]["transactionGuid"] == ROW["transactionGuid"]

    def test_parse_response_raises_on_a_failed_envelope(self):
        with pytest.raises(RuntimeError, match="MDCRIS returned an error"):
            fetch_md.parse_response({"succeeded": False, "error": "boom"})

    def test_parse_response_tolerates_junk(self):
        assert fetch_md.parse_response({}) == ([], 0)
        assert fetch_md.parse_response(None) == ([], 0)

    def test_dedupe_on_guid(self):
        # An owner with several variants sharing a surname would otherwise fetch
        # the same rows once per variant.
        assert len(fetch_md.dedupe([ROW, dict(ROW), {**ROW, "transactionGuid": "other"}])) == 2

    def test_bucket_rows_by_owner(self):
        owners = [("angelos-john-p", {"name_variants": ["Angelos, John P"]}),
                  ("lerner-mark", {"name_variants": ["Lerner, Mark"]})]
        buckets = fetch_md.bucket_rows_by_owner([ROW], owners)
        assert [r["transactionGuid"] for r in buckets["angelos-john-p"]] == [ROW["transactionGuid"]]
        assert buckets["lerner-mark"] == []


class TestRegistry:
    def test_md_is_registered_as_an_api_source(self):
        assert "MD" in REGISTRY
        md = get_source("MD")
        assert md.requires_input is False          # API, no input file
        assert md.source == "MD-SBE"
        assert md.raw_ref == fetch_md.CONTRIBUTION_URL

    def test_registry_now_has_eleven_jurisdictions(self):
        assert len(REGISTRY) == 11

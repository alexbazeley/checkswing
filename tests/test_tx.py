"""Tests for the Texas (TEC) adapter + fetcher (scripts/tx_adapter.py, fetch_tx.py).

Covers field mapping (split names, employer/occupation), date/amount parsing,
native-id keying, the filers.csv recipient join, surname bucketing + recipient
pre-join, dedupe on contributionInfoId, streaming from a real zip, and — crucially —
that a TEC row flows through the UNCHANGED classifier to the right tier.
"""
from __future__ import annotations

import csv
import io
import zipfile

from scripts import fetch_tx, tx_adapter
from scripts.resolve_entities import CONFIRMED, PROBABLE, UNCERTAIN, classify

CONTRIB_COLS = [
    "recordType", "formTypeCd", "schedFormTypeCd", "reportInfoIdent", "receivedDt",
    "infoOnlyFlag", "filerIdent", "filerTypeCd", "filerName", "contributionInfoId",
    "contributionDt", "contributionAmount", "contributionDescr", "itemizeFlag",
    "travelFlag", "contributorPersentTypeCd", "contributorNameOrganization",
    "contributorNameLast", "contributorNameSuffixCd", "contributorNameFirst",
    "contributorNamePrefixCd", "contributorNameShort", "contributorStreetCity",
    "contributorStreetStateCd", "contributorStreetCountyCd", "contributorStreetCountryCd",
    "contributorStreetPostalCode", "contributorStreetRegion", "contributorEmployer",
    "contributorOccupation", "contributorJobTitle", "contributorPacFein",
    "contributorOosPacFlag", "contributorLawFirmName", "contributorSpouseLawFirmName",
    "contributorParent1LawFirmName", "contributorParent2LawFirmName",
]
FILER_COLS = [
    "recordType", "filerIdent", "filerTypeCd", "filerName", "ctaSeekOfficeDescr",
    "filerHoldOfficeDescr", "contestSeekOfficeDescr",
]

OWNER = {
    "slug": "crane-jim",
    "name": "James R. Crane",
    "name_variants": ["Jim Crane", "James Crane", "James R Crane", "James R. Crane", "Crane, James"],
    "verifying_signals": {
        "cities": ["houston"], "states": ["TX"],
        "employers": ["Crane Capital Group", "Crane Worldwide"], "occupations": ["executive"],
    },
    "strong_signals": {}, "negative_signals": {},
}


def _contrib(**kw) -> dict:
    row = {c: "" for c in CONTRIB_COLS}
    row.update({
        "recordType": "RCPT", "formTypeCd": "MPAC", "schedFormTypeCd": "A1",
        "reportInfoIdent": "730", "filerIdent": "00012345", "filerTypeCd": "COH",
        "filerName": "Texans for a Candidate", "contributionInfoId": "100000001",
        "contributionDt": "20240315", "contributionAmount": "5000.00",
        "contributorPersentTypeCd": "INDIVIDUAL",
        "contributorNameLast": "CRANE", "contributorNameFirst": "JAMES R.",
        "contributorStreetCity": "HOUSTON", "contributorStreetStateCd": "TX",
        "contributorStreetPostalCode": "77024", "contributorEmployer": "Crane Capital Group",
        "contributorOccupation": "Executive",
    })
    row.update(kw)
    return row


def _filer(**kw) -> dict:
    row = {c: "" for c in FILER_COLS}
    row.update({
        "recordType": "FILER", "filerIdent": "00012345", "filerTypeCd": "COH",
        "filerName": "Texans for a Candidate", "ctaSeekOfficeDescr": "STATE REPRESENTATIVE",
    })
    row.update(kw)
    return row


def _csv_text(cols, rows) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _make_zip(tmp_path, contrib_rows, filer_rows, contrib_member="contribs_01.csv"):
    zp = tmp_path / "TEC_CF_CSV.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(contrib_member, _csv_text(CONTRIB_COLS, contrib_rows))
        zf.writestr("filers.csv", _csv_text(FILER_COLS, filer_rows))
    return zp


# ── Adapter unit tests ───────────────────────────────────────────────────────

def test_date_and_amount_parsing():
    assert tx_adapter.parse_tx_date("20240315") == "2024-03-15"
    assert tx_adapter.parse_tx_date("2024-03-15") == "2024-03-15"
    assert tx_adapter.parse_tx_date("NULL") is None
    assert tx_adapter.parse_amount("5,000.00") == 5000.0
    assert tx_adapter.parse_amount("$90.00") == 90.0
    assert tx_adapter.parse_amount("NULL") is None


def test_first_middle_split_preserves_middle_initial():
    rec = tx_adapter.to_classifier_record(_contrib(contributorNameFirst="JAMES R."))
    assert rec["contributor_first_name"] == "JAMES"
    assert rec["contributor_middle_name"] == "R."
    assert rec["contributor_last_name"] == "CRANE"
    assert rec["contributor_name"] == "JAMES R. CRANE"


def test_record_maps_employer_occupation_and_jobtitle_fallback():
    rec = tx_adapter.to_classifier_record(_contrib(contributorOccupation="", contributorJobTitle="Owner"))
    assert rec["contributor_employer"] == "Crane Capital Group"
    assert rec["contributor_occupation"] == "Owner"  # falls back to job title


def test_ids_use_native_fields():
    row = _contrib(contributionInfoId="999", reportInfoIdent="730")
    assert tx_adapter.tran_id_of(row) == "999"
    assert tx_adapter.filing_id_of(row) == "730"


def test_recipient_type_mapping():
    assert tx_adapter.recipient_type_of("COH") == "candidate"
    assert tx_adapter.recipient_type_of("JCOH") == "candidate"
    assert tx_adapter.recipient_type_of("MPAC") == "committee"
    assert tx_adapter.recipient_type_of("GPAC") == "committee"
    assert tx_adapter.recipient_type_of("") is None


def test_entity_contributor_uses_org_name():
    rec = tx_adapter.to_classifier_record(
        _contrib(contributorNameLast="", contributorNameFirst="",
                 contributorPersentTypeCd="ENTITY", contributorNameOrganization="Crane Capital Group LLC")
    )
    assert rec["contributor_name"] == "Crane Capital Group LLC"


# ── Fetcher / zip-streaming tests ────────────────────────────────────────────

def test_build_filer_index_and_office(tmp_path):
    zp = _make_zip(tmp_path, [_contrib()], [_filer()])
    idx = fetch_tx.build_filer_index_from_zip(zp)
    assert idx["00012345"]["name"] == "Texans for a Candidate"
    assert idx["00012345"]["type"] == "candidate"
    assert idx["00012345"]["office"] == "STATE REPRESENTATIVE"


def test_stream_and_bucket_with_prejoin(tmp_path):
    zp = _make_zip(tmp_path, [
        _contrib(contributorNameLast="CRANE", contributorNameFirst="JAMES R."),
        _contrib(contributorNameLast="SMITH", contributorNameFirst="JANE", contributionInfoId="100000002"),
    ], [_filer()])
    buckets = fetch_tx.bucket_rows_by_owner(zp, [("crane-jim", OWNER)])
    assert len(buckets["crane-jim"]) == 1
    row = buckets["crane-jim"][0]
    assert row["_recipient_name"] == "Texans for a Candidate"
    assert row["_recipient_office"] == "STATE REPRESENTATIVE"
    assert row["_recipient_type"] == "candidate"


def test_dedupe_on_native_id(tmp_path):
    rows = [_contrib(contributionInfoId="100000001"), _contrib(contributionInfoId="100000001")]
    assert len(fetch_tx.dedupe(rows)) == 1


def test_contrib_member_detection():
    assert fetch_tx._is_contrib_member("contribs_01.csv")
    assert fetch_tx._is_contrib_member("contribs_100.csv")
    assert fetch_tx._is_contrib_member("cont_ss.csv")
    assert fetch_tx._is_contrib_member("cont_t.csv")
    assert not fetch_tx._is_contrib_member("filers.csv")
    assert not fetch_tx._is_contrib_member("cand.csv")


def test_extra_contrib_members_are_streamed(tmp_path):
    # A cont_ss.csv member must also be picked up (same schema).
    zp = tmp_path / "TEC_CF_CSV.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("cont_ss.csv", _csv_text(CONTRIB_COLS, [_contrib(contributionInfoId="200000001")]))
        zf.writestr("filers.csv", _csv_text(FILER_COLS, [_filer()]))
    rows = list(fetch_tx.iter_contrib_rows_from_zip(zp))
    assert len(rows) == 1 and rows[0]["contributionInfoId"] == "200000001"


# ── Integration through the real classifier ─────────────────────────────────

def _classifier_rec(**kw):
    return tx_adapter.to_classifier_record(_contrib(**kw))


def test_classifier_confirmed_employer_plus_city():
    c = classify(_classifier_rec(), OWNER)
    assert c is not None and c.status == CONFIRMED


def test_classifier_probable_city_only():
    c = classify(_classifier_rec(contributorEmployer="", contributorOccupation=""), OWNER)
    assert c is not None and c.status == PROBABLE


def test_classifier_uncertain_wrong_city():
    c = classify(
        _classifier_rec(contributorStreetCity="DALLAS", contributorEmployer="", contributorOccupation=""),
        OWNER,
    )
    assert c is not None and c.status == UNCERTAIN


def test_classifier_filters_non_matching_name():
    assert classify(_classifier_rec(contributorNameLast="JONES", contributorNameFirst="BOB"), OWNER) is None


def test_to_state_donation_row_shape(tmp_path):
    zp = _make_zip(tmp_path, [_contrib()], [_filer()])
    row = fetch_tx.bucket_rows_by_owner(zp, [("crane-jim", OWNER)])["crane-jim"][0]
    out = tx_adapter.to_state_donation_row(
        row, state_txn_id="TX:TEC:730:100000001", status="CONFIRMED", status_reason="x",
        signals_matched_json="[]", entity_slug="crane-jim", entity_kind="owner",
        parent_owner_slug=None, recipient_filer_id="00012345", recipient_name="Texans for a Candidate",
        recipient_type="candidate", raw_payload_path="data/raw/state/tx/TEC_CF_CSV.zip",
        ingested_at="2026-06-06T00:00:00Z",
    )
    assert out["amount"] == 5000.0 and out["date"] == "2024-03-15"
    assert out["jurisdiction"] == "TX" and out["source"] == "TEC"
    assert out["source_tran_id"] == "100000001" and out["source_filing_id"] == "730"
    assert out["contributor_employer_raw"] == "Crane Capital Group"
    assert out["recipient_office"] == "STATE REPRESENTATIVE"
    assert out["recipient_party"] is None
    assert out["election_cycle"] == 2024

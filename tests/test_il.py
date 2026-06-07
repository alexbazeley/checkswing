"""Tests for the Illinois adapter + fetcher (scripts/il_adapter.py, fetch_il.py).

Covers field mapping, ISBE date/amount parsing, the committee (recipient) join with
PartyAffiliation, native-ID dedup, surname bucketing over a streamed Receipts.txt, and
— crucially — that an ISBE row flows through the UNCHANGED classifier to the right tier.

Fixtures use the live ISBE bulk format: tab-delimited, header-bearing Receipts.txt /
Committees.txt. The adapter reads columns by name, so column order is immaterial.
"""
from __future__ import annotations

from scripts import fetch_il, il_adapter
from scripts.resolve_entities import CONFIRMED, PROBABLE, UNCERTAIN, classify


RECEIPTS_HEADER = (
    "ID\tCommitteeID\tFiledDocID\tETransID\tLastOnlyName\tFirstName\tRcvDate\tAmount\t"
    "AggregateAmount\tLoanAmount\tOccupation\tEmployer\tAddress1\tAddress2\tCity\tState\tZip\t"
    "D2Part\tDescription"
)
COMMITTEES_HEADER = "ID\tTypeOfCommittee\tName\tCity\tState\tZip\tPartyAffiliation\tPurpose"


def _receipts(*rows: str) -> str:
    return "﻿" + RECEIPTS_HEADER + "\n" + "\n".join(rows) + "\n"


def _committees(*rows: str) -> str:
    return "﻿" + COMMITTEES_HEADER + "\n" + "\n".join(rows) + "\n"


OWNER = {
    "slug": "reinsdorf-jerry",
    "name": "Jerry M. Reinsdorf",
    "name_variants": ["Jerry Reinsdorf", "Jerry M Reinsdorf", "Reinsdorf, Jerry"],
    "verifying_signals": {
        "cities": ["chicago"], "states": ["IL"],
        "employers": ["Chicago White Sox", "White Sox"], "occupations": ["chairman", "owner"],
    },
    "strong_signals": {"employers": ["Bojer Financial"], "zip_codes": ["60616"]},
    "negative_signals": {},
}


# ID, CmteID, FiledDocID, ETransID, Last, First, RcvDate, Amount, Agg, Loan, Occ, Emp,
# Addr1, Addr2, City, State, Zip, D2Part, Desc
def _row(rid="236630", cmte="10353", last="Reinsdorf", first="Jerry M", date="2022-09-10 00:00:00",
         amount="50000", occ="Chairman", emp="Chicago White Sox", city="Chicago", state="IL",
         zip_="60616", d2="1A"):
    return (f"{rid}\t{cmte}\t82298\t\t{last}\t{first}\t{date}\t{amount}\t0\t0\t{occ}\t{emp}\t"
            f"1 Main\t\t{city}\t{state}\t{zip_}\t{d2}\tcontribution")


COMMITTEE_ROW = "10353\tCandidate\tFriends of a Chicago Pol\tChicago\tIL\t60601\tDemocratic\tx"


def test_date_and_amount_parsing():
    assert il_adapter.parse_il_date("2022-09-10 00:00:00") == "2022-09-10"
    assert il_adapter.parse_il_date("") is None
    assert il_adapter.parse_amount("50,000") == 50000.0
    assert il_adapter.parse_amount("") is None


def test_split_first_middle():
    assert il_adapter._split_first_middle("Jerry M") == ("Jerry", "M")
    assert il_adapter._split_first_middle("Jerry") == ("Jerry", "")
    assert il_adapter._split_first_middle("") == ("", "")


def test_recipient_type_mapping():
    assert il_adapter.recipient_type_of("Candidate") == "candidate"
    assert il_adapter.recipient_type_of("Ballot Initiative") == "ballot_measure"
    assert il_adapter.recipient_type_of("Political Action") == "committee"
    assert il_adapter.recipient_type_of("") is None


def test_build_committee_index_has_party(tmp_path):
    c = tmp_path / "Committees.txt"
    c.write_text(_committees(COMMITTEE_ROW), encoding="utf-8")
    idx = fetch_il.build_committee_index(c)
    assert idx["10353"]["name"] == "Friends of a Chicago Pol"
    assert idx["10353"]["party"] == "Democratic"
    assert idx["10353"]["type"] == "candidate"


def _bucketed(tmp_path, *receipt_rows, owners=None):
    r = tmp_path / "Receipts.txt"
    c = tmp_path / "Committees.txt"
    r.write_text(_receipts(*receipt_rows), encoding="utf-8")
    c.write_text(_committees(COMMITTEE_ROW), encoding="utf-8")
    return fetch_il.bucket_rows_by_owner(tmp_path, owners or [("reinsdorf-jerry", OWNER)])


def test_bucket_streams_and_prejoins_recipient(tmp_path):
    buckets = _bucketed(tmp_path, _row(), _row(rid="2", last="Smith", first="Jane"))
    rows = buckets["reinsdorf-jerry"]
    assert len(rows) == 1
    assert rows[0]["_recipient_name"] == "Friends of a Chicago Pol"
    assert rows[0]["_recipient_party"] == "Democratic"


def test_dedupe_on_native_id(tmp_path):
    buckets = _bucketed(tmp_path, _row(rid="9"), _row(rid="9"))  # same ID twice
    assert len(fetch_il.dedupe(buckets["reinsdorf-jerry"])) == 1


def test_missing_receipts_file_raises(tmp_path):
    (tmp_path / "Committees.txt").write_text(_committees(COMMITTEE_ROW), encoding="utf-8")
    try:
        fetch_il.bucket_rows_by_owner(tmp_path, [("reinsdorf-jerry", OWNER)])
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


# ── Integration through the real classifier ─────────────────────────────────

def _classify_row(tmp_path, **kw):
    buckets = _bucketed(tmp_path, _row(**kw))
    row = buckets["reinsdorf-jerry"][0]
    return classify(il_adapter.to_classifier_record(row), OWNER), row


def test_classifier_confirmed_strong_employer(tmp_path):
    # Bojer Financial is a strong employer → CONFIRMED.
    c, _ = _classify_row(tmp_path, emp="Bojer Financial Ltd", occ="", city="Chicago")
    assert c is not None and c.status == CONFIRMED


def test_classifier_confirmed_employer_plus_city(tmp_path):
    c, _ = _classify_row(tmp_path)  # White Sox + Chicago + IL
    assert c is not None and c.status == CONFIRMED


def test_classifier_probable_city_only(tmp_path):
    c, _ = _classify_row(tmp_path, emp="", occ="", zip_="99999")
    assert c is not None and c.status == PROBABLE


def test_classifier_uncertain_wrong_city(tmp_path):
    c, _ = _classify_row(tmp_path, emp="", occ="", city="Peoria", zip_="61600")
    assert c is not None and c.status == UNCERTAIN


def test_prefilter_drops_other_surname(tmp_path):
    # A different surname never reaches the classifier — filtered at the surname prefilter.
    buckets = _bucketed(tmp_path, _row(last="Daley", first="Richard"))
    assert buckets["reinsdorf-jerry"] == []


def test_classifier_rejects_same_surname_relative(tmp_path):
    # Son "Michael Reinsdorf" buckets on the surname but the classifier rejects him —
    # name_variants require the full first name "Jerry" (family-collision firewall).
    c, _ = _classify_row(tmp_path, last="Reinsdorf", first="Michael")
    assert c is None


def test_to_state_donation_row_carries_party_and_native_ids(tmp_path):
    buckets = _bucketed(tmp_path, _row())
    row = buckets["reinsdorf-jerry"][0]
    out = il_adapter.to_state_donation_row(
        row, state_txn_id="IL:ISBE:82298:236630", status="CONFIRMED", status_reason="x",
        signals_matched_json="[]", entity_slug="reinsdorf-jerry", entity_kind="owner",
        parent_owner_slug=None, recipient_filer_id="10353", recipient_name="Friends of a Chicago Pol",
        recipient_type="candidate", raw_payload_path="data/raw/state/il/Receipts.txt",
        ingested_at="2026-06-06T00:00:00Z",
    )
    assert out["amount"] == 50000.0 and out["date"] == "2022-09-10"
    assert out["recipient_party"] == "Democratic"
    assert out["source_tran_id"] == "236630" and out["source_filing_id"] == "82298"
    assert out["report_type"] == "1A"
    assert out["jurisdiction"] == "IL" and out["source"] == "ISBE"

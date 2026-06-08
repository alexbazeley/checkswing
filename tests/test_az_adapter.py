"""Tests for the Arizona See-The-Money adapter + fetcher (scripts/az_adapter.py,
scripts/fetch_az.py).

Asserts (1) an AZ JSON transaction row, mapped by the adapter, flows through the
UNCHANGED classifier into the right bucket, and (2) the entity-name matcher that
makes the contributor pull viable — a broad surname search returns many unrelated
entities ("Austin Kendrick, Monica"; "begay, kendrick"), and only true
(surname, first-name) matches may be pulled.
"""
from __future__ import annotations

from scripts import az_adapter as az
from scripts import fetch_az
from scripts.resolve_entities import CONFIRMED, PROBABLE, UNCERTAIN, classify


OWNER = {
    "slug": "kendrick-ken",
    "name": "Earl G. Kendrick Jr.",
    "name_variants": ["Ken Kendrick", "Kendrick, Ken", "Earl G. Kendrick", "Ken Kendrick Jr."],
    "verifying_signals": {
        "cities": ["paradise valley", "phoenix"],
        "states": ["AZ"],
        "employers": ["Arizona Diamondbacks"],
        "occupations": ["owner", "managing general partner"],
    },
    "strong_signals": {},
    "negative_signals": {},
}


def _txn(**over) -> dict:
    row = {
        "PublicTransactionId": 8953233,
        "TransactionId": 3236992,
        "TransactionDate": "/Date(1256886000000)/",   # 2009-10-30 UTC
        "Amount": 5000.0,
        "CommitteeId": 201000214,
        "CommitteeUniqueId": 10054,
        "CommitteeName": "No On Prop 140",
        "CommitteeGroupName": "PACs",
        "CandidateFirstName": "",
        "CandidateLastName": "",
        "TransactionFirstName": "Ken",
        "TransactionMiddleName": None,
        "TransactionLastName": "Kendrick, Ken ",
        "TransactionOccupation": "Owner",
        "TransactionEmployer": "Arizona Diamondbacks",
        "TransactionCity": "Paradise Valley",
        "TransactionState": "AZ",
        "TransactionZipCode": "85253",
        "TransactionType": "Contribution from Individuals",
    }
    row.update(over)
    return row


# ── Pure adapter mapping ─────────────────────────────────────────────────────

def test_dotnet_date_parsing():
    assert az.parse_dotnet_date("/Date(1256886000000)/") == "2009-10-30"
    assert az.parse_dotnet_date("/Date(1564383600000)/") == "2019-07-29"
    assert az.parse_dotnet_date("/Date(-1000)/") == "1969-12-31"
    assert az.parse_dotnet_date("not a date") is None
    assert az.parse_dotnet_date(None) is None


def test_amount_parsing():
    assert az.parse_amount("5,000") == 5000.0
    assert az.parse_amount(5000.0) == 5000.0
    assert az.parse_amount("") is None
    assert az.parse_amount(None) is None


def test_contributor_name_split_from_messy_lastname():
    rec = az.to_classifier_record(_txn())
    assert rec["contributor_last_name"] == "Kendrick"   # split from "Kendrick, Ken "
    assert rec["contributor_first_name"] == "Ken"
    assert rec["contributor_name"] == "Kendrick, Ken"
    assert rec["contributor_employer"] == "Arizona Diamondbacks"
    assert rec["contributor_city"] == "Paradise Valley"


def test_ids_and_recipient_type():
    r = _txn()
    assert az.tran_id_of(r) == "8953233"        # PublicTransactionId
    assert az.filing_id_of(r) == "10054"        # CommitteeUniqueId
    assert az.recipient_type_of(r) == "committee"
    assert az.recipient_type_of(_txn(CommitteeGroupName="Candidates")) == "candidate"
    assert az.recipient_type_of(_txn(CommitteeGroupName="", CandidateLastName="Smith")) == "candidate"


def test_state_donation_row_shape():
    row = az.to_state_donation_row(
        _txn(), state_txn_id="AZ:8953233", status=CONFIRMED, status_reason="r",
        signals_matched_json="[]", entity_slug="kendrick-ken", entity_kind="owner",
        parent_owner_slug=None, recipient_filer_id="10054", recipient_name="No On Prop 140",
        recipient_type="committee", raw_payload_path="raw", ingested_at="2026-06-08T00:00:00Z",
    )
    assert row["jurisdiction"] == "AZ" and row["source"] == "AZ-SOS"
    assert row["amount"] == 5000.0 and row["date"] == "2009-10-30"
    assert row["recipient_name"] == "No On Prop 140"
    assert row["contributor_employer_raw"] == "Arizona Diamondbacks"


# ── Integration through the real classifier ──────────────────────────────────

def test_classifier_confirmed_two_signals():
    """employer (Arizona Diamondbacks) + city/state (Paradise Valley AZ) → CONFIRMED."""
    c = classify(az.to_classifier_record(_txn()), OWNER)
    assert c is not None and c.status == CONFIRMED
    assert c.entity_slug == "kendrick-ken"


def test_classifier_probable_one_signal():
    rec = az.to_classifier_record(_txn(TransactionCity="", TransactionState="", TransactionOccupation="Investor"))
    c = classify(rec, OWNER)
    assert c is not None and c.status == PROBABLE


def test_classifier_uncertain_name_only():
    rec = az.to_classifier_record(
        _txn(TransactionEmployer="", TransactionOccupation="", TransactionCity="", TransactionState="")
    )
    c = classify(rec, OWNER)
    assert c is not None and c.status == UNCERTAIN


def test_classifier_filters_non_matching_first_name():
    """A different first name on the same surname is not Ken."""
    assert classify(az.to_classifier_record(_txn(TransactionFirstName="Monica", TransactionLastName="Kendrick, Monica")), OWNER) is None


# ── Fetcher pure helpers ─────────────────────────────────────────────────────

def test_name_pairs():
    assert ("kendrick", "ken") in fetch_az._name_pairs(OWNER)


def test_entity_matches_keeps_real_and_rejects_noise():
    pairs = fetch_az._name_pairs(OWNER)
    assert fetch_az.entity_matches("Kendrick, Ken ", pairs) is True
    assert fetch_az.entity_matches("Kendrick, Kenneth G", pairs) is True      # prefix Ken↔Kenneth
    assert fetch_az.entity_matches("Austin Kendrick, Monica L", pairs) is False  # wrong surname
    assert fetch_az.entity_matches("begay, kendrick ", pairs) is False        # Kendrick is the first name
    assert fetch_az.entity_matches("Kendrick, Randy ", pairs) is False        # spouse, different first


def test_dt_body_carries_search_and_columns():
    body = fetch_az._dt_body("Kendrick", 500).decode()
    assert "search%5Bvalue%5D=Kendrick" in body
    assert "columns%5B11%5D%5Bdata%5D=11" in body   # full 12-col array present
    assert "length=500" in body


def test_parse_search_and_detail_responses():
    sr = fetch_az.parse_search_response({"data": [
        {"EntityID": 541347, "EntityLastName": "Kendrick, Ken "},
        {"EntityID": None, "EntityLastName": "junk"},
    ]})
    assert sr == [{"EntityID": 541347, "EntityLastName": "Kendrick, Ken"}]
    dr = fetch_az.parse_detail_response({"data": [_txn(), _txn(PublicTransactionId=2)]})
    assert len(dr) == 2


def test_dedupe_on_public_transaction_id():
    rows = [_txn(), _txn(), _txn(PublicTransactionId=999)]
    assert len(fetch_az.dedupe(rows)) == 2

"""Tests for the Colorado TRACER adapter + fetcher (scripts/co_adapter.py,
scripts/fetch_co.py).

Two things are asserted:
  1. A TRACER-shaped row, mapped by the adapter, flows through the UNCHANGED
     classifier (resolve_entities.classify) into the right three-tier bucket —
     proving the federal classifier is reused verbatim for CO data.
  2. The disambiguation that motivates CO: "Monfort" is a large Colorado family
     name and the Rockies owner's own brother Charlie shares the Colorado Rockies
     employer, so only first-name discrimination separates Dick Monfort — a bare
     same-surname relative must NOT be attributed to him.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from scripts import co_adapter as co
from scripts import fetch_co
from scripts.resolve_entities import CONFIRMED, PROBABLE, UNCERTAIN, classify


# A minimal CO-resident owner signal block (shape mirrors owners/*.yaml).
OWNER = {
    "slug": "monfort-dick",
    "name": "Richard L. Monfort",
    "name_variants": ["Dick Monfort", "Richard Monfort", "Monfort, Richard", "Monfort, Dick"],
    "verifying_signals": {
        "cities": ["denver"],
        "states": ["CO"],
        "employers": ["Colorado Rockies"],
        "occupations": ["owner"],
    },
    "strong_signals": {},
    "negative_signals": {},
}

HEADER = [
    "CO_ID", "ContributionAmount", "ContributionDate", "LastName", "FirstName", "MI",
    "Suffix", "Address1", "Address2", "City", "State", "Zip", "Explanation", "RecordID",
    "FiledDate", "ContributionType", "ReceiptType", "ContributorType", "Electioneering",
    "CommitteeType", "CommitteeName", "CandidateName", "Employer", "Occupation",
    "Amended", "Amendment", "AmendedRecordID", "Jurisdiction", "OccupationComments",
]


def _row(**over) -> dict:
    row = {
        "CO_ID": "20175032283",
        "ContributionAmount": "1150",
        "ContributionDate": "2018-03-14 00:00:00",
        "LastName": "MONFORT",
        "FirstName": "DICK",
        "MI": "",
        "Suffix": "",
        "Address1": "1891 CURTIS ST",
        "Address2": "",
        "City": "DENVER",
        "State": "CO",
        "Zip": "80202",
        "RecordID": "4819748",
        "FiledDate": "2018-05-07 00:00:00",
        "ContributionType": "Monetary (Itemized)",
        "ReceiptType": "Credit/Debit Card",
        "ContributorType": "Individual",
        "CommitteeType": "Candidate Committee",
        "CommitteeName": "LYNNE FOR COLORADO",
        "CandidateName": "DONNA LYNNE",
        "Employer": "COLORADO ROCKIES",
        "Occupation": "Owner",
        "Jurisdiction": "STATEWIDE",
    }
    row.update(over)
    return row


# ── Pure adapter mapping ─────────────────────────────────────────────────────

def test_to_classifier_record_maps_fields():
    rec = co.to_classifier_record(_row())
    assert rec["contributor_name"] == "MONFORT, DICK"
    assert rec["contributor_last_name"] == "MONFORT"
    assert rec["contributor_first_name"] == "DICK"
    assert rec["contributor_employer"] == "COLORADO ROCKIES"
    assert rec["contributor_occupation"] == "Owner"
    assert rec["contributor_city"] == "DENVER"
    assert rec["contributor_state"] == "CO"
    assert rec["contributor_zip"] == "80202"


def test_middle_initial_in_comma_form():
    rec = co.to_classifier_record(_row(FirstName="RICHARD", MI="L"))
    assert rec["contributor_name"] == "MONFORT, RICHARD L"
    assert rec["contributor_middle_name"] == "L"


def test_business_name_has_no_comma_form():
    rec = co.to_classifier_record(_row(LastName="MONFORT COMPANY", FirstName=""))
    assert rec["contributor_name"] == "MONFORT COMPANY"


def test_date_parsing():
    assert co.parse_co_date("2018-03-14 00:00:00") == "2018-03-14"
    assert co.parse_co_date("2022-11-10 13:45:00") == "2022-11-10"
    assert co.parse_co_date("not a date") is None
    assert co.parse_co_date("") is None


def test_amount_parsing():
    assert co.parse_amount("1,150") == 1150.0
    assert co.parse_amount("156.25") == 156.25
    assert co.parse_amount("-200") == -200.0   # returned contributions
    assert co.parse_amount("") is None
    assert co.parse_amount("abc") is None


def test_election_cycle_is_calendar_year():
    assert co.election_cycle_from_date("2022-11-10") == 2022
    assert co.election_cycle_from_date(None) is None


def test_ids_and_recipient_type():
    r = _row()
    assert co.tran_id_of(r) == "4819748"          # RecordID
    assert co.filing_id_of(r) == "20175032283"    # CO_ID
    assert co.recipient_type_of(r) == "candidate"
    assert co.recipient_type_of(_row(CommitteeType="Political Committee")) == "committee"
    assert co.recipient_type_of(_row(CommitteeType="")) is None


def test_state_donation_row_shape():
    row = co.to_state_donation_row(
        _row(),
        state_txn_id="CO:4819748", status=CONFIRMED, status_reason="r",
        signals_matched_json="[]", entity_slug="monfort-dick", entity_kind="owner",
        parent_owner_slug=None, recipient_filer_id="20175032283",
        recipient_name="LYNNE FOR COLORADO", recipient_type="candidate",
        raw_payload_path="raw", ingested_at="2026-06-08T00:00:00Z",
    )
    assert row["jurisdiction"] == "CO" and row["source"] == "CO-TRACER"
    assert row["amount"] == 1150.0 and row["date"] == "2018-03-14"
    assert row["recipient_name"] == "LYNNE FOR COLORADO"
    assert row["contributor_employer_raw"] == "COLORADO ROCKIES"
    assert row["recipient_party"] is None and row["recipient_office"] is None


# ── Integration through the real classifier ──────────────────────────────────

def test_classifier_confirmed_two_signals():
    """employer (Colorado Rockies) + city/state (Denver CO) → CONFIRMED."""
    c = classify(co.to_classifier_record(_row()), OWNER)
    assert c is not None and c.status == CONFIRMED
    assert c.entity_slug == "monfort-dick"


def test_classifier_probable_one_signal():
    """employer matches but no city/state → exactly one confirming signal → PROBABLE."""
    rec = co.to_classifier_record(_row(City="", State="", Occupation="Investor"))
    c = classify(rec, OWNER)
    assert c is not None and c.status == PROBABLE


def test_classifier_uncertain_name_only():
    rec = co.to_classifier_record(_row(Employer="", Occupation="", City="", State=""))
    c = classify(rec, OWNER)
    assert c is not None and c.status == UNCERTAIN


def test_brother_charlie_not_attributed_to_dick():
    """Charlie Monfort shares the Colorado Rockies employer but is a different person —
    first-name discrimination must keep him OUT of Dick's attribution."""
    rec = co.to_classifier_record(_row(FirstName="CHARLIE"))
    assert classify(rec, OWNER) is None


def test_classifier_uncertain_on_address_contradiction():
    """Documented residence is Denver/CO; a Greelely-relative row out of state with the
    same name + employer demotes to UNCERTAIN rather than CONFIRMED."""
    rec = co.to_classifier_record(_row(City="Greenwich", State="CT"))
    c = classify(rec, OWNER)
    assert c is not None and c.status == UNCERTAIN


# ── Fetcher: parse, recipient inline-join, bucket, dedupe, zip round-trip ─────

def _csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    import csv
    w = csv.DictWriter(buf, fieldnames=HEADER)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in HEADER})
    return buf.getvalue()


def test_iter_contributions_fh_inlines_recipient():
    rows = list(fetch_co.iter_contributions_fh(io.StringIO(_csv_text([_row()]))))
    assert len(rows) == 1
    assert rows[0]["_recipient_name"] == "LYNNE FOR COLORADO"
    assert rows[0]["_recipient_type"] == "candidate"


def test_bucket_rows_by_owner_funnels_by_surname():
    rows = [_row(), _row(LastName="SMITH", FirstName="JOHN", RecordID="9")]
    buckets = fetch_co.bucket_rows_by_owner(rows, [("monfort-dick", OWNER)])
    assert len(buckets["monfort-dick"]) == 1
    assert buckets["monfort-dick"][0]["FirstName"] == "DICK"


def test_dedupe_on_recordid():
    rows = [_row(), _row(), _row(RecordID="other")]
    assert len(fetch_co.dedupe(rows)) == 2


def test_resolver_reads_inline_recipient():
    rows = list(fetch_co.iter_contributions_fh(io.StringIO(_csv_text([_row()]))))
    resolve = fetch_co.make_recipient_resolver()
    rec = resolve(rows[0])
    assert rec["name"] == "LYNNE FOR COLORADO"
    assert rec["type"] == "candidate"
    assert rec["filer_id"] == "20175032283"


def test_zip_round_trip(tmp_path: Path):
    z = tmp_path / "2018_ContributionData.csv.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("2018_ContributionData.csv", _csv_text([_row(), _row(RecordID="2")]))
    out = list(fetch_co.iter_dir(tmp_path))
    assert len(out) == 2
    assert all(r["_recipient_name"] == "LYNNE FOR COLORADO" for r in out)

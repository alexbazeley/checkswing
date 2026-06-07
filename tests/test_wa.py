"""Tests for the Washington adapter + fetcher (scripts/wa_adapter.py, fetch_wa.py).

Covers the 'LAST FIRST [MIDDLE]' name split, gold-grade employer/occupation mapping,
the inline recipient (filer_name + party + office), native-id dedup, the SoQL WHERE
builder, and that a WA row flows through the UNCHANGED classifier to the right tier.

WA is API-based (Socrata), so the network query is the only untested surface; the
parsing/WHERE builders are unit-tested against synthetic rows shaped like real
data.wa.gov `kv7h-kjye` records.
"""
from __future__ import annotations

from scripts import fetch_wa, wa_adapter
from scripts.resolve_entities import CONFIRMED, PROBABLE, UNCERTAIN, classify


OWNER = {
    "slug": "stanton-john",
    "name": "John W. Stanton",
    "name_variants": ["John W Stanton", "John Stanton", "Stanton, John", "Stanton, John W"],
    "verifying_signals": {
        "cities": ["medina", "bellevue", "seattle"], "states": ["WA"],
        "employers": ["Trilogy Partnership", "Seattle Mariners"], "occupations": ["partner", "chairman"],
    },
    "strong_signals": {"employers": ["Trilogy Equity Partners", "Western Wireless"]},
    "negative_signals": {"employers": ["BuzzFeed"]},
}


def _row(name="STANTON JOHN", city="MEDINA", state="WA", occ="PARTNER",
         emp="TRILOGY EQUITY PARTNERS", amount="1000.00", date="2016-04-23T00:00:00.000",
         rid="4025331", repno="100773543", filer="Friends of a WA Senator", party="DEMOCRAT",
         office="STATE SENATE", rtype="Candidate"):
    return {
        "id": rid, "report_number": repno, "filer_id": "4830", "filer_name": filer,
        "office": office, "party": party, "type": rtype, "receipt_date": date,
        "amount": amount, "election_year": date[:4], "contributor_name": name,
        "contributor_city": city, "contributor_state": state,
        "contributor_occupation": occ, "contributor_employer_name": emp,
        "contributor_zip": "98039",
    }


def test_date_and_amount_parsing():
    assert wa_adapter.parse_wa_date("2016-04-23T00:00:00.000") == "2016-04-23"
    assert wa_adapter.parse_wa_date("") is None
    assert wa_adapter.parse_amount("1,000.00") == 1000.0
    assert wa_adapter.parse_amount("") is None


def test_name_split_last_first_middle():
    assert wa_adapter._split_name("STANTON JOHN") == ("STANTON", "JOHN", "")
    assert wa_adapter._split_name("STANTON JOHN W") == ("STANTON", "JOHN", "W")
    assert wa_adapter._split_name("MICROSOFT") == ("MICROSOFT", "", "")
    # Composed comma-form so the classifier swaps to "First Last".
    assert wa_adapter._composed_name(_row(name="STANTON JOHN W")) == "STANTON, JOHN W"


def test_build_where_prefix_per_variant():
    pairs = fetch_wa._name_pairs(OWNER)
    assert ("stanton", "john") in pairs
    where = fetch_wa.build_where(pairs)
    assert "upper(contributor_name) like 'STANTON JOHN%'" in where
    assert " OR " in where or where.count("like") == 1


def test_recipient_resolver_inline():
    resolve = fetch_wa.make_recipient_resolver()
    r = resolve(_row())
    assert r["filer_id"] == "4830"
    assert r["name"] == "Friends of a WA Senator"
    assert r["type"] == "candidate"


def test_dedupe_on_native_id():
    assert len(fetch_wa.dedupe([_row(rid="9"), _row(rid="9"), _row(rid="10")])) == 2


def test_surname_funnel():
    assert wa_adapter.surname_of(_row(name="STANTON JOHN")) == "stanton"


# ── Integration through the real classifier ─────────────────────────────────

def test_classifier_confirmed_strong_employer():
    # Trilogy Equity Partners is a strong employer → CONFIRMED.
    c = classify(wa_adapter.to_classifier_record(_row(emp="TRILOGY EQUITY PARTNERS", occ="")), OWNER)
    assert c is not None and c.status == CONFIRMED


def test_classifier_confirmed_employer_plus_city():
    c = classify(wa_adapter.to_classifier_record(_row(emp="TRILOGY PARTNERSHIP", city="MEDINA")), OWNER)
    assert c is not None and c.status == CONFIRMED


def test_classifier_probable_city_state_only():
    # One signal — city+state together (a city without state isn't verifiable) → PROBABLE.
    c = classify(wa_adapter.to_classifier_record(_row(emp="", occ="", city="MEDINA", state="WA")), OWNER)
    assert c is not None and c.status == PROBABLE


def test_classifier_uncertain_wrong_city():
    c = classify(wa_adapter.to_classifier_record(
        _row(emp="", occ="", city="SPOKANE", state="WA")), OWNER)
    assert c is not None and c.status == UNCERTAIN


def test_classifier_negative_signal_blocks_doppelganger():
    # The journalist John Stanton (BuzzFeed) must not confirm even at a WA city.
    c = classify(wa_adapter.to_classifier_record(
        _row(emp="BUZZFEED", occ="JOURNALIST", city="SEATTLE")), OWNER)
    assert c is None or c.status == UNCERTAIN


def test_classifier_filters_non_matching_first_name():
    # Same surname, different person (e.g. "STANTON MARY") → not John.
    c = classify(wa_adapter.to_classifier_record(_row(name="STANTON MARY")), OWNER)
    assert c is None


def test_to_state_donation_row_carries_party_office_native_ids():
    out = wa_adapter.to_state_donation_row(
        _row(), state_txn_id="WA:WA-PDC:100773543:4025331", status="CONFIRMED",
        status_reason="x", signals_matched_json="[]", entity_slug="stanton-john",
        entity_kind="owner", parent_owner_slug=None, recipient_filer_id="4830",
        recipient_name="Friends of a WA Senator", recipient_type="candidate",
        raw_payload_path=fetch_wa.SODA_URL, ingested_at="2026-06-06T00:00:00Z",
    )
    assert out["amount"] == 1000.0 and out["date"] == "2016-04-23"
    assert out["recipient_party"] == "DEMOCRAT" and out["recipient_office"] == "STATE SENATE"
    assert out["source_filing_id"] == "100773543" and out["source_tran_id"] == "4025331"
    assert out["contributor_employer_raw"] == "TRILOGY EQUITY PARTNERS"
    assert out["jurisdiction"] == "WA" and out["source"] == "WA-PDC"

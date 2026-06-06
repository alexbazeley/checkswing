"""Tests for the New York adapter + fetcher (scripts/ny_adapter.py, fetch_ny.py).

NY has no employer/occupation and no contributor state — so the only CONFIRMED path
is a documented strong ZIP (e.g. Steve Cohen's NYC 10001); everything else is
UNCERTAIN (conservative, no state-from-ZIP guessing). These tests prove that tiering
through the UNCHANGED classifier, plus the field mapping and SoQL builders.
"""
from __future__ import annotations

from scripts import fetch_ny, ny_adapter
from scripts.resolve_entities import CONFIRMED, UNCERTAIN, classify


# Cohen-like owner: NYC strong ZIP 10001 is the CONFIRMED lever for NY.
OWNER = {
    "slug": "cohen-steven",
    "name": "Steven A. Cohen",
    "name_variants": ["Steven Cohen", "Steve Cohen", "Steven A Cohen", "Cohen, Steven"],
    "verifying_signals": {"cities": ["greenwich"], "states": ["CT"], "employers": ["Point72"], "occupations": []},
    "strong_signals": {"employers": [], "zip_codes": ["10001"]},
    "negative_signals": {},
}


def _row(last="Cohen", first="Steven", middle="A", city="New York", zip_="10001",
         amt="25000", date="2018-09-05T00:00:00.000", trans="555111", filer="A100",
         comm="Friends of a NY Senator"):
    return {
        "flng_ent_last_name": last, "flng_ent_first_name": first, "flng_ent_middle_name": middle,
        "flng_ent_city": city, "flng_ent_zip": zip_, "flng_ent_country": "United States",
        "org_amt": amt, "sched_date": date, "trans_number": trans,
        "filer_id": filer, "cand_comm_name": comm, "election_year": "2018",
        "cntrbr_type_desc": "Individual", "filing_desc": "32-Day Pre-Primary",
    }


def test_date_amount_parsing():
    assert ny_adapter.parse_ny_date("2018-09-05T00:00:00.000") == "2018-09-05"
    assert ny_adapter.parse_ny_date("") is None
    assert ny_adapter.parse_amount("25000") == 25000.0
    assert ny_adapter.parse_amount("") is None


def test_classifier_record_no_employer_no_state():
    rec = ny_adapter.to_classifier_record(_row())
    assert rec["contributor_name"] == "Cohen, Steven A"
    # employer/occupation/city/state all suppressed; only name + zip drive NY.
    assert rec["contributor_employer"] == "" and rec["contributor_state"] == ""
    assert rec["contributor_city"] == ""
    assert rec["contributor_zip"] == "10001"


def test_classifier_confirmed_via_strong_zip():
    rec = ny_adapter.to_classifier_record(_row(zip_="10001"))
    c = classify(rec, OWNER)
    assert c is not None and c.status == CONFIRMED
    assert any("zip" in s for s in c.signals_matched)


def test_classifier_uncertain_without_strong_zip():
    # Name matches, but ZIP isn't his strong ZIP and there's no employer/state to lean on.
    rec = ny_adapter.to_classifier_record(_row(zip_="12055", city="Albany"))
    c = classify(rec, OWNER)
    assert c is not None and c.status == UNCERTAIN


def test_classifier_filters_other_first_name():
    rec = ny_adapter.to_classifier_record(_row(first="Daniel", zip_="10001"))
    assert classify(rec, OWNER) is None  # Daniel Cohen ≠ Steven Cohen


def test_to_state_donation_row_recipient_inline():
    out = ny_adapter.to_state_donation_row(
        _row(), state_txn_id="NY:NYSBOE:A100:555111", status="CONFIRMED", status_reason="strong zip",
        signals_matched_json="[]", entity_slug="cohen-steven", entity_kind="owner",
        parent_owner_slug=None, recipient_filer_id="A100", recipient_name="Friends of a NY Senator",
        recipient_type=None, raw_payload_path="https://data.ny.gov/resource/4j2b-6a2j", ingested_at="2026-06-04T00:00:00Z",
    )
    assert out["amount"] == 25000.0 and out["date"] == "2018-09-05"
    assert out["recipient_name"] == "Friends of a NY Senator"
    assert out["recipient_type"] == "candidate"  # "Friends of …" heuristic
    assert out["jurisdiction"] == "NY" and out["source"] == "NYSBOE"
    assert out["contributor_employer_raw"] is None and out["contributor_state"] is None


# ── SoQL builders (network query itself is not unit-tested) ──────────────────

def test_name_parts_and_where():
    lasts, firsts = fetch_ny._name_parts(OWNER)
    assert lasts == {"cohen"} and {"steven", "steve"} <= firsts
    where = fetch_ny.build_where(lasts, firsts)
    assert "upper(flng_ent_last_name) in ('COHEN')" in where
    assert "upper(flng_ent_first_name) in (" in where and "'STEVEN'" in where


def test_where_escapes_quotes():
    where = fetch_ny.build_where({"o'brien"}, set())
    assert "O''BRIEN" in where  # SoQL single-quote escaping


def test_dedupe_on_trans_number():
    rows = [_row(trans="1"), _row(trans="1"), _row(trans="2")]
    assert len(fetch_ny.dedupe(rows)) == 2

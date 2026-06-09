"""Tests for the Florida DoE adapter + fetcher (scripts/fl_adapter.py,
scripts/fetch_fl.py).

Asserts:
  1. An FL-shaped TSV row, mapped by the adapter, flows through the UNCHANGED
     classifier into the right three-tier bucket — proving the federal classifier is
     reused verbatim (FL confirms via occupation + city_state, having no employer).
  2. The Steinbrenner family collision: the FL data is dominated by the late
     George M. Steinbrenner III + relatives; only Hal (Harold) is a tracked owner,
     so a George/relative row must NOT be attributed to Hal.
  3. The fetcher's TSV parse, inline recipient (incl. party/office), content-hash
     dedup, surname funnel, and the contrib.exe form body (search_on=2/queryformat=2).
"""
from __future__ import annotations

from scripts import fetch_fl
from scripts import fl_adapter as fl
from scripts.resolve_entities import CONFIRMED, PROBABLE, UNCERTAIN, classify

HAL = {
    "slug": "steinbrenner-hal",
    "name": "Harold Z. Steinbrenner",
    "name_variants": ["Hal Steinbrenner", "Harold Z. Steinbrenner", "Steinbrenner, Harold Z", "Steinbrenner, Hal"],
    "verifying_signals": {"cities": ["tampa"], "states": ["FL"],
                           "occupations": ["owner", "chairman", "ceo", "partner"]},
    "strong_signals": {},
    "negative_signals": {},
}
ZAL = {
    "slug": "zalupski-patrick",
    "name": "Patrick O. Zalupski",
    "name_variants": ["Patrick Zalupski", "Patrick O. Zalupski", "Zalupski, Patrick"],
    "verifying_signals": {"cities": ["jacksonville", "ponte vedra beach"], "states": ["FL"],
                           "occupations": ["executive", "homebuilder", "president", "ceo"]},
    "strong_signals": {},
    "negative_signals": {},
}

HEADER = "\t".join(fetch_fl.TSV_HEADER)


def _tsv(*rows: list[str]) -> str:
    return "\n".join([HEADER] + ["\t".join(r) for r in rows]) + "\n"


def _hal_row(**over) -> dict:
    base = {
        "Candidate/Committee": "Bondi, Pam  (REP)(ATG)",
        "Date": "09/24/2013", "Amount": "500.00", "Typ": "CHE",
        "Contributor Name": "STEINBRENNER HAROLD Z.", "Address": "1 STEINBRENNER DR",
        "City State Zip": "TAMPA, FL 33614", "Occupation": "SPORTS TEAM OWNER", "Inkind Desc": "",
    }
    base.update(over)
    return base


# ── Pure adapter mapping ─────────────────────────────────────────────────────

def test_name_to_comma_form_and_surname():
    assert fl.contributor_comma_name("STEINBRENNER HAROLD Z.") == "STEINBRENNER, HAROLD Z."
    assert fl.contributor_comma_name("ZALUPSKI PATRICK") == "ZALUPSKI, PATRICK"
    # Already-comma'd / suffix-mangled FL form still surfaces the surname first.
    assert fl.contributor_comma_name("STEINBRENNER, III GEORGE M.").startswith("STEINBRENNER, ")
    assert fl.surname_of({"Contributor Name": "STEINBRENNER HAROLD Z."}) == "steinbrenner"
    assert fl.surname_of({"Contributor Name": "ZALUPSKI, PATRICK"}) == "zalupski"


def test_split_city_state_zip():
    assert fl.split_city_state_zip("TAMPA, FL 33623") == ("TAMPA", "FL", "33623")
    assert fl.split_city_state_zip("PONTE VEDRA BEACH, FL 32082-1234") == ("PONTE VEDRA BEACH", "FL", "32082")
    assert fl.split_city_state_zip("") == ("", "", "")


def test_parse_recipient_party_office():
    assert fl.parse_recipient("DeSantis, Ron  (REP)(GOV)") == ("DeSantis, Ron", "REP", "GOV")
    assert fl.parse_recipient("Some Committee For Stuff") == ("Some Committee For Stuff", None, None)
    assert fl.recipient_type_of({"Candidate/Committee": "DeSantis, Ron  (REP)(GOV)"}) == "candidate"
    assert fl.recipient_type_of({"Candidate/Committee": "Friends of Florida PAC"}) == "committee"


def test_date_and_amount():
    assert fl.parse_fl_date("09/21/2018") == "2018-09-21"
    assert fl.parse_fl_date("9/1/2020") == "2020-09-01"
    assert fl.parse_fl_date("") is None
    assert fl.parse_amount("1,000.00") == 1000.0
    assert fl.parse_amount("-250") == -250.0


def test_classifier_record_has_no_employer():
    rec = fl.to_classifier_record(_hal_row())
    assert rec["contributor_employer"] == ""
    assert rec["contributor_occupation"] == "SPORTS TEAM OWNER"
    assert rec["contributor_city"] == "TAMPA" and rec["contributor_state"] == "FL"


def test_state_donation_row_carries_party_office():
    row = fl.to_state_donation_row(
        _hal_row(**{"_tran": "abc"}),
        state_txn_id="FL:FL-DOE::abc", status=CONFIRMED, status_reason="r",
        signals_matched_json="[]", entity_slug="steinbrenner-hal", entity_kind="owner",
        parent_owner_slug=None, recipient_filer_id=None,
        recipient_name="Bondi, Pam", recipient_type="candidate",
        raw_payload_path="raw", ingested_at="2026-06-08T00:00:00Z",
    )
    assert row["jurisdiction"] == "FL" and row["source"] == "FL-DOE"
    assert row["recipient_party"] == "REP" and row["recipient_office"] == "ATG"
    assert row["contributor_employer_raw"] is None
    assert row["amount"] == 500.0 and row["date"] == "2013-09-24"


# ── Integration through the real classifier ──────────────────────────────────

def test_hal_confirmed_via_occupation_plus_city_state():
    c = classify(fl.to_classifier_record(_hal_row()), HAL)
    assert c is not None and c.status == CONFIRMED
    assert c.entity_slug == "steinbrenner-hal"


def test_george_not_attributed_to_hal():
    """The late George M. Steinbrenner III shares surname + Tampa + 'owner', but his
    first name keeps him OUT of Hal's attribution (the family collision)."""
    rec = fl.to_classifier_record(_hal_row(**{"Contributor Name": "STEINBRENNER, III GEORGE M."}))
    assert classify(rec, HAL) is None


def test_hal_probable_on_city_only():
    """Tampa/FL but an unknown occupation → one signal → PROBABLE."""
    rec = fl.to_classifier_record(_hal_row(Occupation="RETIRED"))
    c = classify(rec, HAL)
    assert c is not None and c.status == PROBABLE


def test_zalupski_confirmed_ponte_vedra_executive():
    row = {
        "Candidate/Committee": "Garrison, Sam  (REP)(STR)", "Date": "12/27/2019",
        "Amount": "1000.00", "Typ": "CHE", "Contributor Name": "ZALUPSKI PATRICK",
        "Address": "331 PABLO ROAD", "City State Zip": "PONTE VEDRA BEACH, FL 32082",
        "Occupation": "CONSTRUCTION CO. EXECUTIVE", "Inkind Desc": "",
    }
    c = classify(fl.to_classifier_record(row), ZAL)
    assert c is not None and c.status == CONFIRMED


def test_out_of_state_same_name_demoted():
    """A same-named Steinbrenner filing from outside FL (city+state present, not a
    documented residence) is demoted to UNCERTAIN by the address-contradiction rule."""
    rec = fl.to_classifier_record(_hal_row(**{"City State Zip": "NEW YORK, NY 10001"}))
    c = classify(rec, HAL)
    assert c is not None and c.status == UNCERTAIN


# ── Fetcher: form body, TSV parse, recipient inline, dedupe, funnel ──────────

def test_form_body_uses_list_mode_and_tab_export():
    body = fetch_fl.build_form_body("Steinbrenner").decode()
    assert "search_on=2" in body          # list of contributions (NOT 1 = list-only)
    assert "queryformat=2" in body        # tab-delimited file
    assert "clname=Steinbrenner" in body
    assert "Submit=Submit" in body


def test_parse_tsv_inlines_recipient_and_stamps_tran():
    text = _tsv(
        ["DeSantis, Ron  (REP)(GOV)", "09/21/2018", "1000.00", "CHE", "ZALUPSKI PATRICK",
         "1031 FIRST ST", "JACKSONVILLE BEACH, FL 32250", "HOME BUILDER", ""],
    )
    rows = fetch_fl.parse_tsv(text)
    assert len(rows) == 1
    assert rows[0]["_recipient_name"] == "DeSantis, Ron"
    assert rows[0]["_recipient_party"] == "REP" and rows[0]["_recipient_office"] == "GOV"
    assert rows[0]["_recipient_type"] == "candidate"
    assert rows[0]["_tran"] and len(rows[0]["_tran"]) == 16


def test_parse_tsv_skips_stray_html_error_line():
    text = HEADER + "\n<H1>Error in /cgi-bin/contrib.exe</H1>\n"
    assert fetch_fl.parse_tsv(text) == []


def test_tran_stable_and_content_sensitive():
    r = _hal_row()
    assert fetch_fl._content_tran(r) == fetch_fl._content_tran(dict(r))
    assert fetch_fl._content_tran(r) != fetch_fl._content_tran(_hal_row(Amount="999.00"))


def test_bucket_by_surname_and_dedupe():
    text = _tsv(
        ["Bondi, Pam  (REP)(ATG)", "09/24/2013", "500.00", "CHE", "STEINBRENNER HAROLD Z.",
         "1 STEINBRENNER DR", "TAMPA, FL 33614", "OWNER", ""],
        ["X (REP)(STR)", "01/01/2020", "100.00", "CHE", "SMITH JOHN", "1 A ST", "TAMPA, FL 33614", "X", ""],
    )
    rows = fetch_fl.parse_tsv(text)
    buckets = fetch_fl.bucket_rows_by_owner(rows, [("steinbrenner-hal", HAL)])
    assert len(buckets["steinbrenner-hal"]) == 1
    # dedupe collapses identical rows
    assert len(fetch_fl.dedupe(rows + rows)) == 2


def test_resolver_reads_inline_recipient():
    rows = fetch_fl.parse_tsv(_tsv(
        ["Bondi, Pam  (REP)(ATG)", "09/24/2013", "500.00", "CHE", "STEINBRENNER HAROLD Z.",
         "1 STEINBRENNER DR", "TAMPA, FL 33614", "OWNER", ""]))
    rec = fetch_fl.make_recipient_resolver()(rows[0])
    assert rec["name"] == "Bondi, Pam" and rec["type"] == "candidate" and rec["filer_id"] is None


# ── Per-owner jurisdiction exclusion (the Fisher/94111 doppelganger guard) ───

def test_exclude_state_jurisdictions_filters_owner():
    from scripts.ingest_state import filter_excluded_owners
    fisher = {"slug": "fisher-john", "exclude_state_jurisdictions": ["FL"]}
    owners = [("fisher-john", fisher), ("steinbrenner-hal", HAL)]
    # Excluded for FL...
    kept_fl = filter_excluded_owners(owners, "FL")
    assert [s for s, _ in kept_fl] == ["steinbrenner-hal"]
    # ...but untouched for every other jurisdiction (CA/federal reach is preserved).
    kept_ca = filter_excluded_owners(owners, "CA")
    assert [s for s, _ in kept_ca] == ["fisher-john", "steinbrenner-hal"]

"""Tests for the Minnesota CFB adapter + fetcher (scripts/mn_adapter.py,
scripts/fetch_mn.py).

Three things are asserted:
  1. An MN-shaped row, mapped by the adapter, flows through the UNCHANGED classifier
     (resolve_entities.classify) into the right three-tier bucket — proving the
     federal classifier is reused verbatim for MN data.
  2. The disambiguation that motivates MN: "Pohlad" is a large Twin Cities family
     (uncle Jim + a dozen relatives all file individual contributions), and MN
     discloses no city/state — so Joe is separated only by his strong ZIP (55436)
     and Tom only by his strong employer (Carousel Motor Group); a bare same-surname
     relative must NOT be attributed to either.
  3. The fetcher's parse / inline recipient join / content-hash dedup / surname
     funnel behave (incl. the comma-form surname extraction unique to MN).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from scripts import fetch_mn
from scripts import mn_adapter as mn
from scripts.resolve_entities import CONFIRMED, PROBABLE, UNCERTAIN, classify

# Minimal owner signal blocks (shape mirrors owners/*.yaml). Joe is ZIP-gated,
# Tom is employer-gated — exactly the live calibration.
JOE = {
    "slug": "pohlad-joe",
    "name": "Joseph C. Pohlad",
    "name_variants": ["Joe Pohlad", "Joseph Pohlad", "Pohlad, Joe", "Pohlad, Joseph"],
    "verifying_signals": {"employers": ["Minnesota Twins", "Pohlad Companies"]},
    "strong_signals": {"zip_codes": ["55436", "55424"]},
    "negative_signals": {},
}
TOM = {
    "slug": "pohlad-tom",
    "name": "Thomas Pohlad",
    "name_variants": ["Tom Pohlad", "Thomas Pohlad", "Pohlad, Thomas", "Pohlad, Tom"],
    "verifying_signals": {"employers": ["Minnesota Twins", "Pohlad Companies"]},
    "strong_signals": {"employers": ["Carousel Motor Group", "Carousel Motors"]},
    "negative_signals": {},
}

HEADER = [
    "Recipient reg num", "Recipient", "Recipient type", "Recipient subtype", "Amount",
    "Receipt date", "Year", "Contributor", "Contrib Reg Num", "Contrib type",
    "Receipt type", "In kind?", "In-kind descr", "Contrib zip", "Contrib Employer name",
]


def _row(**over) -> dict:
    row = {
        "Recipient reg num": "18135",
        "Recipient": "Walz, Tim Gov Committee",
        "Recipient type": "PCC",
        "Recipient subtype": "",
        "Amount": "4000.0000",
        "Receipt date": "2025-04-30",
        "Year": "2025",
        "Contributor": "Pohlad, Joe",
        "Contrib Reg Num": "",
        "Contrib type": "Individual",
        "Receipt type": "Contribution",
        "In kind?": "No",
        "In-kind descr": "",
        "Contrib zip": "55436",
        "Contrib Employer name": "Family Business",
    }
    row.update(over)
    return row


# ── Pure adapter mapping ─────────────────────────────────────────────────────

def test_to_classifier_record_maps_fields():
    rec = mn.to_classifier_record(_row())
    assert rec["contributor_name"] == "Pohlad, Joe"
    assert rec["contributor_employer"] == "Family Business"
    assert rec["contributor_zip"] == "55436"
    # MN discloses no occupation/city/state.
    assert rec["contributor_occupation"] == ""
    assert rec["contributor_city"] == "" and rec["contributor_state"] == ""


def test_surname_is_before_the_comma():
    assert mn.surname_of(_row()) == "pohlad"
    assert mn.surname_of(_row(Contributor="Pohlad, Donna Miller")) == "pohlad"
    # Org contributor with no comma → last token.
    assert mn.surname_of(_row(Contributor="Minnesota DFL Party")) == "party"
    assert mn.surname_of(_row(Contributor="")) == ""


def test_date_parsing():
    assert mn.parse_mn_date("2025-04-30") == "2025-04-30"
    assert mn.parse_mn_date("04/30/2025") == "2025-04-30"
    assert mn.parse_mn_date("not a date") is None
    assert mn.parse_mn_date("") is None


def test_amount_parsing():
    assert mn.parse_amount("4000.0000") == 4000.0
    assert mn.parse_amount("1,150") == 1150.0
    assert mn.parse_amount("-200") == -200.0   # returned contributions
    assert mn.parse_amount("") is None
    assert mn.parse_amount("abc") is None


def test_election_cycle_is_calendar_year():
    assert mn.election_cycle_from_date("2025-04-30") == 2025
    assert mn.election_cycle_from_date(None) is None


def test_recipient_type_mapping():
    assert mn.recipient_type_of(_row()) == "candidate"            # PCC
    assert mn.recipient_type_of(_row(**{"Recipient type": "PTU"})) == "committee"
    assert mn.recipient_type_of(_row(**{"Recipient type": "PCF"})) == "committee"
    assert mn.recipient_type_of(_row(**{"Recipient type": ""})) is None


def test_state_donation_row_shape():
    row = mn.to_state_donation_row(
        _row(),
        state_txn_id="MN:abc", status=CONFIRMED, status_reason="r",
        signals_matched_json="[]", entity_slug="pohlad-joe", entity_kind="owner",
        parent_owner_slug=None, recipient_filer_id="18135",
        recipient_name="Walz, Tim Gov Committee", recipient_type="candidate",
        raw_payload_path="raw", ingested_at="2026-06-08T00:00:00Z",
    )
    assert row["jurisdiction"] == "MN" and row["source"] == "MN-CFB"
    assert row["amount"] == 4000.0 and row["date"] == "2025-04-30"
    assert row["recipient_name"] == "Walz, Tim Gov Committee"
    assert row["contributor_employer_raw"] == "Family Business"
    assert row["contributor_zip"] == "55436"
    assert row["contributor_city"] is None and row["contributor_state"] is None
    assert row["recipient_party"] is None and row["recipient_office"] is None


# ── Integration through the real classifier ──────────────────────────────────

def test_joe_confirmed_via_strong_zip():
    """No employer/city/state signal, but the documented strong ZIP confirms Joe."""
    c = classify(mn.to_classifier_record(_row()), JOE)
    assert c is not None and c.status == CONFIRMED
    assert c.entity_slug == "pohlad-joe"
    assert any(s.startswith("strong_zip:55436") for s in c.signals_matched)


def test_tom_confirmed_via_strong_employer():
    rec = mn.to_classifier_record(
        _row(Contributor="Pohlad, Thomas", **{"Contrib Employer name": "Carousel Motor Group", "Contrib zip": "55331"})
    )
    c = classify(rec, TOM)
    assert c is not None and c.status == CONFIRMED
    assert any(s.startswith("strong_employer:") for s in c.signals_matched)


def test_joe_probable_on_verifying_employer_only():
    """Wrong ZIP but a verifying employer (Pohlad Companies) → one signal → PROBABLE."""
    rec = mn.to_classifier_record(
        _row(**{"Contrib Employer name": "Pohlad Companies", "Contrib zip": "55401"})
    )
    c = classify(rec, JOE)
    assert c is not None and c.status == PROBABLE


def test_joe_uncertain_name_only():
    """A Joe Pohlad row with neither a strong ZIP nor a known employer stays UNCERTAIN
    (no city/state to corroborate) — conservative, not a false CONFIRMED."""
    rec = mn.to_classifier_record(
        _row(**{"Contrib Employer name": "Family Business", "Contrib zip": "55555"})
    )
    c = classify(rec, JOE)
    assert c is not None and c.status == UNCERTAIN


def test_uncle_james_not_attributed_to_joe():
    """Uncle Jim files as 'Pohlad, James' / Pohlad Companies / 55410 — same surname,
    same shared employer, but his first name keeps him OUT of Joe's attribution."""
    rec = mn.to_classifier_record(
        _row(Contributor="Pohlad, James", **{"Contrib Employer name": "Pohlad Companies", "Contrib zip": "55410"})
    )
    assert classify(rec, JOE) is None


def test_uncle_james_not_attributed_to_tom():
    rec = mn.to_classifier_record(
        _row(Contributor="Pohlad, James", **{"Contrib Employer name": "Pohlad Companies", "Contrib zip": "55410"})
    )
    assert classify(rec, TOM) is None


# ── Fetcher: parse, recipient inline-join, hash, bucket, dedupe ───────────────

def _csv_text(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in HEADER})
    return buf.getvalue()


def test_iter_contributions_fh_inlines_recipient_and_stamps_tran():
    rows = list(fetch_mn.iter_contributions_fh(io.StringIO(_csv_text([_row()]))))
    assert len(rows) == 1
    assert rows[0]["_recipient_name"] == "Walz, Tim Gov Committee"
    assert rows[0]["_recipient_type"] == "candidate"
    assert rows[0]["_tran"] and len(rows[0]["_tran"]) == 16


def test_tran_is_stable_and_distinguishes():
    a = fetch_mn._content_tran(_row())
    assert a == fetch_mn._content_tran(_row())                      # stable
    assert a != fetch_mn._content_tran(_row(Amount="5000.0000"))    # content-sensitive


def test_bucket_rows_by_owner_funnels_by_surname():
    rows = list(fetch_mn.iter_contributions_fh(io.StringIO(_csv_text([
        _row(),
        _row(Contributor="Smith, John", **{"Contrib zip": "55101"}),
    ]))))
    buckets = fetch_mn.bucket_rows_by_owner(rows, [("pohlad-joe", JOE)])
    assert len(buckets["pohlad-joe"]) == 1
    assert buckets["pohlad-joe"][0]["Contributor"] == "Pohlad, Joe"


def test_dedupe_on_content_hash():
    rows = list(fetch_mn.iter_contributions_fh(io.StringIO(_csv_text([
        _row(), _row(), _row(Amount="9999.0000"),
    ]))))
    assert len(fetch_mn.dedupe(rows)) == 2


def test_resolver_reads_inline_recipient():
    rows = list(fetch_mn.iter_contributions_fh(io.StringIO(_csv_text([_row()]))))
    rec = fetch_mn.make_recipient_resolver()(rows[0])
    assert rec["name"] == "Walz, Tim Gov Committee"
    assert rec["type"] == "candidate"
    assert rec["filer_id"] == "18135"


def test_iter_dir_reads_csv(tmp_path: Path):
    (tmp_path / "contributions.csv").write_text(_csv_text([_row(), _row(Amount="2.0")]), encoding="utf-8")
    out = list(fetch_mn.iter_dir(tmp_path))
    assert len(out) == 2
    assert all(r["_recipient_name"] == "Walz, Tim Gov Committee" for r in out)


def test_resolve_download_url_matches_anchor_text():
    html = (
        '<a href="/reports-and-data/self-help/data-downloads/campaign-finance/?download=-115995361">'
        'Contributions received by all candidates - 2015 to present</a>'
        '<a href="/reports-and-data/self-help/data-downloads/campaign-finance/?download=-2113865252">'
        'Contributions received by all entities - 2015 to present</a>'
    )
    url = fetch_mn.resolve_download_url(html, "all entities")
    assert url == "https://cfb.mn.gov/reports-and-data/self-help/data-downloads/campaign-finance/?download=-2113865252"
    assert fetch_mn.resolve_download_url(html, "no such dataset") is None

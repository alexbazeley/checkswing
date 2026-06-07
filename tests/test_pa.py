"""Tests for the Pennsylvania adapter + fetcher (scripts/pa_adapter.py, fetch_pa.py).

Covers field mapping, the up-to-3 amount explosion, recipient join from the filer
file, content-hash dedup, surname bucketing, the live per-year **zip** streaming +
multi-year merge, and — crucially — that a PA row flows through the UNCHANGED
classifier to the right tier.

Fixtures use the current pa.gov export format: per-year files `contrib_<YEAR>.txt`
and `filer_<YEAR>.txt` (header-bearing CSV; the contributions file no longer carries
the pre-2026 FILERCODE column). The adapter reads columns by name, so order is
immaterial.
"""
from __future__ import annotations

import zipfile

from scripts import fetch_pa, pa_adapter
from scripts.resolve_entities import CONFIRMED, PROBABLE, UNCERTAIN, classify


CONTRIB_HEADER = (
    "CampaignFinanceID,FilerID,EYEAR,SubmittedDate,CYCLE,Section,CONTRIBUTOR,"
    "ADDRESS1,ADDRESS2,CITY,STATE,ZIPCODE,OCCUPATION,ENAME,EADDRESS1,EADDRESS2,ECITY,ESTATE,"
    "EZIPCODE,CONTDATE1,CONTAMT1,CONTDATE2,CONTAMT2,CONTDATE3,CONTAMT3,CONTDESC"
)
FILER_HEADER = (
    "CampaignfinanceID,FILERID,EYEAR,SubmittedDate,CYCLE,AMMEND,TERMINATE,FILERTYPE,FILERNAME,"
    "OFFICE,DISTRICT,PARTY,ADDRESS1,ADDRESS2,CITY,STATE,ZIPCODE,COUNTY,PHONE,BEGINNING,MONETARY,INKIND"
)


def _contrib_csv(*rows: str) -> str:
    return "﻿" + CONTRIB_HEADER + "\n" + "\n".join(rows) + "\n"


def _filer_csv(*rows: str) -> str:
    return "﻿" + FILER_HEADER + "\n" + "\n".join(rows) + "\n"


OWNER = {
    "slug": "middleton-john",
    "name": "John S. Middleton",
    "name_variants": ["John Middleton", "John S Middleton", "John S. Middleton", "Middleton, John"],
    "verifying_signals": {
        "cities": ["bryn mawr"], "states": ["PA"],
        "employers": ["Bradford Holdings", "Middleton"], "occupations": ["investor"],
    },
    "strong_signals": {}, "negative_signals": {},
}


# CFID, FilerID, EYEAR, Submitted, CYCLE, Section, CONTRIBUTOR, ADDR1, ADDR2,
# CITY, STATE, ZIP, OCC, ENAME, ..., CONTDATE1, CONTAMT1, CONTDATE2, CONTAMT2, CONTDATE3, CONTAMT3, DESC
def _row(contributor="John S Middleton", city="Bryn Mawr", state="PA", ename="Bradford Holdings",
         occ="Investor", a1="5000.00", d1="20240315", a2="", d2="", cfid="555", filer="900"):
    return (f"{cfid},{filer},2024,2024-04-11,2,IB,{contributor},1 Main,NULL,{city},{state},19010,"
            f"{occ},{ename},NULL,NULL,NULL,NULL,NULL,{d1},{a1},{d2},{a2},NULL,NULL,NULL")


def _write(tmp_path, contrib_rows, filer_rows, year="2024"):
    c = tmp_path / f"contrib_{year}.txt"
    f = tmp_path / f"filer_{year}.txt"
    c.write_text(_contrib_csv(*contrib_rows), encoding="utf-8")
    f.write_text(_filer_csv(*filer_rows), encoding="utf-8")
    return c, f


def _write_zip(tmp_path, contrib_rows, filer_rows, year="2024"):
    z = tmp_path / f"{year}.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(f"contrib_{year}.txt", _contrib_csv(*contrib_rows))
        zf.writestr(f"filer_{year}.txt", _filer_csv(*filer_rows))
    return z


# CFID=555, FILERID=900, FILERTYPE=CAN, FILERNAME=Friends..., OFFICE=STH, DISTRICT=1, PARTY=DEM
FILER_ROW = "555,900,2024,2024-01-01,2,N,N,CAN,Friends of a PA Senator,STH,1,DEM,1 St,NULL,Phila,PA,19000,Phila,NULL,0,0,0"


def test_date_and_amount_parsing():
    assert pa_adapter.parse_pa_date("20240315") == "2024-03-15"
    assert pa_adapter.parse_pa_date("2024-03-15") == "2024-03-15"
    assert pa_adapter.parse_pa_date("NULL") is None
    assert pa_adapter.parse_amount("5,000.00") == 5000.0
    assert pa_adapter.parse_amount("NULL") is None


def test_build_filer_index_has_party(tmp_path):
    _, f = _write(tmp_path, [_row()], [FILER_ROW])
    idx = fetch_pa.build_filer_index(f)
    assert idx["900"]["name"] == "Friends of a PA Senator"
    assert idx["900"]["party"] == "DEM"


def test_iter_explodes_multiple_amounts(tmp_path):
    # One row with TWO (date, amount) pairs → two contributions.
    c, f = _write(tmp_path, [_row(a1="5000.00", d1="20240315", a2="2500.00", d2="20240401")], [FILER_ROW])
    idx = fetch_pa.build_filer_index(f)
    rows = list(fetch_pa.iter_contributions(c, idx))
    assert len(rows) == 2
    amts = sorted(r["_amount"] for r in rows)
    assert amts == ["2500.00", "5000.00"]
    # Recipient pre-joined (name + party) onto each exploded row.
    assert rows[0]["_recipient_name"] == "Friends of a PA Senator"
    assert rows[0]["_recipient_party"] == "DEM"
    # Stable distinct content-hash ids.
    assert rows[0]["_tran"] != rows[1]["_tran"] and len(rows[0]["_tran"]) == 16


def test_dedupe_on_content_hash(tmp_path):
    c, f = _write(tmp_path, [_row(), _row()], [FILER_ROW])  # identical rows
    rows = list(fetch_pa.iter_contributions(c, fetch_pa.build_filer_index(f)))
    assert len(rows) == 2
    assert len(fetch_pa.dedupe(rows)) == 1  # same content → one


def test_bucket_by_owner(tmp_path):
    c, f = _write(tmp_path, [_row(contributor="John S Middleton"),
                             _row(contributor="Jane Smith", cfid="556")], [FILER_ROW])
    rows = list(fetch_pa.iter_contributions(c, fetch_pa.build_filer_index(f)))
    buckets = fetch_pa.bucket_rows_by_owner(rows, [("middleton-john", OWNER)])
    assert len(buckets["middleton-john"]) == 1


# ── Live per-year zip + multi-year streaming ─────────────────────────────────

def test_iter_dir_reads_zip(tmp_path):
    _write_zip(tmp_path, [_row()], [FILER_ROW], year="2024")
    rows = list(fetch_pa.iter_dir(tmp_path))
    assert len(rows) == 1
    assert rows[0]["_recipient_name"] == "Friends of a PA Senator"
    assert rows[0]["CITY"] == "Bryn Mawr"


def test_iter_dir_merges_multiple_year_zips(tmp_path):
    # Two cycle zips; the 2026 contribution's recipient lives in the 2024 filer file
    # → the merged-across-years filer index must still resolve it.
    _write_zip(tmp_path, [_row(cfid="555", d1="20240315")], [FILER_ROW], year="2024")
    _write_zip(tmp_path, [_row(cfid="777", d1="20260110", filer="900")], [], year="2026")
    rows = list(fetch_pa.iter_dir(tmp_path))
    assert len(rows) == 2
    # Both resolve the recipient from the shared (merged) filer index.
    assert all(r["_recipient_name"] == "Friends of a PA Senator" for r in rows)
    dates = sorted(r["_date"] for r in rows)
    assert dates == ["20240315", "20260110"]


def test_iter_dir_extracted_fallback(tmp_path):
    # No zip present → falls back to extracted contrib_*/filer_* files.
    _write(tmp_path, [_row()], [FILER_ROW], year="2022")
    rows = list(fetch_pa.iter_dir(tmp_path))
    assert len(rows) == 1


def test_member_name_matchers():
    assert fetch_pa._is_contrib_name("contrib_2026.txt")
    assert fetch_pa._is_contrib_name("May 2024 ECF Contribution.txt")  # legacy tolerated
    assert not fetch_pa._is_contrib_name("receipt_2026.txt")
    assert fetch_pa._is_filer_name("filer_2026.txt")
    assert not fetch_pa._is_filer_name("contrib_2026.txt")


# ── Integration through the real classifier ─────────────────────────────────

def _exploded(tmp_path, **kw):
    c, f = _write(tmp_path, [_row(**kw)], [FILER_ROW])
    return list(fetch_pa.iter_contributions(c, fetch_pa.build_filer_index(f)))[0]


def test_classifier_confirmed_employer_plus_city(tmp_path):
    rec = pa_adapter.to_classifier_record(_exploded(tmp_path))
    c = classify(rec, OWNER)
    assert c is not None and c.status == CONFIRMED


def test_classifier_probable_city_only(tmp_path):
    rec = pa_adapter.to_classifier_record(_exploded(tmp_path, ename="", occ=""))
    c = classify(rec, OWNER)
    assert c is not None and c.status == PROBABLE


def test_classifier_uncertain_wrong_city(tmp_path):
    rec = pa_adapter.to_classifier_record(_exploded(tmp_path, city="Pittsburgh", ename="", occ=""))
    c = classify(rec, OWNER)
    assert c is not None and c.status == UNCERTAIN


def test_classifier_filters_non_matching_name(tmp_path):
    rec = pa_adapter.to_classifier_record(_exploded(tmp_path, contributor="Bob Jones"))
    assert classify(rec, OWNER) is None


def test_to_state_donation_row_carries_party(tmp_path):
    row = _exploded(tmp_path)
    out = pa_adapter.to_state_donation_row(
        row, state_txn_id="PA:PA-DOS:555:abc", status="CONFIRMED", status_reason="x",
        signals_matched_json="[]", entity_slug="middleton-john", entity_kind="owner",
        parent_owner_slug=None, recipient_filer_id="900", recipient_name="Friends of a PA Senator",
        recipient_type="can", raw_payload_path="data/raw/state/pa/x", ingested_at="2026-06-04T00:00:00Z",
    )
    assert out["amount"] == 5000.0 and out["date"] == "2024-03-15"
    assert out["recipient_party"] == "DEM"
    assert out["jurisdiction"] == "PA" and out["source"] == "PA-DOS"

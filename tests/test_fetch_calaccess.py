"""Tests for the CAL-ACCESS parsing + resolver layer (scripts/fetch_calaccess.py).

The network download is excluded from the tested surface by design; what matters
for data correctness — TSV parsing, the filer index, the recipient resolver, and
the surname pre-filter — is covered here against fixture files.
"""
from __future__ import annotations

from scripts import fetch_calaccess as fc


RCPT_TSV = "\t".join(
    ["CTRIB_NAML", "CTRIB_NAMF", "CTRIB_EMP", "AMOUNT", "RCPT_DATE", "TRAN_ID", "FILING_ID", "FILER_ID"]
) + "\n" + "\n".join(
    [
        "\t".join(["MORENO", "ARTURO", "Outdoor Systems", "1500.00", "2018-06-01", "T1", "F100", "9001"]),
        "\t".join(["SMITH", "JOHN", "Acme", "250.00", "2018-07-01", "T2", "F101", "9002"]),
        "\t".join(["MORENO-SANCHEZ", "MARIA", "Foo", "100.00", "2018-08-01", "T3", "F102", "9001"]),
    ]
)

FILERNAME_TSV = "\t".join(["FILER_ID", "NAML", "NAMF", "FILER_TYPE"]) + "\n" + "\n".join(
    [
        "\t".join(["9001", "Friends of Someone", "", "CANDIDATE"]),
        "\t".join(["9002", "Some Committee", "", "RECIPIENT COMMITTEE"]),
    ]
)


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_iter_rcpt_rows(tmp_path):
    p = _write(tmp_path, "RCPT_CD.TSV", RCPT_TSV)
    rows = list(fc.iter_rcpt_rows(p))
    assert len(rows) == 3
    assert rows[0]["CTRIB_NAML"] == "MORENO"
    assert rows[0]["AMOUNT"] == "1500.00"
    assert rows[0]["FILER_ID"] == "9001"


def test_build_filer_index_and_resolver(tmp_path):
    p = _write(tmp_path, "FILERNAME_CD.TSV", FILERNAME_TSV)
    index = fc.build_filer_index(p)
    assert index["9001"]["name"] == "Friends of Someone"
    assert index["9001"]["type"] == "candidate"
    resolve = fc.make_recipient_resolver(index)
    rcpt = {"FILER_ID": "9001"}
    assert resolve(rcpt) == {"filer_id": "9001", "name": "Friends of Someone", "type": "candidate"}
    # Unknown filer → honest empty name, still keyed.
    assert resolve({"FILER_ID": "7777"}) == {"filer_id": "7777", "name": "", "type": None}


def test_prefilter_by_surnames():
    owner = {"name_variants": ["Arturo Moreno", "Moreno, Arturo"]}
    surnames = fc._surname_set(owner)
    assert surnames == {"moreno"}
    rows = [
        {"CTRIB_NAML": "MORENO"},
        {"CTRIB_NAML": "SMITH"},
        {"CTRIB_NAML": "MORENO-SANCHEZ"},  # substring match — classifier decides precisely
    ]
    kept = list(fc.prefilter_by_surnames(rows, surnames))
    assert [r["CTRIB_NAML"] for r in kept] == ["MORENO", "MORENO-SANCHEZ"]


def test_candidate_rows_for_owner_end_to_end(tmp_path):
    p = _write(tmp_path, "RCPT_CD.TSV", RCPT_TSV)
    owner = {"name_variants": ["Arturo Moreno"]}
    rows = fc.candidate_rows_for_owner(p, owner)
    # Funnel keeps the two MORENO* rows, drops SMITH.
    assert {r["CTRIB_NAML"] for r in rows} == {"MORENO", "MORENO-SANCHEZ"}


def test_empty_surnames_passes_all_through():
    rows = [{"CTRIB_NAML": "X"}, {"CTRIB_NAML": "Y"}]
    assert list(fc.prefilter_by_surnames(rows, set())) == rows


# ── Zip streaming + multi-owner bucketing (the live-ingest path) ─────────────


def _make_zip(tmp_path):
    """A tiny dbwebexport.zip with the two tables nested under CalAccess/DATA/."""
    import zipfile

    zpath = tmp_path / "dbwebexport.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("CalAccess/DATA/RCPT_CD.TSV", RCPT_TSV)
        zf.writestr("CalAccess/DATA/FILERNAME_CD.TSV", FILERNAME_TSV)
    return zpath


def test_iter_rcpt_rows_from_zip(tmp_path):
    z = _make_zip(tmp_path)
    rows = list(fc.iter_rcpt_rows_from_zip(z))
    assert len(rows) == 3
    assert rows[0]["CTRIB_NAML"] == "MORENO"


def test_build_filer_index_from_zip(tmp_path):
    z = _make_zip(tmp_path)
    index = fc.build_filer_index_from_zip(z)
    assert index["9001"]["name"] == "Friends of Someone"


def test_bucket_rows_by_owner(tmp_path):
    z = _make_zip(tmp_path)
    owners = [
        ("moreno-arte", {"name_variants": ["Arturo Moreno"]}),
        ("smith-john", {"name_variants": ["John Smith"]}),
    ]
    buckets = fc.bucket_rows_by_owner(fc.iter_rcpt_rows_from_zip(z), owners)
    # MORENO + MORENO-SANCHEZ → moreno-arte; SMITH → smith-john.
    assert {r["CTRIB_NAML"] for r in buckets["moreno-arte"]} == {"MORENO", "MORENO-SANCHEZ"}
    assert {r["CTRIB_NAML"] for r in buckets["smith-john"]} == {"SMITH"}


# ── Recipient resolution via cover pages + amendment dedup ───────────────────

CVR_TSV = "\t".join(["FILING_ID", "AMEND_ID", "FILER_ID", "FILER_NAML", "CAND_NAML", "BAL_NAME"]) + "\n" + "\n".join(
    [
        "\t".join(["F100", "0", "9001", "Friends of Someone", "Doe, Jane", ""]),
        "\t".join(["F100", "1", "9001", "Friends of Jane Doe", "Doe, Jane", ""]),  # later amendment wins
        "\t".join(["F300", "0", "9100", "Yes on Prop 99", "", "Proposition 99"]),
    ]
)


def test_recipient_index_from_cvr(tmp_path):
    import zipfile

    z = tmp_path / "dbwebexport.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("CalAccess/DATA/CVR_CAMPAIGN_DISCLOSURE_CD.TSV", CVR_TSV)
    index = fc.build_recipient_index_from_zip(z)
    # Highest AMEND_ID wins for F100.
    assert index["F100"]["name"] == "Friends of Jane Doe"
    assert index["F100"]["type"] == "candidate"
    assert index["F300"]["type"] == "ballot_measure"
    resolve = fc.make_recipient_resolver_by_filing(index)
    assert resolve({"FILING_ID": "F100"})["name"] == "Friends of Jane Doe"
    assert resolve({"FILING_ID": "F999"}) == {"filer_id": None, "name": "", "type": None}


def _r(**kw):
    base = {"FILING_ID": "F1", "TRAN_ID": "T1", "AMEND_ID": "0", "AMOUNT": "100",
            "RCPT_DATE": "2018-06-01", "CTRIB_NAML": "FISHER", "CTRIB_NAMF": "JOHN"}
    base.update(kw)
    return base


def test_dedupe_amendments_keeps_latest():
    rows = [_r(AMEND_ID="0", AMOUNT="100"), _r(AMEND_ID="2", AMOUNT="100"), _r(AMEND_ID="1", AMOUNT="100")]
    out = fc.dedupe_receipts(rows)
    assert len(out) == 1 and out[0]["AMEND_ID"] == "2"


def test_dedupe_collapses_cross_filing_same_tran():
    # Same TRAN_ID + amount + date + donor on two different filings → one row.
    rows = [_r(FILING_ID="F100"), _r(FILING_ID="F200")]
    out = fc.dedupe_receipts(rows)
    assert len(out) == 1
    assert out[0]["FILING_ID"] == "F200"  # deterministic: max (AMEND_ID, FILING_ID)


def test_dedupe_preserves_genuinely_separate_contributions():
    # Two real same-day same-amount gifts carry DISTINCT TRAN_IDs within a filing.
    rows = [_r(TRAN_ID="T1"), _r(TRAN_ID="T2")]
    out = fc.dedupe_receipts(rows)
    assert len(out) == 2


def test_dedupe_distinct_donors_not_merged():
    rows = [_r(CTRIB_NAMF="JOHN"), _r(CTRIB_NAMF="ROBERT")]
    out = fc.dedupe_receipts(rows)
    assert len(out) == 2

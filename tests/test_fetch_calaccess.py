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

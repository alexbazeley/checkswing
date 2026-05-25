"""Tests for scripts/fetch_committees.py — committee enrichment fetcher."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses

from scripts import fetch_committees
from scripts.fetch_committees import (
    COMMITTEE_DETAIL_ENDPOINT,
    COMMITTEE_TOTALS_ENDPOINT,
    fetch_committee_detail,
    fetch_committee_totals,
    parse_committee_detail,
    parse_committee_totals_row,
)
from scripts.fetch_fec import BASE_URL, FECClient


@pytest.fixture
def patched_raw_dir(tmp_path, monkeypatch):
    """Re-root data/raw to a tmp dir so persists don't leak into the real archive."""
    fake_raw = tmp_path / "raw"
    fake_raw.mkdir()
    monkeypatch.setattr(fetch_committees, "RAW_DIR", fake_raw)
    return fake_raw


@pytest.fixture
def fec_client(monkeypatch):
    """A FECClient with a fake API key and no real network calls."""
    monkeypatch.setenv("FEC_API_KEY", "test-key")
    return FECClient()


@responses.activate
def test_fetch_committee_detail_persists_raw_and_returns_row(
    patched_raw_dir, fec_client
):
    cmte_id = "C00012345"
    fake_response = {
        "results": [
            {
                "committee_id": cmte_id,
                "name": "ACME PAC",
                "designation": "B",
                "designation_full": "Lobbyist/Registrant PAC",
                "committee_type": "O",
                "committee_type_full": "Super PAC (Independent Expenditure-Only)",
                "party": "REP",
                "party_full": "Republican Party",
                "treasurer_name": "Jane Doe",
                "city": "Washington",
                "state": "DC",
                "first_file_date": "2014-05-01",
                "last_file_date": "2024-10-15",
                "cycles": [2014, 2016, 2018, 2020, 2022, 2024],
            }
        ]
    }
    responses.add(
        responses.GET,
        BASE_URL + COMMITTEE_DETAIL_ENDPOINT.format(committee_id=cmte_id),
        json=fake_response,
        status=200,
    )

    row, raw_path = fetch_committee_detail(fec_client, cmte_id)

    assert row["committee_id"] == cmte_id
    assert row["name"] == "ACME PAC"
    # Raw payload landed in _committees/<id>/
    assert raw_path.parent.name == cmte_id
    assert raw_path.parent.parent.name == "_committees"
    # Envelope shape
    envelope = json.loads(raw_path.read_text())
    assert envelope["_meta"]["committee_id"] == cmte_id
    assert "fetched_at" in envelope["_meta"]
    assert envelope["response"] == fake_response


@responses.activate
def test_fetch_committee_detail_raises_on_zero_results(patched_raw_dir, fec_client):
    cmte_id = "C99999999"
    responses.add(
        responses.GET,
        BASE_URL + COMMITTEE_DETAIL_ENDPOINT.format(committee_id=cmte_id),
        json={"results": []},
        status=200,
    )

    with pytest.raises(RuntimeError, match="no committee record"):
        fetch_committee_detail(fec_client, cmte_id)


@responses.activate
def test_fetch_committee_totals_returns_all_cycle_rows(patched_raw_dir, fec_client):
    cmte_id = "C00012345"
    fake_response = {
        "results": [
            {"cycle": 2024, "receipts": 1_000_000.0, "disbursements": 900_000.0},
            {"cycle": 2022, "receipts": 500_000.0, "disbursements": 480_000.0},
        ]
    }
    responses.add(
        responses.GET,
        BASE_URL + COMMITTEE_TOTALS_ENDPOINT.format(committee_id=cmte_id),
        json=fake_response,
        status=200,
    )

    rows, raw_path = fetch_committee_totals(fec_client, cmte_id)

    assert len(rows) == 2
    assert rows[0]["cycle"] == 2024
    # Raw envelope persisted
    envelope = json.loads(raw_path.read_text())
    assert envelope["response"] == fake_response
    assert envelope["_meta"]["params"]["per_page"] == 100


def test_parse_committee_detail_maps_fec_fields():
    fec_row = {
        "committee_id": "C00012345",
        "name": "ACME PAC",
        "designation": "B",
        "designation_full": "Leadership PAC",
        "committee_type": "O",
        "committee_type_full": "Super PAC",
        "party": "REP",
        "party_full": "Republican",
        "organization_type": "C",
        "affiliated_committee_name": "ACME CORP",
        "candidate_ids": ["H8XX00001"],
        "treasurer_name": "Jane Doe",
        "custodian_name_full": "John Smith",
        "city": "Austin",
        "state": "TX",
        "zip": "78701",
        "filing_frequency": "M",
        "first_file_date": "2014-05-01",
        "last_file_date": "2024-10-15",
        "last_f1_date": "2014-05-15",
        "cycles": [2014, 2016, 2024],
    }
    out = parse_committee_detail(fec_row)
    assert out["committee_id"] == "C00012345"
    assert out["designation_label"] == "Leadership PAC"
    assert out["committee_type_label"] == "Super PAC"
    assert out["affiliated_committee_name"] == "ACME CORP"
    assert json.loads(out["candidate_ids"]) == ["H8XX00001"]
    assert json.loads(out["cycles"]) == [2014, 2016, 2024]
    assert out["is_terminated"] == 0


def test_parse_committee_detail_handles_missing_fields():
    out = parse_committee_detail({"committee_id": "C00012345", "name": "X"})
    assert out["committee_id"] == "C00012345"
    assert out["name"] == "X"
    assert out["designation"] is None
    assert out["committee_type_label"] is None
    assert json.loads(out["candidate_ids"]) == []
    assert json.loads(out["cycles"]) == []
    assert out["is_terminated"] == 0


def test_parse_committee_detail_marks_terminated():
    out = parse_committee_detail({"committee_id": "C00012345", "name": "X", "is_terminated": True})
    assert out["is_terminated"] == 1


def test_parse_committee_totals_row_fills_cycle_from_coverage_end_when_missing():
    out = parse_committee_totals_row(
        "C00012345",
        {
            "receipts": 100.0,
            "disbursements": 50.0,
            "coverage_end_date": "2023-12-31",
        },
    )
    # 2023 is odd → bumps to 2024 cycle
    assert out["cycle"] == 2024
    assert out["receipts"] == 100.0


def test_parse_committee_totals_row_preserves_explicit_cycle():
    out = parse_committee_totals_row(
        "C00012345",
        {"cycle": 2020, "receipts": 100.0, "coverage_end_date": "2019-06-30"},
    )
    assert out["cycle"] == 2020

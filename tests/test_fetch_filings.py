"""Tests for scripts/fetch_filings.py — filings batch fetcher."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses

from scripts import fetch_filings
from scripts.fetch_filings import (
    FILINGS_ENDPOINT,
    fetch_filings_batch,
    parse_filing_row,
)
from scripts.fetch_fec import BASE_URL, FECClient


@pytest.fixture
def patched_raw_dir(tmp_path, monkeypatch):
    fake_raw = tmp_path / "raw"
    fake_raw.mkdir()
    monkeypatch.setattr(fetch_filings, "RAW_DIR", fake_raw)
    return fake_raw


@pytest.fixture
def fec_client(monkeypatch):
    monkeypatch.setenv("FEC_API_KEY", "test-key")
    from scripts import fetch_fec
    monkeypatch.setattr(fetch_fec, "MIN_REQUEST_INTERVAL_S", 0.0)
    return FECClient()


@responses.activate
def test_fetch_filings_batch_persists_raw(patched_raw_dir, fec_client):
    """Two file_numbers in, one page out, raw payload persisted."""
    responses.add(
        responses.GET,
        BASE_URL + FILINGS_ENDPOINT,
        json={
            "results": [
                {"file_number": 1917827, "pdf_url": "https://docquery.fec.gov/pdf/847/X/X.pdf",
                 "form_type": "F3X", "filed_date": "2024-10-15"},
                {"file_number": 1965626, "pdf_url": "https://docquery.fec.gov/pdf/193/Y/Y.pdf",
                 "form_type": "F3X", "filed_date": "2024-12-31"},
            ],
            "pagination": {"last_indexes": None},
        },
        status=200,
    )

    results, raw_paths = fetch_filings_batch(fec_client, ["1917827", "1965626"], batch_label="t1")
    assert len(results) == 2
    assert len(raw_paths) == 1
    envelope = json.loads(raw_paths[0].read_text())
    assert envelope["response"]["results"][0]["file_number"] == 1917827
    assert raw_paths[0].parent.name == "_filings"


@responses.activate
def test_fetch_filings_batch_paginates(patched_raw_dir, fec_client):
    """If FEC returns last_indexes, we walk the next page."""
    responses.add(
        responses.GET,
        BASE_URL + FILINGS_ENDPOINT,
        json={
            "results": [{"file_number": 1, "pdf_url": "u1"}],
            "pagination": {"last_indexes": {"last_index": "abc"}},
        },
        status=200,
    )
    responses.add(
        responses.GET,
        BASE_URL + FILINGS_ENDPOINT,
        json={"results": [{"file_number": 2, "pdf_url": "u2"}], "pagination": {"last_indexes": None}},
        status=200,
    )

    results, raw_paths = fetch_filings_batch(fec_client, ["1", "2"], batch_label="t2")
    assert len(results) == 2
    assert len(raw_paths) == 2


def test_fetch_filings_batch_empty_input(patched_raw_dir, fec_client):
    """Empty file_numbers list short-circuits — no FEC request, no raw payload."""
    results, raw_paths = fetch_filings_batch(fec_client, [], batch_label="empty")
    assert results == []
    assert raw_paths == []


def test_parse_filing_row_maps_columns():
    row = {
        "file_number": 1917827,
        "pdf_url": "https://docquery.fec.gov/pdf/847/X/X.pdf",
        "form_type": "F3X",
        "document_type": "REPORT",
        "document_type_full": "Report of Receipts and Disbursements",
        "filed_date": "2024-10-15",
        "receipt_date": "2024-10-16",
        "coverage_start_date": "2024-09-01",
        "coverage_end_date": "2024-09-30",
        "committee_id": "C00368142",
        "committee_name": "ACME PAC",
        "is_amended": True,
        "amendment_chain": [1917827, 1917828],
        "cycle": 2024,
    }
    out = parse_filing_row(row)
    assert out["file_number"] == "1917827"
    assert out["pdf_url"] == "https://docquery.fec.gov/pdf/847/X/X.pdf"
    assert out["is_amended"] == 1
    assert json.loads(out["amendment_chain"]) == [1917827, 1917828]
    assert out["cycle"] == 2024


def test_parse_filing_row_handles_missing_fields():
    out = parse_filing_row({"file_number": 1, "pdf_url": None})
    assert out["file_number"] == "1"
    assert out["pdf_url"] is None
    assert out["is_amended"] == 0
    assert json.loads(out["amendment_chain"]) == []


def test_parse_filing_row_falls_back_to_election_year_when_cycle_missing():
    out = parse_filing_row({"file_number": 1, "election_year": 2022})
    assert out["cycle"] == 2022

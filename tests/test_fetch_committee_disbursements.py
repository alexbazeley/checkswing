"""Tests for scripts/fetch_committee_disbursements.py — Schedule B by_recipient fetcher."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses

from scripts import fetch_committee_disbursements
from scripts.fetch_committee_disbursements import (
    BY_RECIPIENT_ENDPOINT,
    DEFAULT_TOP_N,
    fetch_by_recipient,
    parse_by_recipient_row,
)
from scripts.fetch_fec import BASE_URL, FECClient


@pytest.fixture
def patched_raw_dir(tmp_path, monkeypatch):
    """Re-root data/raw so test persists don't leak into the real archive."""
    fake_raw = tmp_path / "raw"
    fake_raw.mkdir()
    monkeypatch.setattr(fetch_committee_disbursements, "RAW_DIR", fake_raw)
    return fake_raw


@pytest.fixture
def fec_client(monkeypatch):
    monkeypatch.setenv("FEC_API_KEY", "test-key")
    from scripts import fetch_fec
    monkeypatch.setattr(fetch_fec, "MIN_REQUEST_INTERVAL_S", 0.0)
    return FECClient()


@responses.activate
def test_fetch_by_recipient_persists_raw_and_returns_rows(patched_raw_dir, fec_client):
    cmte_id = "C00012345"
    fake_response = {
        "results": [
            {"recipient_name": "ACME CANDIDATE",
             "candidate_id": "H8XX00001",
             "total": 25000.0, "count": 4,
             "recipient_party": "REP", "recipient_office": "H"},
            {"recipient_name": "ALLY SUPER PAC",
             "recipient_committee_id": "C00099999",
             "total": 10000.0, "count": 1,
             "recipient_party": "REP"},
        ],
        "pagination": {"last_indexes": None},
    }
    responses.add(
        responses.GET,
        BASE_URL + BY_RECIPIENT_ENDPOINT,
        json=fake_response,
        status=200,
    )

    rows, raw_paths = fetch_by_recipient(fec_client, cmte_id, 2024)

    assert len(rows) == 2
    # Raw envelope landed under _committee_disbursements/<id>/
    assert raw_paths[0].parent.name == cmte_id
    assert raw_paths[0].parent.parent.name == "_committee_disbursements"
    envelope = json.loads(raw_paths[0].read_text())
    assert envelope["_meta"]["committee_id"] == cmte_id
    assert envelope["_meta"]["cycle"] == 2024
    assert envelope["_meta"]["page"] == 1
    assert envelope["response"] == fake_response
    # The committee_id and cycle were passed to FEC
    sent_params = responses.calls[0].request.params
    assert sent_params["committee_id"] == cmte_id
    assert sent_params["cycle"] == "2024"


@responses.activate
def test_fetch_by_recipient_paginates_until_top_n(patched_raw_dir, fec_client):
    """Two pages of 2 rows each, top_n=3 — stops after enough rows collected."""
    cmte_id = "C00012345"
    responses.add(
        responses.GET,
        BASE_URL + BY_RECIPIENT_ENDPOINT,
        json={
            "results": [
                {"candidate_id": "C1", "recipient_name": "A", "total": 100.0},
                {"candidate_id": "C2", "recipient_name": "B", "total": 90.0},
            ],
            "pagination": {"last_indexes": {"last_index": "abc"}},
        },
        status=200,
    )
    responses.add(
        responses.GET,
        BASE_URL + BY_RECIPIENT_ENDPOINT,
        json={
            "results": [
                {"candidate_id": "C3", "recipient_name": "C", "total": 80.0},
                {"candidate_id": "C4", "recipient_name": "D", "total": 70.0},
            ],
            "pagination": {"last_indexes": None},
        },
        status=200,
    )

    rows, raw_paths = fetch_by_recipient(fec_client, cmte_id, 2024, top_n=3)
    assert len(rows) == 3
    # Both pages got fetched; top_n was applied as a post-fetch slice.
    assert len(raw_paths) == 2
    # Second page filename carries page=2
    page2_meta = json.loads(raw_paths[1].read_text())["_meta"]
    assert page2_meta["page"] == 2


@responses.activate
def test_fetch_by_recipient_stops_when_no_results(patched_raw_dir, fec_client):
    cmte_id = "C00012345"
    responses.add(
        responses.GET,
        BASE_URL + BY_RECIPIENT_ENDPOINT,
        json={"results": [], "pagination": {"last_indexes": None}},
        status=200,
    )

    rows, raw_paths = fetch_by_recipient(fec_client, cmte_id, 2024)
    assert rows == []
    # Raw payload still persisted (CLAUDE.md §1.4 — empty FEC response is
    # also ground truth for "we asked and they had nothing for that cycle").
    assert len(raw_paths) == 1


def test_parse_by_recipient_row_handles_candidate_recipient():
    out = parse_by_recipient_row(
        "C00012345",
        2024,
        {
            "candidate_id": "H8XX00001",
            "recipient_name": "JANE CANDIDATE",
            "recipient_party": "DEM",
            "recipient_office": "S",
            "total": 50000.0,
            "count": 3,
        },
    )
    assert out["committee_id"] == "C00012345"
    assert out["cycle"] == 2024
    assert out["recipient_id"] == "H8XX00001"
    assert out["recipient_kind"] == "candidate"
    assert out["recipient_name"] == "JANE CANDIDATE"
    assert out["recipient_party"] == "DEM"
    assert out["recipient_office"] == "S"
    assert out["total_amount"] == 50000.0
    assert out["n_transactions"] == 3


def test_parse_by_recipient_row_handles_committee_recipient():
    out = parse_by_recipient_row(
        "C00012345",
        2022,
        {
            "recipient_committee_id": "C00099999",
            "recipient_name": "ALLY SUPER PAC",
            "total": 10000.0,
        },
    )
    assert out["recipient_id"] == "C00099999"
    assert out["recipient_kind"] == "committee"
    assert out["recipient_name"] == "ALLY SUPER PAC"
    # Optional fields default cleanly
    assert out["recipient_party"] is None
    assert out["recipient_office"] is None


def test_parse_by_recipient_row_candidate_id_wins_over_committee_id():
    """When both IDs are present (rare but possible), the candidate scope wins
    so that the recipient can be correctly displayed as a candidate, not a PAC."""
    out = parse_by_recipient_row(
        "C00012345",
        2024,
        {
            "candidate_id": "H8XX00001",
            "recipient_committee_id": "C00099999",
            "recipient_name": "ACME",
            "total": 100.0,
        },
    )
    assert out["recipient_kind"] == "candidate"
    assert out["recipient_id"] == "H8XX00001"


def test_parse_by_recipient_row_returns_none_when_no_recipient_id_or_name():
    """Without any recipient identifier we'd have no stable PK to insert under
    — drop the row (CLAUDE.md §1.5)."""
    out = parse_by_recipient_row(
        "C00012345",
        2024,
        {"total": 100.0},
    )
    assert out is None


def test_parse_by_recipient_row_fallback_keys_on_name_when_no_id():
    """When FEC gives us a name but no ID (rare), we synthesize a stable
    NAME:<name> key so the row survives. Same name across re-fetches keeps
    the PK stable, preserving idempotency."""
    out = parse_by_recipient_row(
        "C00012345",
        2024,
        {"recipient_name": "MISC TRANSFER", "total": 500.0},
    )
    assert out is not None
    assert out["recipient_id"] == "NAME:MISC TRANSFER"
    assert out["recipient_kind"] == "committee"


def test_parse_by_recipient_row_tolerates_missing_total():
    """FEC sometimes omits 'total' on placeholder rows — fall back to 0.0
    rather than letting the row's NOT NULL constraint blow up."""
    out = parse_by_recipient_row(
        "C00012345",
        2024,
        {"candidate_id": "H8XX00001", "recipient_name": "X"},
    )
    assert out is not None
    assert out["total_amount"] == 0.0


def test_default_top_n_is_200():
    """Sanity-check the cap matches the design (top 200 covers virtually all
    meaningful spending; the long tail is small refunds/de-minimis)."""
    assert DEFAULT_TOP_N == 200

"""Tests for scripts/ingest_filings.py — orchestrator + idempotency."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses

from scripts import db, fetch_filings, ingest_filings
from scripts.fetch_filings import FILINGS_ENDPOINT
from scripts.fetch_fec import BASE_URL


@pytest.fixture
def tmp_master(tmp_path, monkeypatch):
    """Tmp master.db + tmp raw dir, seeded with two donations pointing at two filings."""
    db_path = tmp_path / "master.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    monkeypatch.setattr(db, "MASTER_DB", db_path)
    monkeypatch.setattr(fetch_filings, "RAW_DIR", raw_dir)
    monkeypatch.setattr(ingest_filings, "MASTER_DB", db_path)
    monkeypatch.setattr(ingest_filings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ingest_filings, "FILINGS_LOCK", tmp_path / ".filings_ingest.lock")
    from scripts import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "SNAPSHOTS_DIR", snap_dir)
    monkeypatch.setattr(paths, "RAW_DIR", raw_dir)
    monkeypatch.setattr(paths, "MASTER_DB", db_path)

    def _fake_relpath(p):
        try:
            return Path(p).resolve().relative_to(tmp_path).as_posix()
        except ValueError:
            return str(p)
    monkeypatch.setattr(fetch_filings, "_persist_filings_raw", fetch_filings._persist_filings_raw)
    monkeypatch.setattr(ingest_filings, "relpath", _fake_relpath)

    db.init(db_path)
    with db.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO donations
              (transaction_id, entity_slug, entity_kind, status,
               signals_matched, contributor_name_raw, recipient_committee_id,
               recipient_committee_name, amount, date, filing_id,
               raw_payload_path, ingested_at)
            VALUES
              ('txn1', 'owner-a', 'owner', 'CONFIRMED', '[]',
               'A', 'C1', 'Cmte', 100, '2024-01-01', '1917827',
               'x', '2024-01-01T00:00:00Z'),
              ('txn2', 'owner-b', 'owner', 'PROBABLE', '[]',
               'B', 'C2', 'Cmte', 200, '2024-02-01', '1965626',
               'x', '2024-02-01T00:00:00Z')
            """
        )
    return db_path


@pytest.fixture
def fec_responses(monkeypatch):
    monkeypatch.setenv("FEC_API_KEY", "test-key")
    from scripts import fetch_fec
    monkeypatch.setattr(fetch_fec, "MIN_REQUEST_INTERVAL_S", 0.0)
    with responses.RequestsMock() as rsps:
        yield rsps


def _mock_filing_batch(rsps, filings: list[dict]):
    rsps.add(
        responses.GET,
        BASE_URL + FILINGS_ENDPOINT,
        json={"results": filings, "pagination": {"last_indexes": None}},
        status=200,
    )


class TestListFilingsFromDonations:
    def test_returns_distinct_non_empty_filing_ids(self, tmp_master):
        ids = ingest_filings.list_filings_from_donations(tmp_master)
        assert sorted(ids) == ["1917827", "1965626"]

    def test_excludes_empty_or_null_filing_ids(self, tmp_master):
        with db.connect(tmp_master) as conn:
            conn.execute(
                """
                INSERT INTO donations
                  (transaction_id, entity_slug, entity_kind, status,
                   signals_matched, contributor_name_raw, recipient_committee_id,
                   recipient_committee_name, amount, date, filing_id,
                   raw_payload_path, ingested_at)
                VALUES
                  ('txn3', 'owner-a', 'owner', 'CONFIRMED', '[]', 'A', 'C1', 'Cmte',
                   50, '2024-03-01', '',
                   'x', '2024-03-01T00:00:00Z')
                """
            )
        ids = ingest_filings.list_filings_from_donations(tmp_master)
        assert "" not in ids
        assert sorted(ids) == ["1917827", "1965626"]


class TestIngestFilings:
    def test_fetches_and_upserts_each_filing(self, tmp_master, fec_responses):
        _mock_filing_batch(fec_responses, [
            {"file_number": 1917827, "pdf_url": "url1", "form_type": "F3X",
             "filed_date": "2024-10-15", "committee_id": "C00368142",
             "committee_name": "ACME"},
            {"file_number": 1965626, "pdf_url": "url2", "form_type": "F3X",
             "filed_date": "2024-12-31", "committee_id": "C00912865",
             "committee_name": "BETA"},
        ])
        summary = ingest_filings.ingest_filings(db_path=tmp_master)
        assert summary["stale_to_fetch"] == 2
        assert summary["fetched"] == 2
        assert summary["upserted"] == 2

        with db.connect(tmp_master) as conn:
            rows = list(conn.execute("SELECT file_number, pdf_url FROM filings ORDER BY file_number"))
            assert [(r["file_number"], r["pdf_url"]) for r in rows] == [
                ("1917827", "url1"),
                ("1965626", "url2"),
            ]

    def test_idempotent_within_freshness(self, tmp_master, fec_responses):
        _mock_filing_batch(fec_responses, [
            {"file_number": 1917827, "pdf_url": "u1"},
            {"file_number": 1965626, "pdf_url": "u2"},
        ])
        first = ingest_filings.ingest_filings(db_path=tmp_master)
        assert first["upserted"] == 2

        fec_responses.reset()
        # Second run should not hit FEC at all
        second = ingest_filings.ingest_filings(db_path=tmp_master)
        assert second["stale_to_fetch"] == 0
        assert second["fetched"] == 0

    def test_force_refresh_overrides_freshness(self, tmp_master, fec_responses):
        _mock_filing_batch(fec_responses, [
            {"file_number": 1917827, "pdf_url": "u1"},
            {"file_number": 1965626, "pdf_url": "u2"},
        ])
        ingest_filings.ingest_filings(db_path=tmp_master)

        fec_responses.reset()
        _mock_filing_batch(fec_responses, [
            {"file_number": 1917827, "pdf_url": "U1_NEW"},
            {"file_number": 1965626, "pdf_url": "U2_NEW"},
        ])
        result = ingest_filings.ingest_filings(db_path=tmp_master, force_refresh=True)
        assert result["upserted"] == 2

        with db.connect(tmp_master) as conn:
            urls = sorted(r[0] for r in conn.execute("SELECT pdf_url FROM filings"))
            assert urls == ["U1_NEW", "U2_NEW"]

    def test_only_subset_restricts_targets(self, tmp_master, fec_responses):
        _mock_filing_batch(fec_responses, [
            {"file_number": 1965626, "pdf_url": "u2"},
        ])
        result = ingest_filings.ingest_filings(db_path=tmp_master, only=["1965626"])
        assert result["candidates"] == 1
        assert result["upserted"] == 1

    def test_missing_from_fec_recorded_in_summary(self, tmp_master, fec_responses):
        # Two requested, only one comes back from FEC
        _mock_filing_batch(fec_responses, [
            {"file_number": 1917827, "pdf_url": "u1"},
        ])
        result = ingest_filings.ingest_filings(db_path=tmp_master)
        assert result["upserted"] == 1
        assert result["missing_from_fec"] == 1

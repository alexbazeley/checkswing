"""Tests for scripts/ingest_committees.py — orchestrator + idempotency."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import responses

from scripts import db, fetch_committees, ingest_committees
from scripts.fetch_committees import (
    COMMITTEE_DETAIL_ENDPOINT,
    COMMITTEE_TOTALS_ENDPOINT,
)
from scripts.fetch_fec import BASE_URL, FECClient


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_master(tmp_path, monkeypatch):
    """Tmp master.db + tmp raw dir; init schema with a couple of donation rows
    referencing two committees so list_committees_from_donations has something."""
    db_path = tmp_path / "master.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()
    data_dir = tmp_path

    # Patch all paths used by the modules under test
    monkeypatch.setattr(db, "MASTER_DB", db_path)
    monkeypatch.setattr(fetch_committees, "RAW_DIR", raw_dir)
    monkeypatch.setattr(ingest_committees, "MASTER_DB", db_path)
    monkeypatch.setattr(ingest_committees, "DATA_DIR", data_dir)
    monkeypatch.setattr(ingest_committees, "COMMITTEES_LOCK", data_dir / ".committees_ingest.lock")
    # ensure_data_dirs uses paths.DATA_DIR — patch that too
    from scripts import paths
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "SNAPSHOTS_DIR", snapshots_dir)
    monkeypatch.setattr(paths, "RAW_DIR", raw_dir)
    monkeypatch.setattr(paths, "MASTER_DB", db_path)
    # relpath needs to handle tmp paths gracefully
    def _fake_relpath(p):
        try:
            return Path(p).resolve().relative_to(tmp_path).as_posix()
        except ValueError:
            return str(p)
    monkeypatch.setattr(fetch_committees, "relpath", _fake_relpath)
    monkeypatch.setattr(ingest_committees, "relpath", _fake_relpath)

    db.init(db_path)
    # Insert two CONFIRMED donation rows pointing at two committees
    with db.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO donations
              (transaction_id, entity_slug, entity_kind, status, status_reason,
               signals_matched, contributor_name_raw, recipient_committee_id,
               recipient_committee_name, amount, date, filing_id,
               raw_payload_path, ingested_at)
            VALUES
              ('txn1', 'owner-a', 'owner', 'CONFIRMED', 'r1',
               '[]', 'Owner A', 'C00000001', 'Cmte One', 100, '2024-01-01', '1000',
               'data/raw/owner-a/x.json', '2024-01-01T00:00:00Z'),
              ('txn2', 'owner-b', 'owner', 'PROBABLE', 'r2',
               '[]', 'Owner B', 'C00000002', 'Cmte Two', 200, '2024-02-01', '1001',
               'data/raw/owner-b/x.json', '2024-02-01T00:00:00Z')
            """
        )
    return db_path


@pytest.fixture
def fec_responses(monkeypatch):
    """Bring up the responses library for a mocked-FEC test, with FEC_API_KEY set.

    Also short-circuits the FEC throttle so tests don't sleep 4s between mocked
    requests. The throttle is correctness-preserving in prod; in tests we just
    want speed.
    """
    monkeypatch.setenv("FEC_API_KEY", "test-key")
    from scripts import fetch_fec
    monkeypatch.setattr(fetch_fec, "MIN_REQUEST_INTERVAL_S", 0.0)
    with responses.RequestsMock() as rsps:
        yield rsps


def _mock_committee(rsps, cmte_id, name="ACME PAC", designation_full="Super PAC"):
    """Stub the two FEC endpoints for one committee."""
    rsps.add(
        responses.GET,
        BASE_URL + COMMITTEE_DETAIL_ENDPOINT.format(committee_id=cmte_id),
        json={
            "results": [
                {
                    "committee_id": cmte_id,
                    "name": name,
                    "designation": "O",
                    "designation_full": designation_full,
                    "committee_type": "O",
                    "committee_type_full": "Independent Expenditure-Only",
                    "party": "OTH",
                    "cycles": [2022, 2024],
                    "first_file_date": "2020-01-01",
                    "last_file_date": "2024-10-01",
                }
            ]
        },
        status=200,
    )
    rsps.add(
        responses.GET,
        BASE_URL + COMMITTEE_TOTALS_ENDPOINT.format(committee_id=cmte_id),
        json={
            "results": [
                {"cycle": 2024, "receipts": 1000.0, "disbursements": 800.0,
                 "cash_on_hand_end_period": 200.0,
                 "coverage_start_date": "2023-01-01", "coverage_end_date": "2024-12-31"},
                {"cycle": 2022, "receipts": 500.0, "disbursements": 480.0,
                 "cash_on_hand_end_period": 20.0,
                 "coverage_start_date": "2021-01-01", "coverage_end_date": "2022-12-31"},
            ]
        },
        status=200,
    )


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestListCommitteesFromDonations:
    def test_returns_distinct_recipient_committee_ids(self, tmp_master):
        ids = ingest_committees.list_committees_from_donations(tmp_master)
        assert sorted(ids) == ["C00000001", "C00000002"]

    def test_ignores_uncertain_status(self, tmp_master):
        # Add an UNCERTAIN donation pointing at a new committee; should NOT
        # appear in the list (uncertain rows shouldn't drive enrichment).
        with db.connect(tmp_master) as conn:
            conn.execute(
                """
                INSERT INTO donations
                  (transaction_id, entity_slug, entity_kind, status,
                   signals_matched, contributor_name_raw, recipient_committee_id,
                   recipient_committee_name, amount, date, filing_id,
                   raw_payload_path, ingested_at)
                VALUES
                  ('txn3', 'owner-a', 'owner', 'UNCERTAIN', '[]',
                   'Owner A', 'C00000003', 'Cmte Three', 50, '2024-03-01', '1002',
                   'x', '2024-03-01T00:00:00Z')
                """
            )
        ids = ingest_committees.list_committees_from_donations(tmp_master)
        assert "C00000003" not in ids


class TestIngestCommittee:
    def test_writes_committees_and_totals_rows(self, tmp_master, fec_responses):
        _mock_committee(fec_responses, "C00000001")
        result = ingest_committees.ingest_committee("C00000001", db_path=tmp_master)
        assert result["status"] == "fetched"
        assert result["totals_rows"] == 2

        with db.connect(tmp_master) as conn:
            c = conn.execute(
                "SELECT name, designation_label, committee_type_label FROM committees WHERE committee_id = ?",
                ("C00000001",),
            ).fetchone()
            assert c["name"] == "ACME PAC"
            assert c["designation_label"] == "Super PAC"
            t = list(
                conn.execute(
                    "SELECT cycle, receipts FROM committee_totals WHERE committee_id = ? ORDER BY cycle",
                    ("C00000001",),
                )
            )
            assert [(r["cycle"], r["receipts"]) for r in t] == [(2022, 500.0), (2024, 1000.0)]

    def test_idempotent_within_freshness_window(self, tmp_master, fec_responses):
        _mock_committee(fec_responses, "C00000001")
        first = ingest_committees.ingest_committee("C00000001", db_path=tmp_master)
        assert first["status"] == "fetched"

        # Second run should not hit FEC at all — no more mocks registered for this id
        # would error if it did. Confirm by clearing the response queue.
        fec_responses.reset()
        second = ingest_committees.ingest_committee("C00000001", db_path=tmp_master)
        assert second["status"] == "skipped_fresh"

    def test_force_refresh_overrides_freshness(self, tmp_master, fec_responses):
        _mock_committee(fec_responses, "C00000001")
        ingest_committees.ingest_committee("C00000001", db_path=tmp_master)

        fec_responses.reset()
        _mock_committee(fec_responses, "C00000001", name="ACME PAC RENAMED")
        result = ingest_committees.ingest_committee(
            "C00000001", db_path=tmp_master, force_refresh=True
        )
        assert result["status"] == "fetched"
        with db.connect(tmp_master) as conn:
            name = conn.execute(
                "SELECT name FROM committees WHERE committee_id = ?", ("C00000001",)
            ).fetchone()["name"]
            assert name == "ACME PAC RENAMED"

    def test_duplicate_cycle_rows_dont_blow_up(self, tmp_master, fec_responses):
        """FEC's /totals/ endpoint sometimes returns multiple rows for the same
        cycle on candidate committees (one per election round). Our writer must
        accept that without violating the (committee_id, cycle) PK."""
        fec_responses.add(
            responses.GET,
            BASE_URL + COMMITTEE_DETAIL_ENDPOINT.format(committee_id="C00000001"),
            json={"results": [{"committee_id": "C00000001", "name": "DUP CYCLE CMTE",
                               "designation": "P", "designation_full": "Principal campaign committee",
                               "committee_type": "H", "committee_type_full": "House",
                               "party": "DEM", "cycles": [2020]}]},
            status=200,
        )
        fec_responses.add(
            responses.GET,
            BASE_URL + COMMITTEE_TOTALS_ENDPOINT.format(committee_id="C00000001"),
            json={
                "results": [
                    {"cycle": 2020, "receipts": 100.0, "disbursements": 90.0,
                     "coverage_end_date": "2020-11-23"},
                    {"cycle": 2020, "receipts": 50.0, "disbursements": 45.0,
                     "coverage_end_date": "2020-07-15"},  # primary
                ]
            },
            status=200,
        )

        result = ingest_committees.ingest_committee("C00000001", db_path=tmp_master)
        assert result["status"] == "fetched"

        with db.connect(tmp_master) as conn:
            rows = list(conn.execute(
                "SELECT receipts FROM committee_totals WHERE committee_id = ? AND cycle = ?",
                ("C00000001", 2020),
            ))
            assert len(rows) == 1  # PK enforced; last write survives

    def test_preserves_external_link_across_refetch(self, tmp_master, fec_responses):
        _mock_committee(fec_responses, "C00000001")
        ingest_committees.ingest_committee("C00000001", db_path=tmp_master)
        # Simulate the external-link applier setting a curated link
        with db.connect(tmp_master) as conn:
            conn.execute(
                """UPDATE committees
                      SET external_link = ?, external_link_label = ?, external_link_source = ?
                    WHERE committee_id = ?""",
                ("https://example.org/wiki/X", "Wikipedia", "manual", "C00000001"),
            )

        # Force-refresh — external link must survive
        fec_responses.reset()
        _mock_committee(fec_responses, "C00000001")
        ingest_committees.ingest_committee(
            "C00000001", db_path=tmp_master, force_refresh=True
        )
        with db.connect(tmp_master) as conn:
            row = conn.execute(
                "SELECT external_link, external_link_label FROM committees WHERE committee_id = ?",
                ("C00000001",),
            ).fetchone()
            assert row["external_link"] == "https://example.org/wiki/X"
            assert row["external_link_label"] == "Wikipedia"


class TestIngestAllCommittees:
    def test_processes_every_donation_recipient(self, tmp_master, fec_responses):
        _mock_committee(fec_responses, "C00000001", name="Cmte One")
        _mock_committee(fec_responses, "C00000002", name="Cmte Two")

        summary = ingest_committees.ingest_all_committees(db_path=tmp_master)
        assert summary["attempted"] == 2
        assert summary["fetched"] == 2
        assert summary["failed"] == 0

        with db.connect(tmp_master) as conn:
            n = conn.execute("SELECT COUNT(*) FROM committees").fetchone()[0]
            assert n == 2

    def test_isolates_per_committee_failure(self, tmp_master, fec_responses):
        # One mocked, one will 404
        _mock_committee(fec_responses, "C00000001")
        fec_responses.add(
            responses.GET,
            BASE_URL + COMMITTEE_DETAIL_ENDPOINT.format(committee_id="C00000002"),
            json={"results": []},
            status=200,
        )
        summary = ingest_committees.ingest_all_committees(db_path=tmp_master)
        assert summary["fetched"] == 1
        assert summary["failed"] == 1
        assert summary["failed_ids"] == ["C00000002"]

    def test_max_count_caps_attempts(self, tmp_master, fec_responses):
        _mock_committee(fec_responses, "C00000001")
        # Don't mock C00000002 — max_count=1 should prevent us from getting there.
        summary = ingest_committees.ingest_all_committees(db_path=tmp_master, max_count=1)
        assert summary["attempted"] == 1
        assert summary["fetched"] == 1

    def test_only_subset_restricts_targets(self, tmp_master, fec_responses):
        _mock_committee(fec_responses, "C00000002")
        summary = ingest_committees.ingest_all_committees(
            db_path=tmp_master, only=["C00000002"]
        )
        assert summary["attempted"] == 1
        with db.connect(tmp_master) as conn:
            ids = [r[0] for r in conn.execute("SELECT committee_id FROM committees ORDER BY committee_id")]
            assert ids == ["C00000002"]

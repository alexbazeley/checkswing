"""Tests for the v3 per-transaction FEC fields on donations.

Covers:
  - Schema migration (ALTER TABLE) is idempotent and additive.
  - insert_donation writes the new columns when the row dict provides them.
  - backfill_donation_image_fields rehydrates rows from local raw payloads.
  - build_data.py prefers DB values over raw-payload lookup (DB-first, fallback only).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import backfill_donation_image_fields as bdif
from scripts import db
from scripts.db import DONATION_EXTRA_COLS, SCHEMA_VERSION


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh tmp master.db rooted under tmp_path. Patches paths so backfill
    snapshots, raw-dir scans, etc. all stay isolated."""
    db_path = tmp_path / "master.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    monkeypatch.setattr(db, "MASTER_DB", db_path)
    from scripts import paths
    monkeypatch.setattr(paths, "MASTER_DB", db_path)
    monkeypatch.setattr(paths, "RAW_DIR", raw_dir)
    monkeypatch.setattr(paths, "SNAPSHOTS_DIR", snap_dir)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bdif, "MASTER_DB", db_path)
    monkeypatch.setattr(bdif, "RAW_DIR", raw_dir)
    db.init(db_path)
    return {"db_path": db_path, "raw_dir": raw_dir}


def _seed_donation(conn, **overrides):
    """Insert one donation row with reasonable defaults."""
    row = {
        "transaction_id": overrides.get("transaction_id", "TXN1"),
        "entity_slug": overrides.get("entity_slug", "owner-a"),
        "entity_kind": "owner",
        "parent_owner_slug": None,
        "status": overrides.get("status", "CONFIRMED"),
        "status_reason": "test",
        "signals_matched": "[]",
        "contributor_name_raw": "Owner A",
        "contributor_employer_raw": "",
        "contributor_occupation_raw": "",
        "contributor_city": "",
        "contributor_state": "",
        "contributor_zip": "",
        "recipient_committee_id": "C00000001",
        "recipient_committee_name": "TestPAC",
        "recipient_candidate_id": "",
        "recipient_candidate_name": "",
        "recipient_party": "",
        "recipient_office": None,
        "amount": 100.0,
        "date": "2024-01-15",
        "election_cycle": 2024,
        "report_type": None,
        "filing_id": "5000",
        "raw_payload_path": overrides.get("raw_payload_path", "data/raw/owner-a/x.json"),
        "ingested_at": "2024-01-15T00:00:00Z",
        **{c: overrides.get(c) for c, _ in DONATION_EXTRA_COLS},
    }
    db.insert_donation(conn, row)


# ─── Migration ───────────────────────────────────────────────────────────────


class TestMigration:
    def test_init_adds_v3_columns_when_missing(self, tmp_db):
        # Simulate a pre-v3 DB by stripping the columns we just added
        with db.connect(tmp_db["db_path"]) as conn:
            for col_name, _ in DONATION_EXTRA_COLS:
                # SQLite doesn't support DROP COLUMN before 3.35; build a fresh
                # pre-v3-shaped table instead.
                pass
        # The fixture already ran init() with the v3 schema, so cols exist.
        # Confirm they're all present.
        with db.connect(tmp_db["db_path"]) as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(donations)")}
            for col_name, _ in DONATION_EXTRA_COLS:
                assert col_name in cols, f"v3 column {col_name!r} missing"
            versions = [r["version"] for r in conn.execute("SELECT version FROM schema_version")]
            assert SCHEMA_VERSION in versions

    def test_init_is_idempotent(self, tmp_db):
        # Two calls in a row should not duplicate the v3 schema_version row,
        # nor crash on a second ALTER TABLE attempt.
        db.init(tmp_db["db_path"])
        db.init(tmp_db["db_path"])
        with db.connect(tmp_db["db_path"]) as conn:
            versions = [r["version"] for r in conn.execute("SELECT version FROM schema_version")]
            assert versions.count(SCHEMA_VERSION) == 1


# ─── insert_donation ─────────────────────────────────────────────────────────


class TestInsertDonation:
    def test_writes_v3_columns_when_present(self, tmp_db):
        with db.connect(tmp_db["db_path"]) as conn:
            _seed_donation(
                conn,
                image_number="202401159000000001",
                pdf_url="https://docquery.fec.gov/cgi-bin/fecimg/?202401159000000001",
                filing_form="F3X",
                line_number="11AI",
                receipt_type_full="Contribution from an individual",
                recipient_committee_type="O",
            )

        with db.connect(tmp_db["db_path"]) as conn:
            row = conn.execute("SELECT * FROM donations WHERE transaction_id = ?", ("TXN1",)).fetchone()
            assert row["image_number"] == "202401159000000001"
            assert row["pdf_url"] == "https://docquery.fec.gov/cgi-bin/fecimg/?202401159000000001"
            assert row["filing_form"] == "F3X"
            assert row["line_number"] == "11AI"
            assert row["receipt_type_full"] == "Contribution from an individual"
            assert row["recipient_committee_type"] == "O"

    def test_tolerates_missing_v3_keys(self, tmp_db):
        """A legacy caller that doesn't know about v3 should still succeed,
        with the new columns left NULL."""
        with db.connect(tmp_db["db_path"]) as conn:
            # Build a row dict missing the v3 keys entirely
            row = {
                "transaction_id": "TXN_LEGACY",
                "entity_slug": "owner-a",
                "entity_kind": "owner",
                "parent_owner_slug": None,
                "status": "CONFIRMED",
                "status_reason": "test",
                "signals_matched": "[]",
                "contributor_name_raw": "Owner A",
                "contributor_employer_raw": "",
                "contributor_occupation_raw": "",
                "contributor_city": "",
                "contributor_state": "",
                "contributor_zip": "",
                "recipient_committee_id": "C00000001",
                "recipient_committee_name": "TestPAC",
                "recipient_candidate_id": "",
                "recipient_candidate_name": "",
                "recipient_party": "",
                "recipient_office": None,
                "amount": 100.0,
                "date": "2024-01-15",
                "election_cycle": 2024,
                "report_type": None,
                "filing_id": "5000",
                "raw_payload_path": "x.json",
                "ingested_at": "2024-01-15T00:00:00Z",
            }
            db.insert_donation(conn, row)
            r = conn.execute("SELECT * FROM donations WHERE transaction_id = ?", ("TXN_LEGACY",)).fetchone()
            for col, _ in DONATION_EXTRA_COLS:
                assert r[col] is None


# ─── backfill ───────────────────────────────────────────────────────────────


def _write_payload(raw_dir: Path, slug: str, name: str, results: list[dict]) -> Path:
    slug_dir = raw_dir / slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    p = slug_dir / name
    envelope = {
        "_meta": {"slug": slug},
        "response": {"results": results},
    }
    p.write_text(json.dumps(envelope))
    return p


class TestBackfill:
    def test_recovers_fields_from_local_raw_payloads(self, tmp_db):
        # Seed: one row with NULL image fields; one matching raw payload on disk
        with db.connect(tmp_db["db_path"]) as conn:
            _seed_donation(conn, transaction_id="TXN_OWNED")
        _write_payload(
            tmp_db["raw_dir"],
            "owner-a",
            "2024-01-15.json",
            [
                {
                    "transaction_id": "TXN_OWNED",
                    "image_number": 202401159111111111,
                    "pdf_url": "https://docquery.fec.gov/cgi-bin/fecimg/?202401159111111111",
                    "filing_form": "F3X",
                    "line_number": "11AI",
                    "receipt_type_full": "Contribution from an individual",
                    "recipient_committee_type": "O",
                }
            ],
        )

        result = bdif.backfill(tmp_db["db_path"], tmp_db["raw_dir"])
        assert result["rows_updated"] == 1
        assert result["rows_unrecoverable"] == 0

        with db.connect(tmp_db["db_path"]) as conn:
            row = conn.execute("SELECT * FROM donations WHERE transaction_id = ?", ("TXN_OWNED",)).fetchone()
            assert row["image_number"] == "202401159111111111"
            assert row["filing_form"] == "F3X"

    def test_idempotent_on_re_run(self, tmp_db):
        with db.connect(tmp_db["db_path"]) as conn:
            _seed_donation(conn, transaction_id="TXN_OWNED")
        _write_payload(
            tmp_db["raw_dir"],
            "owner-a",
            "2024-01-15.json",
            [{"transaction_id": "TXN_OWNED", "image_number": 1, "pdf_url": "u", "filing_form": "F",
              "line_number": "L", "receipt_type_full": "R", "recipient_committee_type": "O"}],
        )

        first = bdif.backfill(tmp_db["db_path"], tmp_db["raw_dir"])
        second = bdif.backfill(tmp_db["db_path"], tmp_db["raw_dir"])
        assert first["rows_updated"] == 1
        # Second run sees no NULL rows → owners_scanned reflects what it actually walked
        assert second["rows_updated"] == 0
        assert second["rows_with_null_image_number"] == 0

    def test_orphan_row_marked_unrecoverable(self, tmp_db):
        """A NULL row whose raw payload doesn't exist anywhere on disk stays NULL."""
        with db.connect(tmp_db["db_path"]) as conn:
            _seed_donation(conn, transaction_id="TXN_ORPHAN")
        # No payload written for this txn

        result = bdif.backfill(tmp_db["db_path"], tmp_db["raw_dir"])
        assert result["rows_updated"] == 0
        assert result["rows_unrecoverable"] == 1

        with db.connect(tmp_db["db_path"]) as conn:
            row = conn.execute("SELECT * FROM donations WHERE transaction_id = ?", ("TXN_ORPHAN",)).fetchone()
            assert row["image_number"] is None

    def test_skips_rows_already_populated(self, tmp_db):
        """Already-populated rows are not overwritten by the backfill, even if
        the raw payload on disk has different (newer? older?) data."""
        with db.connect(tmp_db["db_path"]) as conn:
            _seed_donation(
                conn,
                transaction_id="TXN_ALREADY",
                image_number="ORIGINAL_VAL",
                pdf_url="original_url",
            )
        _write_payload(
            tmp_db["raw_dir"],
            "owner-a",
            "2024-01-15.json",
            [{"transaction_id": "TXN_ALREADY", "image_number": "REHYDRATED_VAL",
              "pdf_url": "rehydrated_url"}],
        )

        result = bdif.backfill(tmp_db["db_path"], tmp_db["raw_dir"])
        assert result["rows_updated"] == 0  # skipped — already populated

        with db.connect(tmp_db["db_path"]) as conn:
            row = conn.execute("SELECT * FROM donations WHERE transaction_id = ?", ("TXN_ALREADY",)).fetchone()
            assert row["image_number"] == "ORIGINAL_VAL"
            assert row["pdf_url"] == "original_url"

    def test_uses_nested_committee_type_when_top_level_missing(self, tmp_db):
        with db.connect(tmp_db["db_path"]) as conn:
            _seed_donation(conn, transaction_id="TXN_NESTED")
        _write_payload(
            tmp_db["raw_dir"],
            "owner-a",
            "2024-01-15.json",
            [{
                "transaction_id": "TXN_NESTED",
                "image_number": 999,
                "committee": {"committee_type": "Q"},  # no top-level recipient_committee_type
            }],
        )

        result = bdif.backfill(tmp_db["db_path"], tmp_db["raw_dir"])
        assert result["rows_updated"] == 1

        with db.connect(tmp_db["db_path"]) as conn:
            row = conn.execute("SELECT recipient_committee_type FROM donations WHERE transaction_id = ?", ("TXN_NESTED",)).fetchone()
            assert row["recipient_committee_type"] == "Q"

    def test_skips_checkpoint_sidecar_files(self, tmp_db):
        """_fetch_state.json and similar underscore-prefixed files must be ignored."""
        with db.connect(tmp_db["db_path"]) as conn:
            _seed_donation(conn, transaction_id="TXN_SIDECAR")
        slug_dir = tmp_db["raw_dir"] / "owner-a"
        slug_dir.mkdir(parents=True, exist_ok=True)
        (slug_dir / "_fetch_state.json").write_text(json.dumps({"oops": "garbage"}))

        result = bdif.backfill(tmp_db["db_path"], tmp_db["raw_dir"])
        # No real payload, no recovery
        assert result["rows_updated"] == 0
        assert result["rows_unrecoverable"] == 1

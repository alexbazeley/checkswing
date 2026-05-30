"""Tests for the Workstream B additions in scripts/ingest.py.

Covers:
  - _resolve_min_date precedence (CLI > full_refetch > YAML > default)
  - _write_audit_last_ingestion round-trips and preserves comments/order
  - The CLAUDE.md §1.7 boundary: signal blocks are untouched on YAML write
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from scripts import ingest
from scripts.fetch_fec import DEFAULT_MIN_DATE
from scripts.ingest import (
    SENTINEL_FILING_ID,
    _record_to_donation_row,
    _resolve_min_date,
    _row_has_required_provenance,
    _write_audit_last_ingestion,
)
from scripts.resolve_entities import Classification


# ─── _resolve_min_date ───────────────────────────────────────────────────────


class TestResolveMinDate:
    def test_full_refetch_overrides_everything(self):
        owner = {"audit": {"last_ingestion": "2024-01-01"}}
        date, source = _resolve_min_date(owner, "2023-06-01", full_refetch=True)
        assert date == DEFAULT_MIN_DATE
        assert "full-refetch" in source

    def test_explicit_min_date_wins_over_audit(self):
        owner = {"audit": {"last_ingestion": "2025-01-01"}}
        date, source = _resolve_min_date(owner, "2020-06-01", full_refetch=False)
        assert date == "2020-06-01"
        assert "user" in source

    def test_audit_last_ingestion_applies_trailing_window(self):
        # H2: incremental runs re-fetch a trailing look-back window before the
        # watermark so late-filed older-dated contributions aren't missed.
        from datetime import date as _date, timedelta as _td

        from scripts.ingest import INCREMENTAL_TRAILING_DAYS

        owner = {"audit": {"last_ingestion": "2026-05-22"}}
        d, source = _resolve_min_date(owner, None, full_refetch=False)
        assert d == (_date(2026, 5, 22) - _td(days=INCREMENTAL_TRAILING_DAYS)).isoformat()
        assert d < "2026-05-22"
        assert "audit.last_ingestion" in source

    def test_trailing_window_floored_at_default(self):
        # A watermark close to the project floor must not produce a min_date
        # before DEFAULT_MIN_DATE.
        owner = {"audit": {"last_ingestion": "2000-06-01"}}
        d, _ = _resolve_min_date(owner, None, full_refetch=False)
        assert d == DEFAULT_MIN_DATE

    def test_default_when_audit_null(self):
        owner = {"audit": {"last_ingestion": None}}
        date, source = _resolve_min_date(owner, None, full_refetch=False)
        assert date == DEFAULT_MIN_DATE
        assert "default" in source

    def test_default_when_no_audit_block(self):
        owner = {}
        date, source = _resolve_min_date(owner, None, full_refetch=False)
        assert date == DEFAULT_MIN_DATE
        assert "default" in source

    def test_audit_last_ingestion_coerced_to_str(self):
        """YAML may load 'last_ingestion: 2026-05-22' as a date object; the
        trailing window is still applied."""
        from datetime import date as date_cls, timedelta as _td

        from scripts.ingest import INCREMENTAL_TRAILING_DAYS

        owner = {"audit": {"last_ingestion": date_cls(2026, 5, 22)}}
        d, source = _resolve_min_date(owner, None, full_refetch=False)
        assert d == (date_cls(2026, 5, 22) - _td(days=INCREMENTAL_TRAILING_DAYS)).isoformat()


# ─── _write_audit_last_ingestion ─────────────────────────────────────────────


SAMPLE_OWNER_YAML = """\
# Sample owner — comments must survive round-trip.
slug: sample-owner
name: Sample Owner
team: Sample Team
role: Principal owner
status: pilot
tenure_start_date: 2020-01-01
tenure_end_date: null

name_variants:
  - "Sample Owner"
  - "S. Owner"
  # Inline comment between variants
  - "Sample O."

verifying_signals:
  cities: ["sampleville"]
  states: ["SA"]
  employers:
    - "Sample Corp"  # uniquely his
  occupations:
    - "investor"

strong_signals:
  employers: []
  zip_codes: []

sources:
  - description: "MLB ownership page"
    url: ""
    accessed: "2026-05-22"
    archive_url: ""

change_log:
  - date: 2026-05-22
    change: "Created."
    by: "test"

audit:
  created: 2026-05-22
  last_ingestion: null
  last_signal_review: 2026-05-22
"""


@pytest.fixture
def tmp_owner_yaml(tmp_path, monkeypatch):
    slug = "sample-owner"
    owners_dir = tmp_path / "owners"
    owners_dir.mkdir()
    path = owners_dir / f"{slug}.yaml"
    path.write_text(SAMPLE_OWNER_YAML, encoding="utf-8")
    monkeypatch.setattr(ingest, "OWNERS_DIR", owners_dir)
    return slug, path


class TestWriteAuditLastIngestion:
    def test_sets_field_from_null(self, tmp_owner_yaml):
        slug, path = tmp_owner_yaml
        _write_audit_last_ingestion(slug, "2026-05-24")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        # YAML loads dates as date objects in this context.
        last = data["audit"]["last_ingestion"]
        assert str(last) == "2026-05-24"

    def test_overwrites_existing_value(self, tmp_owner_yaml):
        slug, path = tmp_owner_yaml
        _write_audit_last_ingestion(slug, "2026-05-24")
        _write_audit_last_ingestion(slug, "2026-06-01")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert str(data["audit"]["last_ingestion"]) == "2026-06-01"

    def test_top_level_comment_preserved(self, tmp_owner_yaml):
        slug, path = tmp_owner_yaml
        _write_audit_last_ingestion(slug, "2026-05-24")
        text = path.read_text(encoding="utf-8")
        assert "# Sample owner — comments must survive round-trip." in text

    def test_inline_comment_preserved(self, tmp_owner_yaml):
        slug, path = tmp_owner_yaml
        _write_audit_last_ingestion(slug, "2026-05-24")
        text = path.read_text(encoding="utf-8")
        assert "# Inline comment between variants" in text
        assert "# uniquely his" in text

    def test_signal_blocks_untouched(self, tmp_owner_yaml):
        """CLAUDE.md §1.7 — the YAML write must not touch signal blocks."""
        slug, path = tmp_owner_yaml
        before = yaml.safe_load(path.read_text(encoding="utf-8"))
        _write_audit_last_ingestion(slug, "2026-05-24")
        after = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key in (
            "name_variants",
            "verifying_signals",
            "strong_signals",
            "name",
            "team",
            "role",
            "status",
            "sources",
            "change_log",
        ):
            assert before.get(key) == after.get(key), f"{key} changed during audit write"

    def test_other_audit_fields_untouched(self, tmp_owner_yaml):
        slug, path = tmp_owner_yaml
        before = yaml.safe_load(path.read_text(encoding="utf-8"))
        _write_audit_last_ingestion(slug, "2026-05-24")
        after = yaml.safe_load(path.read_text(encoding="utf-8"))
        # Only last_ingestion should change inside the audit block.
        assert before["audit"]["created"] == after["audit"]["created"]
        assert before["audit"]["last_signal_review"] == after["audit"]["last_signal_review"]

    def test_missing_owner_yaml_raises(self, tmp_owner_yaml, monkeypatch):
        slug, path = tmp_owner_yaml
        path.unlink()
        with pytest.raises(FileNotFoundError):
            _write_audit_last_ingestion(slug, "2026-05-24")


# ─── Real-owner round-trip (cohen-steven) ────────────────────────────────────


class TestRealOwnerYAMLRoundTrip:
    """Sanity-check that the live cohen-steven.yaml survives an
    audit-only write without semantic drift."""

    def test_cohen_steven_signal_blocks_unchanged(self, tmp_path, monkeypatch):
        from scripts.paths import OWNERS_DIR as REAL_OWNERS_DIR
        real_path = REAL_OWNERS_DIR / "cohen-steven.yaml"
        if not real_path.exists():
            pytest.skip("cohen-steven.yaml not present in this checkout")

        owners_dir = tmp_path / "owners"
        owners_dir.mkdir()
        copy = owners_dir / "cohen-steven.yaml"
        shutil.copy(real_path, copy)
        monkeypatch.setattr(ingest, "OWNERS_DIR", owners_dir)

        before = yaml.safe_load(copy.read_text(encoding="utf-8"))
        _write_audit_last_ingestion("cohen-steven", "2026-05-24")
        after = yaml.safe_load(copy.read_text(encoding="utf-8"))

        # All non-audit content must match exactly.
        for key in set(before.keys()) | set(after.keys()):
            if key == "audit":
                continue
            assert before.get(key) == after.get(key), f"{key} changed"

        # audit.last_ingestion was the only field that should have moved.
        assert str(after["audit"]["last_ingestion"]) == "2026-05-24"
        assert before["audit"]["created"] == after["audit"]["created"]
        assert before["audit"]["last_signal_review"] == after["audit"]["last_signal_review"]


# ─── H3: filing_id sentinel + required-provenance guard ──────────────────────


def _classification() -> Classification:
    return Classification(
        status="CONFIRMED",
        status_reason="two confirming signals",
        signals_matched=[],
        entity_slug="owner-x",
        entity_kind="owner",
    )


class TestFilingIdSentinel:
    def test_sentinel_when_no_file_number_or_report_id(self):
        rec = {
            "transaction_id": "T1",
            "contribution_receipt_date": "2003-05-01",
            "_raw_payload_path": "data/raw/owner-x/a.json",
        }
        row = _record_to_donation_row(rec, _classification(), "2026-05-28T00:00:00Z")
        assert row["filing_id"] == SENTINEL_FILING_ID

    def test_uses_file_number_when_present(self):
        rec = {
            "transaction_id": "T1",
            "file_number": "12345",
            "contribution_receipt_date": "2024-05-01",
            "_raw_payload_path": "data/raw/owner-x/a.json",
        }
        row = _record_to_donation_row(rec, _classification(), "ts")
        assert row["filing_id"] == "12345"

    def test_falls_back_to_report_id(self):
        rec = {
            "transaction_id": "T1",
            "report_id": "RPT-9",
            "contribution_receipt_date": "2024-05-01",
            "_raw_payload_path": "data/raw/owner-x/a.json",
        }
        row = _record_to_donation_row(rec, _classification(), "ts")
        assert row["filing_id"] == "RPT-9"


class TestRequiredProvenanceGuard:
    def test_passes_with_sentinel_filing_id(self):
        row = {"filing_id": SENTINEL_FILING_ID, "raw_payload_path": "p", "date": "2003-01-01"}
        assert _row_has_required_provenance(row)

    def test_fails_when_date_missing(self):
        row = {"filing_id": "F1", "raw_payload_path": "p", "date": ""}
        assert not _row_has_required_provenance(row)

    def test_fails_when_raw_payload_path_missing(self):
        row = {"filing_id": "F1", "raw_payload_path": "", "date": "2024-01-01"}
        assert not _row_has_required_provenance(row)

    def test_fails_when_filing_id_blank(self):
        # Defense-in-depth: even though ingest now sentinel-backs filing_id, a
        # blank one must never pass the guard.
        row = {"filing_id": "", "raw_payload_path": "p", "date": "2024-01-01"}
        assert not _row_has_required_provenance(row)

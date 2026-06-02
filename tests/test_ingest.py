"""Tests for the Workstream B additions in scripts/ingest.py.

Covers:
  - _resolve_min_date precedence (CLI > full_refetch > YAML > default)
  - _write_audit_last_ingestion round-trips and preserves comments/order
  - The GOVERNANCE.md §1.7 boundary: signal blocks are untouched on YAML write
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
    _dedupe_records_by_txn,
    _record_to_donation_row,
    _related_fetch_targets,
    _resolve_min_date,
    _row_has_required_provenance,
    _signal_states,
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
        """GOVERNANCE.md §1.7 — the YAML write must not touch signal blocks."""
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


# ─── Household (related-entity) fetch derivation — Phase A ───────────────────


class TestSignalStates:
    def test_none_when_filter_disabled(self):
        assert _signal_states({"states": ["CT"]}, state_filter=False) is None

    def test_returns_states_when_present(self):
        assert _signal_states({"states": ["CT", "NY"]}, state_filter=True) == ["CT", "NY"]

    def test_none_when_no_states_declared(self):
        # A related entity may file from a different documented residence than the
        # owner; absent states means "search by name only", not "fail".
        assert _signal_states({}, state_filter=True) is None
        assert _signal_states({"states": []}, state_filter=True) is None
        assert _signal_states(None, state_filter=True) is None


class TestRelatedFetchTargets:
    def _owner(self):
        return {
            "slug": "cohen-steven",
            "name_variants": ["Steven Cohen"],
            "verifying_signals": {"states": ["CT"]},
            "related_entities": [
                {
                    "kind": "spouse",
                    "slug": "cohen-alexandra",
                    "name_variants": ["Alexandra Cohen", "Alex Cohen"],
                    "verifying_signals": {"states": ["CT", "NY"]},
                }
            ],
        }

    def test_emits_target_per_related_entity(self):
        targets = _related_fetch_targets(self._owner(), state_filter=True)
        assert targets == [("cohen-alexandra", ["Alexandra Cohen", "Alex Cohen"], ["CT", "NY"])]

    def test_uses_entity_states_not_owner_states(self):
        # NY is the spouse's state, not the owner's — proves per-entity states.
        _slug, _variants, states = _related_fetch_targets(self._owner(), state_filter=True)[0]
        assert states == ["CT", "NY"]

    def test_state_filter_disabled_yields_none_states(self):
        _slug, _variants, states = _related_fetch_targets(self._owner(), state_filter=False)[0]
        assert states is None

    def test_empty_when_no_related_entities(self):
        assert _related_fetch_targets({"slug": "x"}, state_filter=True) == []

    def test_skips_entities_without_variants_or_slug(self):
        owner = {
            "related_entities": [
                {"kind": "spouse", "slug": "no-variants"},          # no name_variants
                {"kind": "child", "name_variants": ["Kid X"]},      # no slug
                "garbage",                                          # not a mapping
                {"kind": "spouse", "slug": "ok", "name_variants": ["A B"]},
            ]
        }
        targets = _related_fetch_targets(owner, state_filter=True)
        assert [t[0] for t in targets] == ["ok"]


class TestDedupeRecordsByTxn:
    def test_keeps_first_occurrence(self):
        recs = [
            {"transaction_id": "T1", "_raw_payload_path": "owner/a.json"},
            {"transaction_id": "T1", "_raw_payload_path": "spouse/b.json"},  # dup across fetches
            {"transaction_id": "T2", "_raw_payload_path": "spouse/b.json"},
        ]
        out = _dedupe_records_by_txn(recs)
        assert [r["transaction_id"] for r in out] == ["T1", "T2"]
        # First (owner-side) copy wins.
        assert out[0]["_raw_payload_path"] == "owner/a.json"

    def test_sub_id_fallback_key(self):
        recs = [{"sub_id": "S1"}, {"sub_id": "S1"}, {"sub_id": "S2"}]
        out = _dedupe_records_by_txn(recs)
        assert [r["sub_id"] for r in out] == ["S1", "S2"]

    def test_keyless_records_are_kept(self):
        # No txn/sub_id key → can't dedupe; keep (dropped later at the §1.3 gate).
        recs = [{"x": 1}, {"x": 2}]
        assert _dedupe_records_by_txn(recs) == recs


# ─── ingest_entity from_raw: household merge integration — Phase A ───────────


class TestIngestEntityRelatedFromRaw:
    """Prove the from_raw path fetches/merges related-entity raw and routes each
    record to the right slug. Uses dry_run so no DB writes happen; the classifier
    is the real one (records carry FEC-shaped fields)."""

    def _owner(self):
        return {
            "slug": "owner-h",
            "name_variants": ["Owner H"],
            "verifying_signals": {
                "cities": ["townsville"], "states": ["TT"],
                "employers": ["Owner H LLC"], "occupations": [],
            },
            "strong_signals": {"employers": [], "zip_codes": []},
            "related_entities": [
                {
                    "kind": "spouse", "slug": "spouse-h", "name_variants": ["Spouse H"],
                    "verifying_signals": {
                        "cities": ["townsville"], "states": ["TT"],
                        "employers": [], "occupations": [],
                    },
                    "strong_signals": {"employers": [], "zip_codes": []},
                }
            ],
        }

    def _patch(self, monkeypatch, owner_records, spouse_records):
        from contextlib import contextmanager

        from scripts import db

        monkeypatch.setattr(ingest, "validate_all", lambda: [])
        monkeypatch.setattr(ingest, "_load_owner", lambda slug: self._owner())

        def _fake_load(slug, raw_dir=None):
            if slug == "owner-h":
                return (list(owner_records), [Path("data/raw/owner-h/a.json")])
            if slug == "spouse-h":
                return (list(spouse_records), [Path("data/raw/spouse-h/b.json")])
            return ([], [])

        monkeypatch.setattr(ingest, "load_raw_payloads", _fake_load)

        @contextmanager
        def _fake_connect(*a, **k):
            yield None

        monkeypatch.setattr(db, "connect", _fake_connect)
        monkeypatch.setattr(db, "manual_attributions_for_slug", lambda conn, slug: {})

    def _rec(self, txn, name, **fields):
        base = {
            "transaction_id": txn,
            "contributor_name": name,
            "contributor_city": "townsville",
            "contributor_state": "TT",
            "contribution_receipt_date": "2024-03-01",
            "contribution_receipt_amount": 1000,
            "filing_id": "F1",
            "_raw_payload_path": "data/raw/x/a.json",
        }
        base.update(fields)
        return base

    def test_related_raw_is_merged_and_routed(self, monkeypatch):
        owner_rec = self._rec("T_OWN", "Owner H", contributor_employer="Owner H LLC")
        spouse_rec = self._rec("T_SP", "Spouse H")
        self._patch(monkeypatch, [owner_rec], [spouse_rec])

        summary = ingest.ingest_entity(
            "owner-h", dry_run=True, from_raw=True, process_related_entities=True
        )

        assert summary["records_fetched"] == 2
        assert summary["confirmed_count"] == 1   # Owner H: employer + city/state
        assert summary["probable_count"] == 1    # Spouse H: city/state only
        assert summary["related_breakdown"] == {
            "spouse-h": {"CONFIRMED": 0, "PROBABLE": 1, "UNCERTAIN": 0}
        }

    def test_dedupes_txn_seen_in_both_fetches(self, monkeypatch):
        owner_rec = self._rec("T_OWN", "Owner H", contributor_employer="Owner H LLC")
        spouse_rec = self._rec("T_SP", "Spouse H")
        # Spouse fetch ALSO returns the owner's txn (e.g. a joint filing). Dedupe
        # must collapse it to one, keeping the owner-side copy.
        dup_owner = self._rec("T_OWN", "Owner H", contributor_employer="Owner H LLC",
                              _raw_payload_path="data/raw/spouse-h/b.json")
        self._patch(monkeypatch, [owner_rec], [spouse_rec, dup_owner])

        summary = ingest.ingest_entity(
            "owner-h", dry_run=True, from_raw=True, process_related_entities=True
        )
        assert summary["records_fetched"] == 2  # T_OWN + T_SP, not 3

    def test_related_not_fetched_without_flag(self, monkeypatch):
        owner_rec = self._rec("T_OWN", "Owner H", contributor_employer="Owner H LLC")
        spouse_rec = self._rec("T_SP", "Spouse H")
        self._patch(monkeypatch, [owner_rec], [spouse_rec])

        summary = ingest.ingest_entity(
            "owner-h", dry_run=True, from_raw=True, process_related_entities=False
        )
        # Spouse raw never loaded; only the owner's record is present.
        assert summary["records_fetched"] == 1
        assert "related_breakdown" not in summary

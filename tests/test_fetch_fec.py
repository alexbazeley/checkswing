"""Tests for the durability additions in scripts/fetch_fec.py.

Covers Workstream B changes:
  - Cycle math (_cycles_from)
  - Checkpoint sidecar read/write/staleness
  - Auto-chunk detection threshold via mocked FEC responses
  - Explicit chunk_by_cycle path passes two_year_transaction_period
  - load_raw_payloads skips _fetch_state.json sidecars
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import responses

from scripts import fetch_fec
from scripts.fetch_fec import (
    BASE_URL,
    CHECKPOINT_STALE_DAYS,
    DEFAULT_AUTO_CHUNK_THRESHOLD,
    EARLIEST_CYCLE,
    FECClient,
    SCHEDULE_A,
    _checkpoint_path,
    _cycles_from,
    _read_checkpoint,
    _write_checkpoint,
    load_raw_payloads,
)


# ─── Cycle math ─────────────────────────────────────────────────────────────


class TestCyclesFrom:
    def test_even_start_year_starts_at_self(self):
        cycles = _cycles_from("2020-06-15")
        assert 2020 in cycles
        assert cycles[0] == 2020

    def test_odd_start_year_starts_at_next_even(self):
        cycles = _cycles_from("2021-03-01")
        assert cycles[0] == 2022

    def test_pre_floor_clamps_to_earliest(self):
        cycles = _cycles_from("1998-01-01")
        assert cycles[0] == EARLIEST_CYCLE

    def test_garbage_min_date_does_not_crash(self):
        cycles = _cycles_from("not-a-date")
        assert cycles[0] == EARLIEST_CYCLE

    def test_includes_current_or_next_even_year_as_last(self):
        cycles = _cycles_from("2024-01-01")
        # Last cycle should cover the current year's giving — i.e., be even and
        # at least as large as the current year.
        current_year = datetime.now(timezone.utc).year
        last_expected = current_year if current_year % 2 == 0 else current_year + 1
        assert cycles[-1] >= last_expected

    def test_all_returned_values_are_even(self):
        cycles = _cycles_from("2014-07-04")
        assert all(c % 2 == 0 for c in cycles)


# ─── Checkpoint sidecar ──────────────────────────────────────────────────────


@pytest.fixture
def slug_with_clean_raw_dir(tmp_path, monkeypatch):
    """Re-root the raw_dir for this slug to a temp dir.

    Patches both `raw_dir_for` AND `relpath` in fetch_fec so the persisted-raw
    helper doesn't trip on tmp paths being outside REPO_ROOT.
    """
    slug = "test-owner"
    raw_dir = tmp_path / "raw" / slug
    raw_dir.mkdir(parents=True)

    def _fake_raw_dir_for(_slug: str) -> Path:
        return raw_dir

    def _fake_relpath(p: Path) -> str:
        try:
            return Path(p).resolve().relative_to(tmp_path).as_posix()
        except ValueError:
            return str(p)

    monkeypatch.setattr(fetch_fec, "raw_dir_for", _fake_raw_dir_for)
    monkeypatch.setattr(fetch_fec, "relpath", _fake_relpath)
    return slug, raw_dir


class TestCheckpoint:
    def test_no_checkpoint_returns_none(self, slug_with_clean_raw_dir):
        slug, _ = slug_with_clean_raw_dir
        assert _read_checkpoint(slug, force_resume=False) is None

    def test_write_then_read_roundtrips(self, slug_with_clean_raw_dir):
        slug, _ = slug_with_clean_raw_dir
        state = {
            "slug": slug,
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "completed_variants": ["Steve Cohen"],
            "completed_cycles_by_variant": {"Steven A Cohen": [2020, 2022]},
        }
        _write_checkpoint(slug, state)
        loaded = _read_checkpoint(slug, force_resume=False)
        assert loaded["completed_variants"] == ["Steve Cohen"]
        assert loaded["completed_cycles_by_variant"]["Steven A Cohen"] == [2020, 2022]

    def test_stale_checkpoint_ignored_without_force(self, slug_with_clean_raw_dir):
        slug, _ = slug_with_clean_raw_dir
        stale_at = datetime.now(timezone.utc) - timedelta(days=CHECKPOINT_STALE_DAYS + 2)
        state = {
            "slug": slug,
            "started_at": stale_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "completed_variants": ["X"],
        }
        _write_checkpoint(slug, state)
        assert _read_checkpoint(slug, force_resume=False) is None

    def test_stale_checkpoint_honored_with_force(self, slug_with_clean_raw_dir):
        slug, _ = slug_with_clean_raw_dir
        stale_at = datetime.now(timezone.utc) - timedelta(days=CHECKPOINT_STALE_DAYS + 2)
        state = {
            "slug": slug,
            "started_at": stale_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "completed_variants": ["X"],
        }
        _write_checkpoint(slug, state)
        loaded = _read_checkpoint(slug, force_resume=True)
        assert loaded is not None
        assert loaded["completed_variants"] == ["X"]

    def test_corrupt_checkpoint_returns_none(self, slug_with_clean_raw_dir):
        slug, raw_dir = slug_with_clean_raw_dir
        _checkpoint_path(slug).write_text("not json {{{")
        assert _read_checkpoint(slug, force_resume=False) is None


# ─── load_raw_payloads skips sidecars ────────────────────────────────────────


class TestLoadRawPayloadsIgnoresSidecars:
    def test_underscore_files_are_skipped(self, slug_with_clean_raw_dir):
        slug, raw_dir = slug_with_clean_raw_dir
        # Write a sidecar (the kind that would break the parser if read as a
        # payload).
        (raw_dir / "_fetch_state.json").write_text('{"completed_variants": []}')
        # Write one real-ish payload envelope.
        (raw_dir / "2026-05-24T10-00-00Z__schedule_a.json").write_text(json.dumps({
            "_meta": {"slug": slug, "name_variant": "Steven A Cohen"},
            "response": {
                "results": [
                    {"transaction_id": "T1", "contributor_name": "STEVEN A COHEN"},
                ],
            },
        }))
        records, raw_paths = load_raw_payloads(slug, raw_dir=raw_dir)
        # Only the real payload should be loaded.
        assert len(records) == 1
        assert records[0]["transaction_id"] == "T1"
        assert all(not p.name.startswith("_") for p in raw_paths)


# ─── Mocked FEC: auto-chunk threshold + explicit chunk_by_cycle ──────────────


@pytest.fixture
def fec_client(monkeypatch, slug_with_clean_raw_dir):
    """A FECClient with a fake API key and rate-limit disabled for tests."""
    slug, _ = slug_with_clean_raw_dir
    monkeypatch.setenv("FEC_API_KEY", "test-key")
    monkeypatch.setattr(fetch_fec, "MIN_REQUEST_INTERVAL_S", 0.0)
    client = FECClient()
    return client, slug


def _payload(results, pages=1, last_indexes=None):
    """Build a FEC schedule_a payload envelope."""
    return {
        "results": results,
        "pagination": {
            "pages": pages,
            "per_page": 100,
            "count": len(results),
            "last_indexes": last_indexes,
        },
    }


@responses.activate
def test_chunk_by_cycle_explicit_sends_cycle_param(fec_client):
    """When chunk_by_cycle=True, each cycle's request includes
    two_year_transaction_period."""
    client, slug = fec_client
    # Stub every call: one empty page per cycle.
    responses.add(
        responses.GET,
        BASE_URL + SCHEDULE_A,
        json=_payload([], pages=0),
        status=200,
    )
    # Use a short window to keep the test fast.
    records, raws = client.fetch_schedule_a_for_name(
        slug,
        "Steven A Cohen",
        min_date="2024-01-01",
        chunk_by_cycle=True,
    )
    # Should hit FEC once per cycle in [2024, ...current+padding].
    cycles_expected = _cycles_from("2024-01-01")
    assert len(responses.calls) == len(cycles_expected)
    # Each call should carry two_year_transaction_period.
    seen_cycles = sorted(
        int(call.request.url.split("two_year_transaction_period=")[1].split("&")[0])
        for call in responses.calls
    )
    assert seen_cycles == sorted(cycles_expected)


@responses.activate
def test_auto_chunk_triggers_when_pages_over_threshold(fec_client):
    """First page reports many pages → fetch switches to cycle mode."""
    client, slug = fec_client
    # Sniff page 1 reports lots of pages.
    responses.add(
        responses.GET,
        BASE_URL + SCHEDULE_A,
        json=_payload(
            [{"transaction_id": "T0", "contributor_name": "STEVEN A COHEN"}],
            pages=DEFAULT_AUTO_CHUNK_THRESHOLD + 5,
            last_indexes={"last_contribution_receipt_date": "2024-01-01"},
        ),
        status=200,
    )
    # Every following call returns empty (cycle mode walks each cycle).
    responses.add(
        responses.GET,
        BASE_URL + SCHEDULE_A,
        json=_payload([], pages=0),
        status=200,
    )
    records, raws = client.fetch_schedule_a_for_name(
        slug,
        "Steven A Cohen",
        min_date="2024-01-01",
        chunk_by_cycle=False,  # let auto-detect fire
    )
    # 1 sniff call + len(cycles) cycle calls.
    cycles_expected = _cycles_from("2024-01-01")
    assert len(responses.calls) == 1 + len(cycles_expected)
    # The sniff record gets discarded; only cycle records (none in this stub).
    assert records == []


@responses.activate
def test_normal_pagination_when_under_threshold(fec_client):
    """When sniff pages < threshold, unified pagination continues from page 2."""
    client, slug = fec_client
    # Page 1: claims 2 pages total, has last_indexes pointing to page 2.
    responses.add(
        responses.GET,
        BASE_URL + SCHEDULE_A,
        json=_payload(
            [{"transaction_id": "T1", "contributor_name": "STEVEN A COHEN"}],
            pages=2,
            last_indexes={"last_contribution_receipt_date": "2024-06-01"},
        ),
        status=200,
    )
    # Page 2: terminal (no last_indexes).
    responses.add(
        responses.GET,
        BASE_URL + SCHEDULE_A,
        json=_payload(
            [{"transaction_id": "T2", "contributor_name": "STEVEN A COHEN"}],
            pages=2,
            last_indexes=None,
        ),
        status=200,
    )
    records, raws = client.fetch_schedule_a_for_name(
        slug,
        "Steven A Cohen",
        min_date="2024-01-01",
    )
    # 2 calls total (sniff + page 2), 2 records, no cycle param sent.
    assert len(responses.calls) == 2
    assert {r["transaction_id"] for r in records} == {"T1", "T2"}
    for call in responses.calls:
        assert "two_year_transaction_period" not in call.request.url


@responses.activate
def test_fetch_all_variants_clears_checkpoint_on_success(fec_client):
    client, slug = fec_client
    responses.add(
        responses.GET,
        BASE_URL + SCHEDULE_A,
        json=_payload([], pages=0),
        status=200,
    )
    # Run fetch — should leave no checkpoint behind.
    records, raws = client.fetch_all_name_variants(
        slug,
        ["Steven A Cohen", "Steve Cohen"],
        min_date="2024-01-01",
    )
    assert not _checkpoint_path(slug).exists()


@responses.activate
def test_fetch_all_variants_resumes_skipping_completed(fec_client):
    client, slug = fec_client
    # Seed a checkpoint claiming "Steven A Cohen" is done.
    _write_checkpoint(slug, {
        "slug": slug,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "completed_variants": ["Steven A Cohen"],
        "completed_cycles_by_variant": {},
    })
    responses.add(
        responses.GET,
        BASE_URL + SCHEDULE_A,
        json=_payload([], pages=0),
        status=200,
    )
    # Now fetch both variants — only "Steve Cohen" should hit FEC.
    records, raws = client.fetch_all_name_variants(
        slug,
        ["Steven A Cohen", "Steve Cohen"],
        min_date="2024-01-01",
    )
    # All calls should have contributor_name=Steve+Cohen, not "Steven A Cohen".
    for call in responses.calls:
        assert "Steve+Cohen" in call.request.url or "Steve%20Cohen" in call.request.url
        assert "Steven+A+Cohen" not in call.request.url
        assert "Steven%20A%20Cohen" not in call.request.url
    # Checkpoint should be gone after success.
    assert not _checkpoint_path(slug).exists()

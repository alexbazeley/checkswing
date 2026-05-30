"""Tests for scripts/refresh.py — the self-maintaining refresh layer.

Covers Workstream C:
  - File lock prevents concurrent runs
  - _list_active_owners filters by status and respects --only
  - refresh_all per-owner failure isolation (one fail does not abort others)
  - refresh_all calls build_data exactly when records were added
  - refresh_all dry-run path writes no log and skips data.json
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts import refresh
from scripts.refresh import (
    _acquire_lock,
    _list_active_owners,
    refresh_all,
)


# ─── Common fixture: temp owners dir + temp lock + temp PROVENANCE_LOG ───────


_OWNER_TEMPLATE = """\
slug: {slug}
name: {name}
team: {team}
role: Principal owner
status: {status}
tenure_start_date: 2020-01-01
tenure_end_date: null

name_variants:
  - "{name}"
  - "{name} Jr."

verifying_signals:
  cities: ["sampleville"]
  states: ["SA"]
  employers: ["Sample Corp"]
  occupations: ["investor"]

strong_signals:
  employers: []
  zip_codes: []

sources:
  - description: "MLB ownership"
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


def _make_owner(owners_dir: Path, slug: str, status: str, name: str = "Test Owner", team: str = "Test Team") -> None:
    (owners_dir / f"{slug}.yaml").write_text(
        _OWNER_TEMPLATE.format(slug=slug, name=name, team=team, status=status),
        encoding="utf-8",
    )


@pytest.fixture
def refresh_world(tmp_path, monkeypatch):
    """Re-root every filesystem dependency of refresh.py into tmp_path."""
    owners_dir = tmp_path / "owners"
    owners_dir.mkdir()
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    lock_path = data_dir / ".refresh.lock"
    provenance_path = catalog_dir / "PROVENANCE_LOG.md"

    monkeypatch.setattr(refresh, "OWNERS_DIR", owners_dir)
    monkeypatch.setattr(refresh, "REFRESH_LOCK", lock_path)
    monkeypatch.setattr(refresh, "PROVENANCE_LOG", provenance_path)

    return {
        "owners_dir": owners_dir,
        "catalog_dir": catalog_dir,
        "data_dir": data_dir,
        "lock_path": lock_path,
        "provenance_path": provenance_path,
    }


# ─── _acquire_lock ───────────────────────────────────────────────────────────


class TestAcquireLock:
    def test_releases_lock_on_normal_exit(self, refresh_world):
        lock = refresh_world["lock_path"]
        with _acquire_lock(lock):
            assert lock.exists()
        assert not lock.exists()

    def test_releases_lock_on_exception(self, refresh_world):
        lock = refresh_world["lock_path"]
        with pytest.raises(ValueError):
            with _acquire_lock(lock):
                assert lock.exists()
                raise ValueError("boom")
        assert not lock.exists()

    def test_concurrent_acquire_raises(self, refresh_world):
        lock = refresh_world["lock_path"]
        with _acquire_lock(lock):
            with pytest.raises(RuntimeError, match="Refresh already running"):
                with _acquire_lock(lock):
                    pass

    def test_acquire_reclaims_stale_lock(self, refresh_world):
        # A crashed run leaves an ancient, pid-less lock — it must be reclaimed
        # rather than wedging every later refresh.
        lock = refresh_world["lock_path"]
        lock.write_text("2000-01-01T00:00:00Z\n", encoding="utf-8")
        with _acquire_lock(lock):
            assert lock.exists()
        assert not lock.exists()


class TestLockStaleness:
    def test_live_pid_not_stale(self):
        import os
        from scripts.refresh import _lock_is_stale
        assert _lock_is_stale(f"2000-01-01T00:00:00Z · pid={os.getpid()}") is False

    def test_dead_pid_is_stale(self):
        import os
        from scripts.refresh import _lock_is_stale
        dead = 999999
        try:
            os.kill(dead, 0)
            pytest.skip("pid 999999 unexpectedly exists on this host")
        except ProcessLookupError:
            pass
        except PermissionError:
            pytest.skip("pid 999999 exists (no permission) on this host")
        assert _lock_is_stale(f"2026-01-01T00:00:00Z · pid={dead}") is True

    def test_recent_pidless_lock_not_stale(self):
        from scripts.refresh import _lock_is_stale, _utc_now_iso
        assert _lock_is_stale(_utc_now_iso()) is False

    def test_old_pidless_lock_is_stale(self):
        from scripts.refresh import _lock_is_stale
        assert _lock_is_stale("2000-01-01T00:00:00Z") is True


# ─── _list_active_owners ─────────────────────────────────────────────────────


class TestListActiveOwners:
    def test_includes_pilot_and_active(self, refresh_world):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")
        _make_owner(d, "b-owner", "active")
        _make_owner(d, "c-owner", "paused")
        _make_owner(d, "d-owner", "queued")
        assert _list_active_owners() == ["a-owner", "b-owner"]

    def test_skips_underscore_files(self, refresh_world):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")
        # A registry / template file should be ignored.
        (d / "_registry.yaml").write_text("# registry comment")
        (d / "_template.yaml").write_text("# template comment")
        assert _list_active_owners() == ["a-owner"]

    def test_only_filters_to_subset(self, refresh_world):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")
        _make_owner(d, "b-owner", "pilot")
        _make_owner(d, "c-owner", "pilot")
        # Caller order is preserved (predictable for tests).
        assert _list_active_owners(only=["c-owner", "a-owner"]) == ["c-owner", "a-owner"]

    def test_only_unknown_slug_raises(self, refresh_world):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")
        with pytest.raises(RuntimeError, match="not found"):
            _list_active_owners(only=["a-owner", "z-typo"])

    def test_only_paused_slug_raises(self, refresh_world):
        """A `paused` owner is not active and should be rejected if --only lists it."""
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "paused")
        with pytest.raises(RuntimeError):
            _list_active_owners(only=["a-owner"])

    def test_no_active_owners_returns_empty(self, refresh_world):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "paused")
        assert _list_active_owners() == []


# ─── refresh_all per-owner behavior ──────────────────────────────────────────


def _stub_ingest(records_by_slug: dict, failures: set[str] | None = None):
    """Build a stub ingest_entity that returns canned summaries per slug."""
    failures = failures or set()

    def _fake(slug, **kwargs):
        if slug in failures:
            raise RuntimeError(f"simulated FEC timeout on {slug}")
        return {
            "run_id": "test",
            "entity_slug": slug,
            "records_fetched": records_by_slug.get(slug, 0),
            "confirmed_count": records_by_slug.get(slug, 0),
            "probable_count": 0,
            "uncertain_count": 0,
            "audit_last_ingestion_set": "2026-05-24",
        }

    return _fake


class TestRefreshAll:
    def test_isolates_per_owner_failures(self, refresh_world, monkeypatch):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")
        _make_owner(d, "b-owner", "pilot")
        _make_owner(d, "c-owner", "pilot")

        monkeypatch.setattr(
            refresh, "ingest_entity",
            _stub_ingest({"a-owner": 5, "c-owner": 7}, failures={"b-owner"}),
        )
        # Stub out the data.json regen so the test stays hermetic.
        monkeypatch.setattr(refresh, "_rebuild_data_json", lambda: (True, "stub"))

        summary = refresh_all()
        assert summary["owners_attempted"] == 3
        assert summary["owners_succeeded"] == 2
        assert summary["owners_failed"] == 1
        assert summary["failed_owners"] == ["b-owner"]
        # Both successes recorded; failure has error string.
        assert summary["per_owner"]["a-owner"]["status"] == "ok"
        assert summary["per_owner"]["c-owner"]["status"] == "ok"
        assert summary["per_owner"]["b-owner"]["status"] == "error"
        assert "simulated FEC timeout" in summary["per_owner"]["b-owner"]["error"]

    def test_calls_build_data_when_records_added(self, refresh_world, monkeypatch):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")

        monkeypatch.setattr(refresh, "ingest_entity", _stub_ingest({"a-owner": 3}))

        calls = []
        def _fake_rebuild():
            calls.append("rebuild")
            return True, "wrote data.json"
        monkeypatch.setattr(refresh, "_rebuild_data_json", _fake_rebuild)

        summary = refresh_all()
        assert calls == ["rebuild"]
        assert summary["data_json_regenerated"] is True

    def test_skips_build_data_when_no_new_records(self, refresh_world, monkeypatch):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")

        # records_fetched = 0
        monkeypatch.setattr(refresh, "ingest_entity", _stub_ingest({"a-owner": 0}))

        calls = []
        def _fake_rebuild():
            calls.append("rebuild")
            return True, None
        monkeypatch.setattr(refresh, "_rebuild_data_json", _fake_rebuild)

        summary = refresh_all()
        assert calls == []
        assert summary["data_json_regenerated"] is False

    def test_skip_data_json_flag(self, refresh_world, monkeypatch):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")

        monkeypatch.setattr(refresh, "ingest_entity", _stub_ingest({"a-owner": 10}))
        calls = []
        monkeypatch.setattr(refresh, "_rebuild_data_json", lambda: (calls.append("rebuild"), (True, None))[1])

        summary = refresh_all(skip_data_json=True)
        assert calls == []
        assert summary["data_json_regenerated"] is False

    def test_dry_run_skips_provenance_log_and_data_json(self, refresh_world, monkeypatch):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")

        monkeypatch.setattr(refresh, "ingest_entity", _stub_ingest({"a-owner": 10}))
        calls = []
        monkeypatch.setattr(refresh, "_rebuild_data_json", lambda: (calls.append("rebuild"), (True, None))[1])

        summary = refresh_all(dry_run=True)
        assert calls == []
        assert summary["dry_run"] == 1
        # PROVENANCE_LOG should not have been touched in dry-run.
        assert not refresh_world["provenance_path"].exists()

    def test_provenance_log_appended_on_real_run(self, refresh_world, monkeypatch):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")

        monkeypatch.setattr(refresh, "ingest_entity", _stub_ingest({"a-owner": 1}))
        monkeypatch.setattr(refresh, "_rebuild_data_json", lambda: (True, None))

        summary = refresh_all()
        text = refresh_world["provenance_path"].read_text(encoding="utf-8")
        assert "REFRESH RUN" in text
        assert summary["refresh_id"] in text
        assert "owners_succeeded" in text

    def test_only_slug_passes_through(self, refresh_world, monkeypatch):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")
        _make_owner(d, "b-owner", "pilot")

        seen = []
        def _record(slug, **kwargs):
            seen.append(slug)
            return {"records_fetched": 0}
        monkeypatch.setattr(refresh, "ingest_entity", _record)
        monkeypatch.setattr(refresh, "_rebuild_data_json", lambda: (True, None))

        refresh_all(only=["b-owner"])
        assert seen == ["b-owner"]

    def test_full_refetch_propagates_to_ingest(self, refresh_world, monkeypatch):
        d = refresh_world["owners_dir"]
        _make_owner(d, "a-owner", "pilot")

        passthrough = {}
        def _record(slug, **kwargs):
            passthrough.update(kwargs)
            return {"records_fetched": 0}
        monkeypatch.setattr(refresh, "ingest_entity", _record)
        monkeypatch.setattr(refresh, "_rebuild_data_json", lambda: (True, None))

        refresh_all(full_refetch=True, chunk_by_cycle=True)
        assert passthrough.get("full_refetch") is True
        assert passthrough.get("chunk_by_cycle") is True

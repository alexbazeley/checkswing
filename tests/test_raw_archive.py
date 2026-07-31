"""§2.1 read side: telling 'recoverable from R2' apart from 'truly lost'.

Before this, every consumer checked local disk alone, so a payload sitting
safely in R2 and a payload that no longer exists anywhere produced the same
verdict — which made the reclassify guard a dead end rather than a recoverable
state. These tests pin the distinction, and pin the degradation behaviour: a
developer without R2 credentials must still be able to run everything.
"""
from __future__ import annotations

import pytest

from scripts import db, ingest, raw_archive


R2_ENV = {
    "RAW_ARCHIVE_S3_BUCKET": "checkswing-raw",
    "RAW_ARCHIVE_S3_ENDPOINT": "https://example.r2.cloudflarestorage.com",
    "AWS_ACCESS_KEY_ID": "k" * 32,
    "AWS_SECRET_ACCESS_KEY": "s" * 64,
}


def _row(txn, slug, raw_path, **ov):
    base = {
        "transaction_id": txn, "entity_slug": slug, "entity_kind": "owner",
        "parent_owner_slug": None, "status": "CONFIRMED",
        "status_reason": "two confirming signals", "signals_matched": "[]",
        "contributor_name_raw": "X", "contributor_employer_raw": "",
        "contributor_occupation_raw": "", "contributor_city": "", "contributor_state": "",
        "contributor_zip": "", "recipient_committee_id": "C1",
        "recipient_committee_name": "Cmte", "recipient_candidate_id": "",
        "recipient_candidate_name": "", "recipient_party": "", "recipient_office": None,
        "amount": 100.0, "date": "2024-01-15", "election_cycle": 2024, "report_type": None,
        "filing_id": "F1", "raw_payload_path": raw_path,
        "ingested_at": "2026-01-01T00:00:00Z",
    }
    base.update(ov)
    return base


@pytest.fixture
def dbp(tmp_path):
    p = tmp_path / "m.db"
    db.init(p)
    return p


class TestBucketKeyMapping:
    """The key layout must mirror archive_raw.sh's `sync data/raw/ → s3://…/raw/`,
    or a rehydrate silently looks in the wrong place."""

    def test_repo_relative_path_maps_to_raw_prefix(self):
        assert raw_archive.bucket_key_for("data/raw/fisher-john/x.json") == "raw/fisher-john/x.json"

    def test_mapping_is_total_not_raising(self):
        assert raw_archive.bucket_key_for("odd/path.json").startswith("raw/")


class TestConfiguration:
    def test_unconfigured_when_env_absent(self, monkeypatch):
        for k in R2_ENV:
            monkeypatch.delenv(k, raising=False)
        assert raw_archive.is_configured() is False
        assert raw_archive.bucket_status().state == raw_archive.UNCONFIGURED

    def test_partial_credentials_are_not_configured(self, monkeypatch):
        for k in R2_ENV:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("RAW_ARCHIVE_S3_BUCKET", "checkswing-raw")
        assert raw_archive.is_configured() is False

    def test_unreachable_bucket_degrades_and_does_not_raise(self, monkeypatch):
        for k, v in R2_ENV.items():
            monkeypatch.setenv(k, v)

        def _boom():
            raise OSError("network down")

        monkeypatch.setattr(raw_archive, "_client", _boom)
        st = raw_archive.bucket_status()
        assert st.state == raw_archive.UNAVAILABLE
        assert not st.usable
        assert "network down" in st.detail


class TestCoverageSplit:
    def _seed(self, dbp, tmp_path):
        present = tmp_path / "present.json"
        present.write_text("{}")
        with db.connect(dbp) as conn:
            db.insert_donation(conn, _row("T1", "owner-x", str(present)))
            db.insert_donation(conn, _row("T2", "owner-x", "data/raw/owner-x/in-r2.json"))
            db.insert_donation(conn, _row("T3", "owner-x", "data/raw/owner-x/gone.json"))

    def test_local_only_report_cannot_distinguish(self, dbp, tmp_path):
        self._seed(dbp, tmp_path)
        rep = ingest.raw_coverage_report(db_path=dbp)
        assert rep["rows_missing_raw"] == 2
        assert "rows_truly_lost" not in rep, "local-only must not claim to know recoverability"

    def test_bucket_split_separates_recoverable_from_lost(self, dbp, tmp_path, monkeypatch):
        self._seed(dbp, tmp_path)
        monkeypatch.setattr(
            raw_archive, "bucket_status",
            lambda *a, **k: raw_archive.BucketStatus(
                raw_archive.OK, keys={"raw/owner-x/in-r2.json"}
            ),
        )
        rep = ingest.raw_coverage_report(db_path=dbp, check_bucket=True)
        assert rep["rows_missing_raw"] == 2
        assert rep["rows_recoverable_from_bucket"] == 1
        assert rep["rows_truly_lost"] == 1
        assert rep["by_slug"]["owner-x"]["recoverable"] == 1
        assert rep["by_slug"]["owner-x"]["lost"] == 1

    def test_unusable_bucket_reports_state_without_claiming_loss(self, dbp, tmp_path, monkeypatch):
        self._seed(dbp, tmp_path)
        monkeypatch.setattr(
            raw_archive, "bucket_status",
            lambda *a, **k: raw_archive.BucketStatus(raw_archive.UNCONFIGURED, detail="no creds"),
        )
        rep = ingest.raw_coverage_report(db_path=dbp, check_bucket=True)
        assert rep["bucket"]["state"] == raw_archive.UNCONFIGURED
        assert "rows_truly_lost" not in rep
        assert "rows_recoverable_from_bucket" not in rep


class TestRehydrate:
    def _seed(self, dbp):
        with db.connect(dbp) as conn:
            db.insert_donation(conn, _row("T2", "owner-x", "data/raw/owner-x/in-r2.json"))
            db.insert_donation(conn, _row("T3", "owner-x", "data/raw/owner-x/gone.json"))

    def _ok_bucket(self, monkeypatch):
        monkeypatch.setattr(
            raw_archive, "bucket_status",
            lambda *a, **k: raw_archive.BucketStatus(
                raw_archive.OK, keys={"raw/owner-x/in-r2.json"}
            ),
        )

    def test_dry_run_downloads_nothing(self, dbp, monkeypatch):
        self._seed(dbp)
        self._ok_bucket(monkeypatch)
        calls = []
        monkeypatch.setattr(raw_archive, "download", lambda p, dest=None: calls.append(p))
        res = ingest.rehydrate_raw(db_path=dbp, dry_run=True)
        assert calls == [], "dry run must not touch the network"
        assert res["candidates"] == 1
        assert res["lost"] == 1

    def test_restores_only_what_the_bucket_has(self, dbp, monkeypatch):
        self._seed(dbp)
        self._ok_bucket(monkeypatch)
        got = []
        monkeypatch.setattr(raw_archive, "download", lambda p, dest=None: got.append(p))
        res = ingest.rehydrate_raw(db_path=dbp)
        assert got == ["data/raw/owner-x/in-r2.json"]
        assert res["restored"] == 1
        assert res["lost"] == 1
        assert "data/raw/owner-x/gone.json" in res["lost_examples"]

    def test_one_failed_object_does_not_stop_the_rest(self, dbp, monkeypatch):
        with db.connect(dbp) as conn:
            db.insert_donation(conn, _row("A", "owner-x", "data/raw/owner-x/a.json"))
            db.insert_donation(conn, _row("B", "owner-x", "data/raw/owner-x/b.json"))
        monkeypatch.setattr(
            raw_archive, "bucket_status",
            lambda *a, **k: raw_archive.BucketStatus(
                raw_archive.OK, keys={"raw/owner-x/a.json", "raw/owner-x/b.json"}
            ),
        )

        def _dl(p, dest=None):
            if p.endswith("a.json"):
                raise OSError("403")

        monkeypatch.setattr(raw_archive, "download", _dl)
        res = ingest.rehydrate_raw(db_path=dbp)
        assert res["restored"] == 1, "the good object must still be restored"
        assert len(res["failed"]) == 1
        assert "403" in res["failed"][0]["error"]

    def test_unreadable_bucket_reports_unknown_not_lost(self, dbp, monkeypatch):
        """Without a readable archive we have NOT established loss — only absence
        from this disk. Reporting those as `lost` would re-create the exact
        conflation this change removes."""
        self._seed(dbp)
        monkeypatch.setattr(
            raw_archive, "bucket_status",
            lambda *a, **k: raw_archive.BucketStatus(raw_archive.UNCONFIGURED, detail="no creds"),
        )
        res = ingest.rehydrate_raw(db_path=dbp)
        assert res["lost"] is None
        assert res["missing_locally"] == 2
        assert "unknown" in res["recoverability"]


class TestOutputOrdering:
    """The headline numbers must precede `by_slug`.

    `by_slug` runs to dozens of lines, so totals placed after it are invisible to
    the `| head` that reading this output invites — which is exactly what happened
    the first time the command was run against the live bucket.
    """

    def test_summary_keys_come_before_by_slug(self, dbp, tmp_path, monkeypatch):
        with db.connect(dbp) as conn:
            db.insert_donation(conn, _row("T2", "owner-x", "data/raw/owner-x/in-r2.json"))
            db.insert_donation(conn, _row("T3", "owner-x", "data/raw/owner-x/gone.json"))
        monkeypatch.setattr(
            raw_archive, "bucket_status",
            lambda *a, **k: raw_archive.BucketStatus(
                raw_archive.OK, keys={"raw/owner-x/in-r2.json"}
            ),
        )
        keys = list(ingest.raw_coverage_report(db_path=dbp, check_bucket=True))
        assert keys[-1] == "by_slug", "by_slug must be last so totals survive truncation"
        for headline in ("rows_recoverable_from_bucket", "rows_truly_lost", "bucket"):
            assert keys.index(headline) < keys.index("by_slug")

    def test_local_only_also_puts_by_slug_last(self, dbp, tmp_path):
        with db.connect(dbp) as conn:
            db.insert_donation(conn, _row("T3", "owner-x", "data/raw/owner-x/gone.json"))
        assert list(ingest.raw_coverage_report(db_path=dbp))[-1] == "by_slug"

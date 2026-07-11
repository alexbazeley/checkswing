"""Snapshot retention policy (§2.3): keep < keep_days + newest per operation group."""
from __future__ import annotations

from datetime import datetime, timezone

from scripts.prune_snapshots import _parse, prune


def _touch(d, name):
    (d / name).write_bytes(b"x" * 10)


def test_parse_operation_groups():
    assert _parse("2026-06-04T01-10-00Z__committees_ingest_2026-06-04T01-10-00Z.db")[1] == "committees_ingest"
    assert _parse("2026-06-04T02-05-37Z__1ee196c8.db")[1] == "ingestion-run"
    assert _parse("2026-07-01T00-00-00Z__pre-reclassify-fisher-john.db")[1] == "pre-reclassify-fisher-john"
    assert _parse("2026-07-01T00-00-00Z__backfill_memo_fields.db")[1] == "backfill_memo_fields"
    assert _parse("not-a-snapshot.txt") is None


def test_keeps_recent_and_newest_prunes_old(tmp_path, monkeypatch):
    import scripts.prune_snapshots as m
    monkeypatch.setattr(m, "PROVENANCE_LOG", tmp_path / "PROV.md")
    d = tmp_path / "snaps"; d.mkdir()
    # A recent snapshot (kept: < 30d).
    _touch(d, "2026-07-05T00-00-00Z__ingestion-run.db")   # wait, uuid form below
    _touch(d, "2026-07-05T00-00-00Z__aaaaaaaa.db")        # recent ingestion (kept, <30d)
    # Two OLD committees_ingest snapshots — newest kept, older pruned.
    _touch(d, "2026-01-01T00-00-00Z__committees_ingest_2026-01-01T00-00-00Z.db")  # old, not newest → prune
    _touch(d, "2026-02-01T00-00-00Z__committees_ingest_2026-02-01T00-00-00Z.db")  # old, newest in group → keep
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)

    dry = prune(keep_days=30, dry_run=True, snapshots_dir=d, now=now)
    assert dry["pruned"] == 1 and dry["kept"] == 3
    # Nothing deleted on dry run.
    assert len(list(d.glob("*.db"))) == 4

    applied = prune(keep_days=30, dry_run=False, snapshots_dir=d, now=now)
    assert applied["pruned"] == 1
    remaining = {p.name for p in d.glob("*.db")}
    assert "2026-01-01T00-00-00Z__committees_ingest_2026-01-01T00-00-00Z.db" not in remaining
    assert "2026-02-01T00-00-00Z__committees_ingest_2026-02-01T00-00-00Z.db" in remaining  # newest-in-group
    assert (tmp_path / "PROV.md").exists()  # prune logged

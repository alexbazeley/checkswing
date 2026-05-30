"""Tests for the matrix consolidation merge (scripts/merge_buckets.py).

The disjoint-owner invariant is load-bearing: the per-slug merge is DELETE +
INSERT (last-writer-wins), so overlap would silently drop a bucket's rows.
These pin the happy-path merge, the no-new-runs case, missing-bucket tolerance,
and the disjointness guard.
"""
from __future__ import annotations

from pathlib import Path

from scripts import db
from scripts.merge_buckets import main as merge_main


def _make_db(path: Path) -> None:
    db.init(path)


def _add_run(path: Path, run_id: str, slug: str) -> None:
    with db.connect(path) as conn:
        db.insert_ingestion_run(
            conn,
            {
                "run_id": run_id,
                "entity_slug": slug,
                "started_at": "2026-05-28T00:00:00Z",
                "completed_at": "2026-05-28T00:01:00Z",
                "period_start": "2000-01-01",
                "period_end": "2026-05-28",
                "name_variants_queried": "[]",
                "api_calls_made": 1,
                "records_fetched": 1,
                "confirmed_count": 1,
                "probable_count": 0,
                "uncertain_count": 0,
                "snapshot_path": None,
                "notes": "",
                "dry_run": 0,
            },
        )


def _donation_row(txn: str, slug: str) -> dict:
    return {
        "transaction_id": txn,
        "entity_slug": slug,
        "entity_kind": "owner",
        "parent_owner_slug": None,
        "status": "CONFIRMED",
        "status_reason": "x",
        "signals_matched": "[]",
        "contributor_name_raw": "N",
        "contributor_employer_raw": "",
        "contributor_occupation_raw": "",
        "contributor_city": "",
        "contributor_state": "",
        "contributor_zip": "",
        "recipient_committee_id": "C1",
        "recipient_committee_name": "Cmte",
        "recipient_candidate_id": "",
        "recipient_candidate_name": "",
        "recipient_party": "DEM",
        "recipient_office": None,
        "amount": 100.0,
        "date": "2024-01-01",
        "election_cycle": 2024,
        "report_type": None,
        "filing_id": "F1",
        "raw_payload_path": "data/raw/x/a.json",
        "ingested_at": "2026-05-28T00:00:00Z",
    }


def _add_donation(path: Path, txn: str, slug: str) -> None:
    with db.connect(path) as conn:
        db.insert_donation(conn, _donation_row(txn, slug))


def _count(path: Path, slug: str) -> int:
    with db.connect(path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM donations WHERE entity_slug = ?", (slug,)
        ).fetchone()[0]


def test_disjoint_merge_adopts_both_buckets(tmp_path):
    cons = tmp_path / "cons.db"
    b0 = tmp_path / "b0.db"
    b1 = tmp_path / "b1.db"
    _make_db(cons)
    _make_db(b0)
    _make_db(b1)
    _add_run(b0, "R0", "alice")
    _add_donation(b0, "T1", "alice")
    _add_run(b1, "R1", "bob")
    _add_donation(b1, "T2", "bob")

    rc = merge_main(["--consolidated", str(cons), "--bucket-db", str(b0), "--bucket-db", str(b1)])
    assert rc == 0
    assert _count(cons, "alice") == 1
    assert _count(cons, "bob") == 1


def test_overlapping_buckets_abort(tmp_path):
    cons = tmp_path / "cons.db"
    b0 = tmp_path / "b0.db"
    b1 = tmp_path / "b1.db"
    _make_db(cons)
    _make_db(b0)
    _make_db(b1)
    # Both buckets touch the same owner — must abort before merging.
    _add_run(b0, "R0", "alice")
    _add_donation(b0, "T1", "alice")
    _add_run(b1, "R1", "alice")
    _add_donation(b1, "T2", "alice")

    rc = merge_main(["--consolidated", str(cons), "--bucket-db", str(b0), "--bucket-db", str(b1)])
    assert rc == 3
    # Aborted before any merge: consolidated untouched.
    assert _count(cons, "alice") == 0


def test_no_new_runs_is_noop(tmp_path):
    cons = tmp_path / "cons.db"
    b0 = tmp_path / "b0.db"
    _make_db(cons)
    _make_db(b0)
    # The same run already exists in the consolidated DB → bucket has no new runs.
    _add_run(cons, "R0", "alice")
    _add_run(b0, "R0", "alice")

    rc = merge_main(["--consolidated", str(cons), "--bucket-db", str(b0)])
    assert rc == 0


def test_missing_bucket_db_tolerated(tmp_path):
    cons = tmp_path / "cons.db"
    b0 = tmp_path / "b0.db"
    missing = tmp_path / "nope.db"
    _make_db(cons)
    _make_db(b0)
    _add_run(b0, "R0", "alice")
    _add_donation(b0, "T1", "alice")

    rc = merge_main(["--consolidated", str(cons), "--bucket-db", str(b0), "--bucket-db", str(missing)])
    assert rc == 0
    assert _count(cons, "alice") == 1


def test_no_bucket_dbs_returns_error(tmp_path):
    cons = tmp_path / "cons.db"
    _make_db(cons)
    rc = merge_main(["--consolidated", str(cons)])
    assert rc == 2

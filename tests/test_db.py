"""Supersession + idempotency tests for db.insert_donation (GOVERNANCE.md §1.5, §1.10)."""
from __future__ import annotations

import pytest

from scripts import db


def _row(txn: str = "TXN1", **overrides) -> dict:
    base = {
        "transaction_id": txn,
        "entity_slug": "owner-x",
        "entity_kind": "owner",
        "parent_owner_slug": None,
        "status": "CONFIRMED",
        "status_reason": "two confirming signals",
        "signals_matched": "[]",
        "contributor_name_raw": "John Doe",
        "contributor_employer_raw": "Acme",
        "contributor_occupation_raw": "ceo",
        "contributor_city": "Greenwich",
        "contributor_state": "CT",
        "contributor_zip": "06830",
        "recipient_committee_id": "C001",
        "recipient_committee_name": "Committee",
        "recipient_candidate_id": "",
        "recipient_candidate_name": "",
        "recipient_party": "DEM",
        "recipient_office": None,
        "amount": 1000.0,
        "date": "2024-01-15",
        "election_cycle": 2024,
        "report_type": None,
        "filing_id": "F100",
        "raw_payload_path": "data/raw/owner-x/x.json",
        "ingested_at": "2026-05-28T00:00:00Z",
    }
    base.update(overrides)
    return base


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "test.db"
    db.init(p)
    return p


def _count(p, where: str = "", params=()):
    with db.connect(p) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM donations {where}", params).fetchone()[0]


class TestIdempotency:
    def test_same_txn_inserted_once(self, db_path):
        with db.connect(db_path) as conn:
            assert db.insert_donation(conn, _row())[0] == "inserted"
        with db.connect(db_path) as conn:
            assert db.insert_donation(conn, _row())[0] == "unchanged"
        assert _count(db_path) == 1

    def test_status_change_alone_does_not_supersede(self, db_path):
        # A reclassification changes our derived status but not FEC substance —
        # insert_donation treats it as an idempotent no-op (reclassify uses
        # DELETE+reinsert, not this upsert path).
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(status="CONFIRMED"))
        with db.connect(db_path) as conn:
            action, _ = db.insert_donation(
                conn, _row(status="PROBABLE", status_reason="one confirming signal")
            )
            assert action == "unchanged"
        assert _count(db_path) == 1
        with db.connect(db_path) as conn:
            row = conn.execute(
                "SELECT status FROM donations WHERE transaction_id='TXN1'"
            ).fetchone()
            assert row["status"] == "CONFIRMED"  # original retained


class TestSupersession:
    def test_amount_restatement_supersedes(self, db_path):
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(amount=1000.0))
        with db.connect(db_path) as conn:
            action, reason = db.insert_donation(conn, _row(amount=2500.0))
            assert action == "superseded"
            assert "amount" in reason

        # Two rows now: one live under the canonical key, one archived.
        assert _count(db_path) == 2
        with db.connect(db_path) as conn:
            live = conn.execute(
                "SELECT * FROM donations WHERE transaction_id='TXN1'"
            ).fetchone()
            assert live["amount"] == 2500.0
            assert live["superseded_by"] is None
            assert live["status"] == "CONFIRMED"

            archived = conn.execute(
                "SELECT * FROM donations WHERE superseded_by='TXN1'"
            ).fetchone()
            assert archived is not None
            assert archived["status"] == "SUPERSEDED"
            assert archived["amount"] == 1000.0
            assert archived["transaction_id"].startswith("TXN1~superseded~")
            # Old row preserved, not deleted (§1.10).
            assert archived["entity_slug"] == "owner-x"

    def test_recipient_restatement_supersedes(self, db_path):
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(recipient_committee_id="C001"))
        with db.connect(db_path) as conn:
            action, reason = db.insert_donation(conn, _row(recipient_committee_id="C999"))
            assert action == "superseded"
            assert "recipient_committee_id" in reason

    def test_superseded_rows_excluded_from_live_filter(self, db_path):
        # SUPERSEDED rows must not appear under the CONFIRMED/PROBABLE filter
        # used by export.py and build_data.py.
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(amount=1000.0))
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(amount=2000.0))
        assert _count(db_path, "WHERE status IN ('CONFIRMED','PROBABLE')") == 1


# ─── C1: reclassify raw-coverage guard + raw_coverage_report ─────────────────


class TestReclassifyGuard:
    def test_lost_txns_detects_missing_raw(self, db_path, monkeypatch):
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T1"))
            db.insert_donation(conn, _row(txn="T2"))
        # Only T1 is recoverable from raw → T2 would be lost on reclassify.
        monkeypatch.setattr(
            ingest, "load_raw_payloads", lambda slug: ([{"transaction_id": "T1"}], [])
        )
        live, lost = ingest._reclassify_lost_txns("owner-x", db_path=db_path)
        assert live == {"T1", "T2"}
        assert lost == {"T2"}

    def test_no_lost_when_all_recoverable(self, db_path, monkeypatch):
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T1"))
        monkeypatch.setattr(
            ingest, "load_raw_payloads", lambda slug: ([{"transaction_id": "T1"}], [])
        )
        _, lost = ingest._reclassify_lost_txns("owner-x", db_path=db_path)
        assert lost == set()

    def test_archived_rows_not_counted_as_lost(self, db_path, monkeypatch):
        # A superseded (archived) row must not be treated as an at-risk live row.
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T1", amount=1000.0))
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T1", amount=2000.0))  # supersede
        monkeypatch.setattr(
            ingest, "load_raw_payloads", lambda slug: ([{"transaction_id": "T1"}], [])
        )
        live, lost = ingest._reclassify_lost_txns("owner-x", db_path=db_path)
        assert live == {"T1"}  # only the live row, not the archived one
        assert lost == set()


class TestRawCoverageReport:
    def test_counts_missing_raw_files(self, db_path, tmp_path):
        from scripts import ingest

        present = tmp_path / "present.json"
        present.write_text("{}", encoding="utf-8")
        missing = tmp_path / "missing.json"  # deliberately not created
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T1", raw_payload_path=str(present)))
            db.insert_donation(conn, _row(txn="T2", raw_payload_path=str(missing)))
        rep = ingest.raw_coverage_report(db_path=db_path)
        assert rep["rows_checked"] == 2
        assert rep["rows_missing_raw"] == 1
        assert rep["distinct_missing_files"] == 1
        assert rep["by_slug"]["owner-x"]["missing_raw"] == 1


# ─── H3 backfill: filing_id sentinel (Part B, isolated update logic) ─────────


class TestFilingIdSentinelBackfill:
    def test_apply_sentinel_updates_blank_only_and_is_idempotent(self, db_path):
        from scripts.backfill_pre2006_filing_id import _apply_sentinel
        from scripts.ingest import SENTINEL_FILING_ID

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="B1", filing_id=""))      # blank → sentinel
            db.insert_donation(conn, _row(txn="R1", filing_id="F100"))  # real → untouched
        with db.connect(db_path) as conn:
            assert _apply_sentinel(conn) == 1
        with db.connect(db_path) as conn:
            assert conn.execute(
                "SELECT filing_id FROM donations WHERE transaction_id='B1'"
            ).fetchone()[0] == SENTINEL_FILING_ID
            assert conn.execute(
                "SELECT filing_id FROM donations WHERE transaction_id='R1'"
            ).fetchone()[0] == "F100"
        # Idempotent: a second run finds nothing to change.
        with db.connect(db_path) as conn:
            assert _apply_sentinel(conn) == 0


# ─── v6 review_resolutions: durable verdicts + sticky discard (audit M6) ──────

def test_review_resolution_upsert_query_and_delete(db_path):
    with db.connect(db_path) as conn:
        db.upsert_review_resolution(
            conn, transaction_id="T1", entity_slug="owner-a",
            resolution="DISCARDED", resolution_reason="stranger",
            resolved_at="2026-05-30T00:00:00Z",
        )
        # independent key per (txn, slug)
        db.upsert_review_resolution(
            conn, transaction_id="T1", entity_slug="owner-b",
            resolution="DISCARDED", resolution_reason="other",
            resolved_at="2026-05-30T00:00:00Z",
        )
        assert db.discarded_txns_for_slug(conn, "owner-a") == {"T1"}
        assert db.discarded_txns_for_slug(conn, "owner-b") == {"T1"}
        assert db.discarded_txns_for_slug(conn, "owner-c") == set()
        # upsert overwrites
        db.upsert_review_resolution(
            conn, transaction_id="T1", entity_slug="owner-a",
            resolution="DISCARDED", resolution_reason="v2",
            resolved_at="2026-05-30T01:00:00Z",
        )
        rows = conn.execute(
            "SELECT resolution_reason FROM review_resolutions "
            "WHERE transaction_id='T1' AND entity_slug='owner-a'"
        ).fetchall()
        assert len(rows) == 1 and rows[0]["resolution_reason"] == "v2"
        # delete (undo)
        assert db.delete_review_resolution(conn, transaction_id="T1", entity_slug="owner-a") == 1
        assert db.discarded_txns_for_slug(conn, "owner-a") == set()
        assert db.delete_review_resolution(conn, transaction_id="T1", entity_slug="owner-a") == 0


def test_review_resolution_survives_queue_wipe(db_path):
    """The M6 guarantee: a DISCARDED verdict outlives a review_queue rebuild
    (what reclassify does) and remains available to suppress re-queuing."""
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO review_queue (transaction_id, entity_slug, reason, raw_payload_path, queued_at) "
            "VALUES ('T1','owner-a','name match only','data/raw/owner-a/x.json','2026-05-30T00:00:00Z')"
        )
        db.upsert_review_resolution(
            conn, transaction_id="T1", entity_slug="owner-a",
            resolution="DISCARDED", resolution_reason="stranger",
            resolved_at="2026-05-30T00:00:00Z",
        )
        conn.execute("DELETE FROM review_queue WHERE entity_slug='owner-a'")  # reclassify wipes the projection
        assert db.discarded_txns_for_slug(conn, "owner-a") == {"T1"}  # verdict persists


# ─── v7 manual_attributions: transaction-level override (GOVERNANCE.md §1.1) ──

def test_manual_attribution_upsert_query_and_delete(db_path):
    with db.connect(db_path) as conn:
        db.upsert_manual_attribution(
            conn, transaction_id="T1", entity_slug="owner-a",
            status="CONFIRMED", reason="misfiled suffix", source="zip+employer match",
            attributed_at="2026-05-30T00:00:00Z",
        )
        assert db.manual_attributions_for_slug(conn, "owner-a") == {"T1": "CONFIRMED"}
        assert db.manual_attributions_for_slug(conn, "owner-b") == {}
        # upsert overwrites status
        db.upsert_manual_attribution(
            conn, transaction_id="T1", entity_slug="owner-a",
            status="PROBABLE", reason="v2", source=None,
            attributed_at="2026-05-30T01:00:00Z",
        )
        assert db.manual_attributions_for_slug(conn, "owner-a") == {"T1": "PROBABLE"}
        # delete (undo)
        assert db.delete_manual_attribution(conn, transaction_id="T1", entity_slug="owner-a") == 1
        assert db.manual_attributions_for_slug(conn, "owner-a") == {}
        assert db.delete_manual_attribution(conn, transaction_id="T1", entity_slug="owner-a") == 0


def test_schema_v7_tables_present(db_path):
    with db.connect(db_path) as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"review_resolutions", "manual_attributions"} <= names
    assert db.SCHEMA_VERSION >= 7

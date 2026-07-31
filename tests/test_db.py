"""Supersession + idempotency tests for db.insert_donation (GOVERNANCE.md §1.5, §1.10)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

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

    def test_siblings_sharing_a_txn_id_are_each_tracked(self, db_path, monkeypatch):
        """The guard keys on record_uid, so two live rows sharing one filer id
        are two rows — not one.

        Regression for the fourth layer of the collision class: keyed on
        `transaction_id`, `live` collapsed to a single entry and a sibling whose
        raw had gone missing was invisible, so the guard reported 0 at-risk rows
        while one was about to be silently dropped. Live analogue:
        `johnson-charles`' SA12.4099.0 is ten distinct $3,300 contributions.
        """
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="SHARED", sub_id="S1", recipient_committee_id="C1"))
        with db.connect(db_path) as conn:
            db.insert_donation(
                conn, _row(txn="SHARED", sub_id="S2", recipient_committee_id="C2", amount=999.0)
            )
        assert _count(db_path) == 2, "precondition: v12 stores both siblings"

        # Only S1 survives in raw → S2 would be silently dropped by a reclassify.
        monkeypatch.setattr(
            ingest,
            "load_raw_payloads",
            lambda slug: ([{"transaction_id": "SHARED", "sub_id": "S1"}], []),
        )
        live, lost = ingest._reclassify_lost_txns("owner-x", db_path=db_path)
        assert live == {"S1", "S2"}, "both siblings must be seen as live rows"
        assert lost == {"S2"}, "the sibling with no raw must be reported at risk"


# ─── C1b: reclassify classifier-divergence guard ────────────────────────────


def _stub_classification(status):
    return type("_C", (), {"status": status})()


class TestReclassifyDivergenceGuard:
    """A row present in raw but no longer classifying CONFIRMED/PROBABLE under
    the current YAML would be silently dropped by a reclassify. The divergence
    guard catches these (the raw-coverage guard cannot — the raw is present)."""

    def _patch(self, monkeypatch, raw_txns, verdicts):
        from scripts import ingest

        monkeypatch.setattr(ingest, "_load_owner", lambda slug: {"name_variants": []})
        monkeypatch.setattr(
            ingest,
            "load_raw_payloads",
            lambda slug: ([{"transaction_id": t} for t in raw_txns], []),
        )
        monkeypatch.setattr(
            ingest,
            "classify",
            lambda rec, owner, **kw: verdicts[rec["transaction_id"]],
        )

    def test_demoted_row_flagged_divergent(self, db_path, monkeypatch):
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T1"))
            db.insert_donation(conn, _row(txn="T2"))
        # Both present in raw; T1 re-confirms, T2 now UNCERTAIN → divergent.
        self._patch(
            monkeypatch,
            ["T1", "T2"],
            {
                "T1": _stub_classification("CONFIRMED"),
                "T2": _stub_classification("UNCERTAIN"),
            },
        )
        div = ingest._reclassify_divergent_txns("owner-x", db_path=db_path)
        assert div == {"T2"}

    def test_name_no_match_flagged_divergent(self, db_path, monkeypatch):
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T3"))
        # classify returns None (name-no-match) → would drop entirely.
        self._patch(monkeypatch, ["T3"], {"T3": None})
        assert ingest._reclassify_divergent_txns("owner-x", db_path=db_path) == {"T3"}

    def test_manual_override_excludes_divergent(self, db_path, monkeypatch):
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T2"))
            db.upsert_manual_attribution(
                conn,
                transaction_id="T2",
                entity_slug="owner-x",
                status="CONFIRMED",
                reason="documented human decision",
                source=None,
                attributed_at="2026-01-01T00:00:00Z",
            )
        # Even though classify would demote T2, the override re-forces it.
        self._patch(monkeypatch, ["T2"], {"T2": _stub_classification("UNCERTAIN")})
        assert ingest._reclassify_divergent_txns("owner-x", db_path=db_path) == set()

    def test_excluded_override_not_flagged_divergent(self, db_path, monkeypatch):
        # An EXCLUDED override intentionally drops its txn on reclassify; the
        # guard must NOT flag that as accidental loss (it's in `manual`).
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T2"))
            db.upsert_manual_attribution(
                conn,
                transaction_id="T2",
                entity_slug="owner-x",
                status="EXCLUDED",
                reason="same-named relative, not the owner",
                source=None,
                attributed_at="2026-01-01T00:00:00Z",
            )
        # classify would even CONFIRM T2, but the EXCLUDED override drops it on
        # purpose — not an accidental divergence loss.
        self._patch(monkeypatch, ["T2"], {"T2": _stub_classification("CONFIRMED")})
        assert ingest._reclassify_divergent_txns("owner-x", db_path=db_path) == set()

    def test_raw_missing_not_counted_here(self, db_path, monkeypatch):
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T4"))
        # T4 absent from raw → that's the raw-coverage guard's job, not divergence.
        self._patch(monkeypatch, [], {})
        assert ingest._reclassify_divergent_txns("owner-x", db_path=db_path) == set()

    def test_no_yaml_returns_empty(self, db_path, monkeypatch):
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T5"))

        def _raise(slug):
            raise FileNotFoundError(slug)

        monkeypatch.setattr(ingest, "_load_owner", _raise)
        monkeypatch.setattr(ingest, "load_raw_payloads", lambda slug: ([], []))
        # Can't assess divergence without a YAML → empty (raw guard still applies).
        assert ingest._reclassify_divergent_txns("owner-x", db_path=db_path) == set()

    def test_each_sibling_sharing_a_txn_id_is_classified(self, db_path, monkeypatch):
        """Every sibling is re-classified on its own record, not just one.

        Regression for the fourth layer of the collision class: the raw map was
        keyed on `transaction_id`, so of N siblings only the last-read survived
        the dict and the other N-1 were never scored. A demoted sibling was
        therefore invisible and would vanish on reclassify without the guard
        firing.
        """
        from scripts import ingest

        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="SHARED", sub_id="S1", recipient_committee_id="C1"))
        with db.connect(db_path) as conn:
            db.insert_donation(
                conn, _row(txn="SHARED", sub_id="S2", recipient_committee_id="C2", amount=999.0)
            )
        assert _count(db_path) == 2, "precondition: v12 stores both siblings"

        raw = [
            {"transaction_id": "SHARED", "sub_id": "S1"},
            {"transaction_id": "SHARED", "sub_id": "S2"},
        ]
        verdicts = {
            "S1": _stub_classification("CONFIRMED"),
            "S2": _stub_classification("UNCERTAIN"),  # demoted → must be caught
        }
        monkeypatch.setattr(ingest, "_load_owner", lambda slug: {"name_variants": []})
        monkeypatch.setattr(ingest, "load_raw_payloads", lambda slug: (raw, []))
        monkeypatch.setattr(
            ingest, "classify", lambda rec, owner, **kw: verdicts[rec["sub_id"]]
        )

        div = ingest._reclassify_divergent_txns("owner-x", db_path=db_path)
        assert div == {"S2"}, "the demoted sibling must be flagged, not masked by S1"


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


def test_manual_attribution_excluded_status_roundtrip(db_path):
    # The EXCLUDED override (negative of CONFIRMED) stores/queries/deletes the
    # same way — no schema change, status is free-text.
    with db.connect(db_path) as conn:
        db.upsert_manual_attribution(
            conn, transaction_id="T9", entity_slug="owner-a",
            status="EXCLUDED", reason="same-named son, not the owner", source="middle initial P",
            attributed_at="2026-05-30T00:00:00Z",
        )
        assert db.manual_attributions_for_slug(conn, "owner-a") == {"T9": "EXCLUDED"}
        assert db.delete_manual_attribution(conn, transaction_id="T9", entity_slug="owner-a") == 1
        assert db.manual_attributions_for_slug(conn, "owner-a") == {}


def test_schema_v7_tables_present(db_path):
    with db.connect(db_path) as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"review_resolutions", "manual_attributions"} <= names
    assert db.SCHEMA_VERSION >= 7


class TestSubIdCollision:
    """v9 (§1.3): sub_id distinguishes a genuine restatement from a
    cross-committee transaction_id collision."""

    def test_same_txn_different_subid_now_stores_BOTH(self, db_path):
        """v12: two real contributions sharing a filer id are BOTH kept.

        This inverts the v9/v11 expectation, which was 'collision' — a loud
        refusal that protected the stored row but still lost the incoming one.
        Refusing was the best available answer while the PK was
        (transaction_id, entity_slug); it could not represent two rows sharing
        an id within one owner. v12 keys on record_uid (= sub_id when present),
        so both are simply stored, which is what FEC actually published.

        Observed live 2026-07-20: dewitt-bill SA18.1160868 was $300 to John
        McCain 2008 AND $2,300 to the McCain-Palin Compliance Fund on the same
        day; reinsdorf-jerry SA17A.939857 likewise.
        """
        with db.connect(db_path) as conn:
            assert db.insert_donation(conn, _row(sub_id="S1", recipient_committee_id="C1"))[0] == "inserted"
        with db.connect(db_path) as conn:
            action, _reason = db.insert_donation(
                conn, _row(sub_id="S2", recipient_committee_id="C2", amount=999.0))
            assert action == "inserted"
        # BOTH survive, under one transaction_id but distinct record_uids.
        assert _count(db_path) == 2
        with db.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT sub_id, record_uid, amount FROM donations ORDER BY sub_id"
            ).fetchall()
            assert [r["sub_id"] for r in rows] == ["S1", "S2"]
            assert [r["record_uid"] for r in rows] == ["S1", "S2"]
            assert [r["amount"] for r in rows] == [1000.0, 999.0]
            # Neither was superseded — nothing was archived or overwritten.
            assert all(r["record_uid"] is not None for r in rows)

    def test_record_uid_falls_back_to_txn_when_no_sub_id(self, db_path):
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id=None))
            r = conn.execute("SELECT record_uid, transaction_id FROM donations").fetchone()
            assert r["record_uid"] == r["transaction_id"]

    def test_legacy_row_is_adopted_when_sub_id_arrives(self, db_path):
        """The v12 migration bridge. 58% of the archive predates sub_id capture,
        so those rows have record_uid = transaction_id. The same contribution
        re-ingested WITH a sub_id must UPDATE that row, not duplicate it."""
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id=None, amount=1000.0))
        with db.connect(db_path) as conn:
            action, _ = db.insert_donation(conn, _row(sub_id="S9", amount=1000.0))
            assert action == "unchanged"          # adopted, same substance
        assert _count(db_path) == 1               # NOT duplicated
        with db.connect(db_path) as conn:
            r = conn.execute("SELECT sub_id, record_uid FROM donations").fetchone()
            assert r["sub_id"] == "S9"            # upgraded in place
            assert r["record_uid"] == "S9"

    def test_legacy_bridge_does_not_adopt_a_different_contribution(self, db_path):
        """The bridge must not swallow a genuine sibling. A row sharing the
        transaction_id but differing in substance is a distinct contribution and
        must insert alongside, not overwrite the legacy row."""
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id=None, amount=1000.0, recipient_committee_id="C1"))
        with db.connect(db_path) as conn:
            action, _ = db.insert_donation(
                conn, _row(sub_id="S9", amount=777.0, recipient_committee_id="C2"))
            assert action == "inserted"
        assert _count(db_path) == 2

    def test_same_txn_same_subid_still_supersedes_on_restatement(self, db_path):
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id="S1", amount=1000.0))
        with db.connect(db_path) as conn:
            action, _ = db.insert_donation(conn, _row(sub_id="S1", amount=2500.0))
            assert action == "superseded"  # same record, FEC restated the amount

    def test_missing_subid_falls_back_to_txn_identity(self, db_path):
        # Legacy rows with NULL sub_id keep the prior transaction_id behavior.
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id=None, amount=1000.0))
        with db.connect(db_path) as conn:
            action, _ = db.insert_donation(conn, _row(sub_id=None, amount=1000.0))
            assert action == "unchanged"


class TestCollisionWithoutSubId:
    """§1.3 — the sub_id test can only fire when BOTH rows carry one, and sub_id
    is sparsely populated, so it was inert for most of the archive. Real
    collisions fell through to the supersede path and were recorded as
    fabricated "FEC restatements" of one owner's donation into another's — two
    were found live on 2026-07-19. These cases carry NO sub_id on either side.
    """

    def test_two_owners_may_share_a_transaction_id(self, db_path):
        """v11: the composite PK makes the cross-owner case a non-event.

        This is the live bug — kendrick-ken $2,800 was "restated" into a
        reinsdorf-jerry row because both filings reused one transaction_id.
        Under the v11 PK (transaction_id, entity_slug) both contributions are
        simply stored; neither is refused and neither is superseded.
        """
        with db.connect(db_path) as conn:
            a, _ = db.insert_donation(conn, _row(
                sub_id=None, entity_slug="kendrick-ken",
                contributor_name_raw="KENDRICK, EARL", amount=2800.0))
        with db.connect(db_path) as conn:
            b, _ = db.insert_donation(conn, _row(
                sub_id=None, entity_slug="reinsdorf-jerry",
                contributor_name_raw="REINSDORF, JERRY M.", amount=8400.0))
        assert (a, b) == ("inserted", "inserted")
        assert _count(db_path) == 2
        with db.connect(db_path) as conn:
            rows = {r["entity_slug"]: dict(r) for r in conn.execute(
                "SELECT entity_slug, amount, status, superseded_by FROM donations")}
        assert rows["kendrick-ken"]["amount"] == 2800.0
        assert rows["reinsdorf-jerry"]["amount"] == 8400.0
        assert all(r["status"] == "CONFIRMED" and r["superseded_by"] is None
                   for r in rows.values())

    def test_each_owners_own_row_still_supersedes_independently(self, db_path):
        """Two owners on one id must not interfere when one IS restated."""
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id=None, entity_slug="owner-a", amount=1000.0))
            db.insert_donation(conn, _row(sub_id=None, entity_slug="owner-b", amount=7000.0))
        with db.connect(db_path) as conn:
            action, _ = db.insert_donation(conn, _row(
                sub_id=None, entity_slug="owner-a", amount=1500.0))
        assert action == "superseded"
        with db.connect(db_path) as conn:
            # owner-b is untouched by owner-a's supersession.
            b = conn.execute(
                "SELECT amount, status FROM donations WHERE entity_slug='owner-b'").fetchone()
            assert (b["amount"], b["status"]) == (7000.0, "CONFIRMED")

    def test_same_owner_wholly_different_contribution_is_a_collision(self, db_path):
        # One owner, one reused transaction_id, but committee AND date AND
        # amount all differ — that is two contributions, not one restated.
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(
                sub_id=None, recipient_committee_id="C001",
                date="2016-12-11", amount=2500.0))
        with db.connect(db_path) as conn:
            action, reason = db.insert_donation(conn, _row(
                sub_id=None, recipient_committee_id="C999",
                date="2019-09-20", amount=20000.0))
        assert action == "collision"
        assert "distinct contributions" in reason
        assert _count(db_path) == 1

    def test_ordinary_restatement_still_supersedes(self, db_path):
        # The guard must not swallow genuine restatements: same committee, same
        # date, corrected amount — one field, so it stays on the supersede path.
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id=None, amount=1000.0))
        with db.connect(db_path) as conn:
            action, _ = db.insert_donation(conn, _row(sub_id=None, amount=1500.0))
        assert action == "superseded"

    def test_reimage_of_same_contribution_still_supersedes(self, db_path):
        # Two of three identity fields differ (date + amount) but the recipient
        # is unchanged — still a restatement, not a collision.
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id=None, date="2024-01-15", amount=1000.0))
        with db.connect(db_path) as conn:
            action, _ = db.insert_donation(conn, _row(sub_id=None, date="2024-01-16", amount=1200.0))
        assert action == "superseded"


class TestReviewQueueCompositePK:
    """v9 (§1.3): review_queue PK is (transaction_id, entity_slug) — two owners
    can flag the same FEC transaction without one silently dropping the other."""

    def test_two_owners_same_txn_both_kept(self, db_path):
        with db.connect(db_path) as conn:
            db.insert_review_queue(conn, {
                "transaction_id": "T1", "entity_slug": "owner-a", "reason": "name only",
                "raw_payload_path": "p", "queued_at": "2026-07-10T00:00:00Z"})
            db.insert_review_queue(conn, {
                "transaction_id": "T1", "entity_slug": "owner-b", "reason": "name only",
                "raw_payload_path": "p", "queued_at": "2026-07-10T00:00:00Z"})
            n = conn.execute("SELECT COUNT(*) FROM review_queue WHERE transaction_id='T1'").fetchone()[0]
        assert n == 2  # both preserved (was 1 under the single-column PK)

    def test_pk_migration_from_legacy_single_pk(self, tmp_path):
        # Build a DB with the OLD single-column review_queue PK, populate it, then
        # init() and confirm it's migrated to the composite PK with data preserved.
        import sqlite3
        p = tmp_path / "legacy.db"
        conn = sqlite3.connect(p)
        conn.executescript("""
            CREATE TABLE review_queue (
                transaction_id TEXT PRIMARY KEY, entity_slug TEXT NOT NULL,
                reason TEXT NOT NULL, raw_payload_path TEXT NOT NULL, queued_at TEXT NOT NULL,
                resolution TEXT, resolution_reason TEXT, resolution_at TEXT, resolved_by TEXT);
            INSERT INTO review_queue VALUES ('T1','owner-a','r','p','t',NULL,NULL,NULL,NULL);
        """)
        conn.commit(); conn.close()
        db.init(p)
        with db.connect(p) as conn:
            pk = {r["name"] for r in conn.execute("PRAGMA table_info(review_queue)") if r["pk"]}
            row = conn.execute("SELECT transaction_id, entity_slug FROM review_queue").fetchone()
        assert pk == {"transaction_id", "entity_slug"}
        assert row["transaction_id"] == "T1" and row["entity_slug"] == "owner-a"  # data preserved


def test_connect_sets_busy_timeout(db_path):
    # §4.5: a 30s busy_timeout so a competing writer waits instead of failing
    # instantly with "database is locked".
    with db.connect(db_path) as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
# ─── refresh_entities: household (related-entity) mirror — Phase A ───────────

_OWNER_WITH_SPOUSE_YAML = """\
slug: owner-h
name: Owner H
team: Test Team
role: Principal owner
status: pilot
tenure_start_date: 2020-01-01
tenure_end_date: null
name_variants: ["Owner H", "O. H."]
verifying_signals:
  cities: ["townsville"]
  states: ["TT"]
  employers: ["Owner H LLC"]
  occupations: ["investor"]
strong_signals: {employers: [], zip_codes: []}
related_entities:
  - kind: spouse
    slug: spouse-h
    name: Spouse H
    name_variants: ["Spouse H"]
    verifying_signals: {cities: ["townsville"], states: ["TT"], employers: [], occupations: []}
    strong_signals: {employers: [], zip_codes: []}
  - kind: child
    slug: child-h
    name: Child H
    name_variants: ["Child H"]
    verifying_signals: {cities: ["townsville"], states: ["TT"], employers: [], occupations: []}
    strong_signals: {employers: [], zip_codes: []}
sources:
  - description: "ownership page"
    url: ""
    accessed: "2026-06-02"
"""

_PLAIN_OWNER_YAML = """\
slug: owner-p
name: Owner P
team: Plain Team
role: Principal owner
status: pilot
tenure_start_date: 2019-01-01
tenure_end_date: null
name_variants: ["Owner P"]
verifying_signals:
  cities: ["plainville"]
  states: ["PP"]
  employers: ["Owner P Inc"]
  occupations: ["owner"]
strong_signals: {employers: [], zip_codes: []}
sources:
  - description: "ownership page"
    url: ""
    accessed: "2026-06-02"
"""


@pytest.fixture
def owners_dir(tmp_path, monkeypatch):
    d = tmp_path / "owners"
    d.mkdir()
    (d / "owner-h.yaml").write_text(_OWNER_WITH_SPOUSE_YAML, encoding="utf-8")
    (d / "owner-p.yaml").write_text(_PLAIN_OWNER_YAML, encoding="utf-8")
    (d / "_registry.yaml").write_text("owners: []\n", encoding="utf-8")  # underscore-skipped
    monkeypatch.setattr(db, "OWNERS_DIR", d)
    # relpath() is repo-root-relative; tmp paths live outside the repo, so stub
    # it (same pattern as the fetch/ingest tests).
    monkeypatch.setattr(db, "relpath", lambda p: Path(p).name)
    return d


class TestRefreshEntitiesHouseholds:
    def _rows(self, db_path):
        with db.connect(db_path) as conn:
            return {
                r["slug"]: dict(r)
                for r in conn.execute("SELECT * FROM entities")
            }

    def test_writes_owner_and_related_rows(self, db_path, owners_dir):
        n = db.refresh_entities(db_path)
        rows = self._rows(db_path)
        # 2 owners + 1 spouse + 1 child = 4 (registry underscore file skipped).
        assert n == 4
        assert set(rows) == {"owner-h", "spouse-h", "child-h", "owner-p"}

    def test_related_rows_carry_kind_and_parent(self, db_path, owners_dir):
        db.refresh_entities(db_path)
        rows = self._rows(db_path)
        assert rows["spouse-h"]["kind"] == "spouse"
        assert rows["spouse-h"]["parent_slug"] == "owner-h"
        assert rows["child-h"]["kind"] == "child"
        assert rows["child-h"]["parent_slug"] == "owner-h"
        assert rows["spouse-h"]["name"] == "Spouse H"

    def test_owner_rows_have_no_parent(self, db_path, owners_dir):
        db.refresh_entities(db_path)
        rows = self._rows(db_path)
        assert rows["owner-h"]["kind"] == "owner"
        assert rows["owner-h"]["parent_slug"] is None
        assert rows["owner-h"]["team"] == "Test Team"

    def test_related_rows_share_owner_yaml_provenance(self, db_path, owners_dir):
        db.refresh_entities(db_path)
        rows = self._rows(db_path)
        # Related entity lives in the owner's file → same yaml_path + hash.
        assert rows["spouse-h"]["yaml_path"] == rows["owner-h"]["yaml_path"]
        assert rows["spouse-h"]["yaml_sha256"] == rows["owner-h"]["yaml_sha256"]

    def test_idempotent(self, db_path, owners_dir):
        db.refresh_entities(db_path)
        n2 = db.refresh_entities(db_path)
        rows = self._rows(db_path)
        assert n2 == 4
        assert len(rows) == 4  # DELETE-then-reinsert, no duplication

    def test_plain_owner_emits_single_row(self, db_path, owners_dir):
        db.refresh_entities(db_path)
        rows = self._rows(db_path)
        assert rows["owner-p"]["kind"] == "owner"
        assert "spouse" not in {rows[s]["kind"] for s in rows if rows[s]["parent_slug"] == "owner-p"}


class TestRepairTxnCollisions:
    """The one-off repair for the two wrong supersessions the §1.3 gap caused."""

    def _seed_wrong_supersession(self, db_path):
        """Reproduce the exact live state: kendrick-ken's real $2,800 donation
        archived as SUPERSEDED under a fabricated 'FEC restatement' reason,
        while a reinsdorf-jerry row holds the canonical transaction_id."""
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(
                txn="SA11AI.4319~superseded~2026-05-30T22-53-46Z",
                entity_slug="kendrick-ken",
                contributor_name_raw="KENDRICK, EARL",
                recipient_committee_id="C1",
                recipient_committee_name="GOP WINNING WOMEN",
                amount=2800.0, date="2020-05-30",
                status="SUPERSEDED",
                status_reason="two confirming signals: employer:Diamondbacks",
            ))
            conn.execute(
                "UPDATE donations SET superseded_by='SA11AI.4319',"
                " superseded_reason='FEC restatement: amount, date'"
                " WHERE entity_slug='kendrick-ken'"
            )
            # The colliding row that legitimately holds the canonical id.
            db.insert_donation(conn, _row(
                txn="SA11AI.4319",
                entity_slug="reinsdorf-jerry",
                contributor_name_raw="REINSDORF, JERRY M.",
                recipient_committee_id="C2",
                recipient_committee_name="OTHER",
                amount=8400.0, date="2019-01-01",
            ))

    def test_restores_status_and_rekeys_without_touching_the_collider(self, db_path):
        from scripts import repair_txn_collisions as rtc

        self._seed_wrong_supersession(db_path)
        out = rtc.repair(db_path=db_path)
        assert len(out) == 1 and out[0]["restored_status"] == "CONFIRMED"

        with db.connect(db_path) as conn:
            rows = {r["transaction_id"]: dict(r) for r in conn.execute(
                "SELECT transaction_id, entity_slug, status, superseded_by, amount FROM donations")}
        # Displaced row is live again under an honest key...
        fixed = rows["SA11AI.4319~collision~kendrick-ken"]
        assert fixed["status"] == "CONFIRMED"
        assert fixed["superseded_by"] is None
        assert fixed["amount"] == 2800.0
        # ...and the row that legitimately holds the canonical id is untouched.
        assert rows["SA11AI.4319"]["entity_slug"] == "reinsdorf-jerry"
        assert rows["SA11AI.4319"]["amount"] == 8400.0

    def test_is_idempotent(self, db_path):
        from scripts import repair_txn_collisions as rtc

        self._seed_wrong_supersession(db_path)
        assert len(rtc.repair(db_path=db_path)) == 1
        assert rtc.repair(db_path=db_path) == []   # nothing left to repair

    def test_dry_run_writes_nothing(self, db_path):
        from scripts import repair_txn_collisions as rtc

        self._seed_wrong_supersession(db_path)
        assert len(rtc.repair(db_path=db_path, dry_run=True)) == 1
        with db.connect(db_path) as conn:
            still = conn.execute(
                "SELECT status FROM donations WHERE superseded_by = 'SA11AI.4319'").fetchone()
        assert still["status"] == "SUPERSEDED"


class TestDonationsPkMigration:
    """v11: rebuild donations with PK (transaction_id, entity_slug)."""

    def _legacy_db(self, tmp_path):
        """A DB with the pre-v11 single-column PK, as a real archive has."""
        p = tmp_path / "legacy.db"
        conn = sqlite3.connect(p)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE donations (
                transaction_id TEXT PRIMARY KEY,
                entity_slug TEXT NOT NULL,
                entity_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                contributor_name_raw TEXT NOT NULL,
                recipient_committee_id TEXT NOT NULL,
                recipient_committee_name TEXT NOT NULL,
                recipient_candidate_id TEXT,
                amount REAL NOT NULL,
                date TEXT NOT NULL,
                election_cycle INTEGER,
                filing_id TEXT NOT NULL,
                raw_payload_path TEXT NOT NULL,
                ingested_at TEXT NOT NULL
            );
            INSERT INTO donations VALUES
              ('T1','owner-a','owner','CONFIRMED','A','C1','Cmte','',100.0,'2020-01-01',2020,'F','r','t'),
              ('T2','owner-b','owner','PROBABLE','B','C2','Cmte','',200.0,'2020-01-02',2020,'F','r','t');
            """
        )
        conn.commit()
        return p, conn

    def test_rebuild_preserves_every_row_and_sets_composite_pk(self, tmp_path):
        p, conn = self._legacy_db(tmp_path)
        assert [r["name"] for r in conn.execute("PRAGMA table_info(donations)") if r["pk"]] == ["transaction_id"]

        db._migrate_donations_pk(conn)

        pk = {r["name"] for r in conn.execute("PRAGMA table_info(donations)") if r["pk"]}
        assert pk == {"transaction_id", "entity_slug"}
        rows = {(r["transaction_id"], r["entity_slug"]): r["amount"]
                for r in conn.execute("SELECT transaction_id, entity_slug, amount FROM donations")}
        assert rows == {("T1", "owner-a"): 100.0, ("T2", "owner-b"): 200.0}

    def test_preserves_columns_added_by_later_alters(self, tmp_path):
        # donations grows columns via ALTER in init(); a hard-coded CREATE in the
        # migration would silently drop them.
        p, conn = self._legacy_db(tmp_path)
        conn.execute("ALTER TABLE donations ADD COLUMN sub_id TEXT")
        conn.execute("UPDATE donations SET sub_id = 'S9' WHERE transaction_id = 'T1'")

        db._migrate_donations_pk(conn)

        cols = {r["name"] for r in conn.execute("PRAGMA table_info(donations)")}
        assert "sub_id" in cols
        assert conn.execute(
            "SELECT sub_id FROM donations WHERE transaction_id='T1'").fetchone()["sub_id"] == "S9"

    def test_recreates_indexes_the_drop_removed(self, tmp_path):
        p, conn = self._legacy_db(tmp_path)
        conn.execute("CREATE INDEX idx_donations_status ON donations(status)")
        db._migrate_donations_pk(conn)
        idx = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='donations'"
            " AND name NOT LIKE 'sqlite_%'")}
        assert "idx_donations_status" in idx
        assert "idx_donations_entity_date" in idx

    def test_is_idempotent(self, tmp_path):
        p, conn = self._legacy_db(tmp_path)
        db._migrate_donations_pk(conn)
        db._migrate_donations_pk(conn)   # second call is a no-op
        assert conn.execute("SELECT COUNT(*) c FROM donations").fetchone()["c"] == 2

    def test_two_owners_sharing_an_id_survive_the_rebuild(self, tmp_path):
        """The whole point: post-migration the table can hold what the old PK could not."""
        p, conn = self._legacy_db(tmp_path)
        db._migrate_donations_pk(conn)
        conn.execute(
            "INSERT INTO donations (transaction_id, entity_slug, entity_kind, status,"
            " contributor_name_raw, recipient_committee_id, recipient_committee_name,"
            " recipient_candidate_id, amount, date, election_cycle, filing_id, raw_payload_path, ingested_at)"
            " VALUES ('T1','owner-z','owner','CONFIRMED','Z','C9','Other','',900.0,'2021-01-01',2020,'F','r','t')"
        )
        assert conn.execute(
            "SELECT COUNT(*) c FROM donations WHERE transaction_id='T1'").fetchone()["c"] == 2


class TestRecordUidIntegrityCheck:
    """v12: `cli validate` asserts the identity guarantee that the DDL cannot
    (record_uid is nullable so raw-SQL fixtures need not carry it)."""

    def test_clean_db_reports_no_errors(self, db_path):
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id="S1"))
        assert db.check_record_uid_integrity(db_path) == []

    def test_missing_record_uid_is_reported(self, db_path):
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id="S1"))
            conn.execute("UPDATE donations SET record_uid = NULL")
        errs = db.check_record_uid_integrity(db_path)
        assert errs and "no record_uid" in errs[0]

    def test_uid_disagreeing_with_its_own_ids_is_reported(self, db_path):
        """Catches a writer that bypassed db.record_uid_for()."""
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(sub_id="S1"))
            conn.execute("UPDATE donations SET record_uid = 'something-else'")
        errs = db.check_record_uid_integrity(db_path)
        assert any("bypassed" in e for e in errs)

    def test_lfs_pointer_is_not_an_integrity_failure(self, tmp_path):
        """In CI master.db is checked out as a Git LFS pointer, not a database.
        sqlite3.connect() is lazy, so this surfaces on the first query as
        'file is not a database'. That is an absent DB, not a failure — without
        this branch `cli validate` crashed in CI while passing locally."""
        pointer = tmp_path / "master.db"
        pointer.write_text(
            "version https://git-lfs.github.com/spec/v1\n"
            "oid sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
            "size 123\n"
        )
        assert db.check_record_uid_integrity(pointer) == []

    def test_absent_db_is_not_an_integrity_failure(self, tmp_path):
        assert db.check_record_uid_integrity(tmp_path / "nope.db") == []


class TestAdjudicationIntegrity:
    """Human adjudication must be durable, and must not act beyond its record."""

    def test_clean_db_has_no_warnings(self, db_path):
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T1"))
        assert db.check_adjudication_integrity(db_path) == []

    def test_orphaned_queue_verdict_is_flagged(self, db_path):
        """A verdict on the queue row with no review_resolutions row is not a
        weaker record of the decision — reclassify rebuilds review_queue and
        suppresses only from review_resolutions, so it reverts to open."""
        with db.connect(db_path) as conn:
            db.insert_review_queue(
                conn,
                {
                    "transaction_id": "Q1",
                    "entity_slug": "owner-x",
                    "reason": "city/state outside documented residences",
                    "raw_payload_path": "",
                    "queued_at": "2026-01-01T00:00:00Z",
                },
            )
            conn.execute("UPDATE review_queue SET resolution = 'DISCARDED'")
        warns = db.check_adjudication_integrity(db_path)
        assert any("NOT durable" in w for w in warns)

    def test_durable_verdict_is_not_flagged(self, db_path):
        with db.connect(db_path) as conn:
            db.insert_review_queue(
                conn,
                {
                    "transaction_id": "Q1",
                    "entity_slug": "owner-x",
                    "reason": "city/state outside documented residences",
                    "raw_payload_path": "",
                    "queued_at": "2026-01-01T00:00:00Z",
                },
            )
            conn.execute("UPDATE review_queue SET resolution = 'DISCARDED'")
            db.upsert_review_resolution(
                conn,
                transaction_id="Q1",
                entity_slug="owner-x",
                resolution="DISCARDED",
                resolution_reason="same-name stranger",
                resolved_at="2026-01-01T00:00:00Z",
            )
        assert db.check_adjudication_integrity(db_path) == []

    def test_override_hitting_two_siblings_is_flagged(self, db_path):
        """Since v12 one owner can hold several live rows sharing a
        transaction_id, so a manual_attributions override applies to every
        sibling at once. Live analogue: middleton-john/SA18.1294499 → 2 rows."""
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="SHARED", sub_id="S1", recipient_committee_id="C1"))
        with db.connect(db_path) as conn:
            db.insert_donation(
                conn, _row(txn="SHARED", sub_id="S2", recipient_committee_id="C2", amount=999.0)
            )
            db.upsert_manual_attribution(
                conn,
                transaction_id="SHARED",
                entity_slug="owner-x",
                status="EXCLUDED",
                reason="same-named relative",
                source=None,
                attributed_at="2026-01-01T00:00:00Z",
            )
        warns = db.check_adjudication_integrity(db_path)
        assert any("more than one" in w for w in warns)

    def test_override_hitting_one_row_is_not_flagged(self, db_path):
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T1", sub_id="S1"))
            db.upsert_manual_attribution(
                conn,
                transaction_id="T1",
                entity_slug="owner-x",
                status="CONFIRMED",
                reason="documented human decision",
                source=None,
                attributed_at="2026-01-01T00:00:00Z",
            )
        assert db.check_adjudication_integrity(db_path) == []

    def test_superseded_sibling_does_not_widen_blast_radius(self, db_path):
        """Only LIVE rows count — an archived row is not something an override
        can still act on."""
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T1", sub_id="S1", amount=1000.0))
        with db.connect(db_path) as conn:
            db.insert_donation(conn, _row(txn="T1", sub_id="S1", amount=2000.0))  # supersedes
            db.upsert_manual_attribution(
                conn,
                transaction_id="T1",
                entity_slug="owner-x",
                status="CONFIRMED",
                reason="documented human decision",
                source=None,
                attributed_at="2026-01-01T00:00:00Z",
            )
        assert db.check_adjudication_integrity(db_path) == []

    def test_lfs_pointer_and_absent_db_are_not_failures(self, tmp_path):
        pointer = tmp_path / "master.db"
        pointer.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:0\nsize 1\n")
        assert db.check_adjudication_integrity(pointer) == []
        assert db.check_adjudication_integrity(tmp_path / "nope.db") == []

"""Tests for scripts/queue_stats.py — the review-queue burndown.

Read-only aggregation across master.db (federal) and state.db (state). These
tests seed temp DBs through the real db / state_db write helpers, then assert
the structured stats (not the cosmetic formatted output) so they stay
deterministic.
"""
from __future__ import annotations

import pytest

from scripts import db, queue_stats, state_db


# ─── Seed helpers ────────────────────────────────────────────────────────────


def _donation(txn: str, slug: str, status: str, **overrides) -> dict:
    base = {
        "transaction_id": txn,
        "entity_slug": slug,
        "entity_kind": "owner",
        "parent_owner_slug": None,
        "status": status,
        "status_reason": "seed",
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
        "raw_payload_path": f"data/raw/{slug}/{txn}.json",
        "ingested_at": "2026-05-28T00:00:00Z",
    }
    base.update(overrides)
    return base


def _queue(conn, txn: str, slug: str, reason: str, resolution: str | None = None) -> None:
    db.insert_review_queue(
        conn,
        {
            "transaction_id": txn,
            "entity_slug": slug,
            "reason": reason,
            "raw_payload_path": f"data/raw/{slug}/{txn}.json",
            "queued_at": "2026-05-28T00:00:00Z",
        },
    )
    if resolution is not None:
        conn.execute(
            "UPDATE review_queue SET resolution = ?, resolution_at = ? "
            "WHERE transaction_id = ? AND entity_slug = ?",
            (resolution, "2026-05-29T00:00:00Z", txn, slug),
        )


@pytest.fixture
def master_db(tmp_path):
    p = tmp_path / "master.db"
    db.init(p)
    with db.connect(p) as conn:
        # owner-a: 4 CONFIRMED, 2 PROBABLE (P/C = 0.5), 3 open + 1 resolved queue
        for i in range(4):
            db.insert_donation(conn, _donation(f"A-C{i}", "owner-a", "CONFIRMED"))
        for i in range(2):
            db.insert_donation(conn, _donation(f"A-P{i}", "owner-a", "PROBABLE"))
        _queue(conn, "A-Q0", "owner-a", "name match only", None)
        _queue(conn, "A-Q1", "owner-a", "name match only", None)
        _queue(conn, "A-Q2", "owner-a", "city/state alone", None)
        _queue(conn, "A-Q3", "owner-a", "name match only", "DISCARDED")

        # owner-b: 1 CONFIRMED, 3 PROBABLE (P/C = 3.0 — loose), no open queue
        db.insert_donation(conn, _donation("B-C0", "owner-b", "CONFIRMED"))
        for i in range(3):
            db.insert_donation(conn, _donation(f"B-P{i}", "owner-b", "PROBABLE"))
        _queue(conn, "B-Q0", "owner-b", "name match only", "DISCARDED")

        # owner-c: queue-only (no donations), 5 open
        for i in range(5):
            _queue(conn, f"C-Q{i}", "owner-c", "name match only", None)

        # name lookup + last-ingestion age
        conn.execute(
            "INSERT INTO entities (slug, kind, name, team, yaml_path, yaml_sha256, refreshed_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("owner-a", "owner", "Owner Alpha", "Team A",
             "owners/owner-a.yaml", "0" * 64, "2026-05-28T00:00:00Z"),
        )
        db.insert_ingestion_run(
            conn,
            {
                "run_id": "r1", "entity_slug": "owner-a",
                "started_at": "2026-05-01T00:00:00Z",
                "completed_at": "2026-05-02T00:00:00Z",
                "period_start": None, "period_end": None,
                "name_variants_queried": 1, "api_calls_made": 1,
                "records_fetched": 6, "confirmed_count": 4,
                "probable_count": 2, "uncertain_count": 4,
                "snapshot_path": None, "notes": None, "dry_run": 0,
            },
        )
        conn.commit()
    return p


@pytest.fixture
def state_db_path(tmp_path):
    p = tmp_path / "state.db"
    state_db.init(p)
    with state_db.connect(p) as conn:
        def sq(txn, slug, juris, reason, resolution=None):
            state_db.insert_state_review_queue(
                conn,
                {
                    "state_txn_id": txn, "entity_slug": slug,
                    "jurisdiction": juris, "source": "TEST",
                    "reason": reason,
                    "raw_payload_path": f"data/raw/state/{juris}/{txn}.json",
                    "queued_at": "2026-05-28T00:00:00Z",
                },
            )
            if resolution is not None:
                conn.execute(
                    "UPDATE state_review_queue SET resolution = ? "
                    "WHERE state_txn_id = ? AND entity_slug = ?",
                    (resolution, txn, slug),
                )

        # TX: 3 open; CA: 1 open + 1 resolved
        sq("TX1", "owner-a", "TX", "name match only")
        sq("TX2", "owner-a", "TX", "name match only")
        sq("TX3", "owner-b", "TX", "zip only")
        sq("CA1", "owner-a", "CA", "name match only")
        sq("CA2", "owner-b", "CA", "name match only", "DISCARDED")
        conn.commit()
    return p


# ─── Federal ─────────────────────────────────────────────────────────────────


class TestFederal:
    def test_totals_and_pct(self, master_db):
        stats = queue_stats.build_queue_stats(db_path=master_db, state_db_path=master_db.with_name("absent.db"))
        f = stats.federal
        # open: a=3, c=5 → 8 ; resolved: a=1, b=1 → 2
        assert f.total_open == 8
        assert f.total_resolved == 2
        assert f.pct_adjudicated == pytest.approx(2 / 10)

    def test_per_owner_counts_and_ratio(self, master_db):
        f = queue_stats.build_queue_stats(db_path=master_db, state_db_path=master_db.with_name("absent.db")).federal
        by_slug = {o.slug: o for o in f.owners}
        assert by_slug["owner-a"].confirmed == 4
        assert by_slug["owner-a"].probable == 2
        assert by_slug["owner-a"].pc_ratio == pytest.approx(0.5)
        assert by_slug["owner-a"].open_queue == 3
        assert by_slug["owner-a"].resolved_queue == 1
        assert by_slug["owner-b"].pc_ratio == pytest.approx(3.0)
        # queue-only owner has no donations and an undefined ratio
        assert by_slug["owner-c"].confirmed == 0
        assert by_slug["owner-c"].pc_ratio is None
        assert by_slug["owner-c"].open_queue == 5

    def test_sort_is_open_queue_desc(self, master_db):
        f = queue_stats.build_queue_stats(db_path=master_db, state_db_path=master_db.with_name("absent.db")).federal
        # owner-c (5 open) before owner-a (3 open) before owner-b (0 open)
        order = [o.slug for o in f.owners]
        assert order.index("owner-c") < order.index("owner-a") < order.index("owner-b")

    def test_name_falls_back_to_slug(self, master_db):
        f = queue_stats.build_queue_stats(db_path=master_db, state_db_path=master_db.with_name("absent.db")).federal
        by_slug = {o.slug: o for o in f.owners}
        assert by_slug["owner-a"].name == "Owner Alpha"  # from entities
        assert by_slug["owner-b"].name == "owner-b"       # fallback

    def test_last_ingestion_surfaced(self, master_db):
        f = queue_stats.build_queue_stats(db_path=master_db, state_db_path=master_db.with_name("absent.db")).federal
        by_slug = {o.slug: o for o in f.owners}
        assert by_slug["owner-a"].last_ingestion == "2026-05-02T00:00:00Z"
        assert by_slug["owner-c"].last_ingestion is None

    def test_open_reasons_histogram(self, master_db):
        f = queue_stats.build_queue_stats(db_path=master_db, state_db_path=master_db.with_name("absent.db")).federal
        reasons = dict(f.reasons)
        # 'name match only' open: a=2 + c=5 = 7 ; 'city/state alone' open: a=1
        assert reasons["name match only"] == 7
        assert reasons["city/state alone"] == 1
        # resolved rows are excluded from the open histogram
        assert sum(n for _, n in f.reasons) == f.total_open


# ─── State ───────────────────────────────────────────────────────────────────


class TestState:
    def test_absent_state_db_is_none(self, master_db, tmp_path):
        missing = tmp_path / "nope.db"
        stats = queue_stats.build_queue_stats(db_path=master_db, state_db_path=missing)
        assert stats.state is None

    def test_state_totals_and_breakdowns(self, master_db, state_db_path):
        stats = queue_stats.build_queue_stats(db_path=master_db, state_db_path=state_db_path)
        s = stats.state
        assert s is not None
        assert s.total_open == 4      # TX 3 + CA 1
        assert s.total_resolved == 1  # CA 1
        jur = {j: (o, r) for j, o, r in s.by_jurisdiction}
        assert jur["TX"] == (3, 0)
        assert jur["CA"] == (1, 1)
        # by_owner only lists owners with open items, desc
        owners = dict(s.by_owner)
        assert owners["owner-a"] == 3  # TX1, TX2, CA1
        assert owners["owner-b"] == 1  # TX3 (CA2 resolved)
        assert s.by_owner[0][0] == "owner-a"

    def test_state_reasons_open_only(self, master_db, state_db_path):
        s = queue_stats.build_queue_stats(db_path=master_db, state_db_path=state_db_path).state
        reasons = dict(s.reasons)
        assert reasons["name match only"] == 3  # TX1, TX2, CA1 (CA2 resolved-excluded)
        assert reasons["zip only"] == 1


# ─── Formatting smoke ────────────────────────────────────────────────────────


def test_format_runs_and_mentions_layers(master_db, state_db_path):
    stats = queue_stats.build_queue_stats(db_path=master_db, state_db_path=state_db_path)
    out = queue_stats.format_queue_stats(stats)
    assert "REVIEW-QUEUE BURNDOWN" in out
    assert "FEDERAL" in out
    assert "STATE" in out
    assert "owner-c" in out  # highest open federal queue is shown


def test_format_handles_absent_state(master_db, tmp_path):
    stats = queue_stats.build_queue_stats(db_path=master_db, state_db_path=tmp_path / "nope.db")
    out = queue_stats.format_queue_stats(stats)
    assert "STATE (state.db): not present." in out

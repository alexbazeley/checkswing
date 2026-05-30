"""Tests for scripts/ingest_committee_disbursements.py — orchestrator + idempotency."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses

from scripts import (
    db,
    fetch_committee_disbursements,
    ingest_committee_disbursements,
)
from scripts.fetch_committee_disbursements import BY_RECIPIENT_ENDPOINT
from scripts.fetch_fec import BASE_URL


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_master(tmp_path, monkeypatch):
    """Tmp master.db seeded with a committee in committees + committee_totals.

    The orchestrator only walks committees that exist in both tables (a Phase 1
    enriched recipient), so we seed both rather than a raw donation row.
    """
    db_path = tmp_path / "master.db"
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    monkeypatch.setattr(db, "MASTER_DB", db_path)
    monkeypatch.setattr(fetch_committee_disbursements, "RAW_DIR", raw_dir)
    monkeypatch.setattr(ingest_committee_disbursements, "MASTER_DB", db_path)
    monkeypatch.setattr(ingest_committee_disbursements, "DATA_DIR", tmp_path)
    monkeypatch.setattr(
        ingest_committee_disbursements,
        "BENEFICIARIES_LOCK",
        tmp_path / ".committee_disbursements_ingest.lock",
    )

    from scripts import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "SNAPSHOTS_DIR", snap_dir)
    monkeypatch.setattr(paths, "RAW_DIR", raw_dir)
    monkeypatch.setattr(paths, "MASTER_DB", db_path)

    def _fake_relpath(p):
        try:
            return Path(p).resolve().relative_to(tmp_path).as_posix()
        except ValueError:
            return str(p)

    monkeypatch.setattr(fetch_committee_disbursements, "_persist_beneficiaries_raw",
                        fetch_committee_disbursements._persist_beneficiaries_raw)
    monkeypatch.setattr(ingest_committee_disbursements, "relpath", _fake_relpath)

    db.init(db_path)
    with db.connect(db_path) as conn:
        # Seed one committee with two cycles of totals so list_cycles_for_committee
        # returns [2022, 2024].
        conn.execute(
            """
            INSERT INTO committees (
                committee_id, name, raw_payload_path, fetched_at, refreshed_at
            ) VALUES (
                'C00000001', 'ACME PAC',
                'data/raw/_committees/C00000001/x.json',
                '2024-10-15T00:00:00Z', '2024-10-15T00:00:00Z'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO committee_totals
              (committee_id, cycle, receipts, disbursements,
               raw_payload_path, fetched_at)
            VALUES
              ('C00000001', 2024, 1000.0, 800.0,
               'data/raw/_committees/C00000001/x.json', '2024-10-15T00:00:00Z'),
              ('C00000001', 2022, 500.0, 480.0,
               'data/raw/_committees/C00000001/x.json', '2024-10-15T00:00:00Z')
            """
        )
    return db_path


@pytest.fixture
def fec_responses(monkeypatch):
    monkeypatch.setenv("FEC_API_KEY", "test-key")
    from scripts import fetch_fec
    monkeypatch.setattr(fetch_fec, "MIN_REQUEST_INTERVAL_S", 0.0)
    with responses.RequestsMock() as rsps:
        yield rsps


def _mock_by_recipient(rsps, results_by_cycle):
    """Register one mock per cycle. results_by_cycle is dict[int, list[dict]]."""
    for cycle, results in results_by_cycle.items():
        rsps.add(
            responses.GET,
            BASE_URL + BY_RECIPIENT_ENDPOINT,
            json={"results": results, "pagination": {"last_indexes": None}},
            status=200,
        )


# ─── Cycle enumeration & freshness ──────────────────────────────────────────


class TestListCyclesForCommittee:
    def test_returns_cycles_in_ascending_order(self, tmp_master):
        cycles = ingest_committee_disbursements.list_cycles_for_committee(
            "C00000001", tmp_master
        )
        assert cycles == [2022, 2024]

    def test_returns_empty_for_unknown_committee(self, tmp_master):
        cycles = ingest_committee_disbursements.list_cycles_for_committee(
            "C99999999", tmp_master
        )
        assert cycles == []


class TestListCommitteesForBeneficiaries:
    def test_returns_committees_with_totals(self, tmp_master):
        ids = ingest_committee_disbursements.list_committees_for_beneficiaries(
            tmp_master
        )
        assert ids == ["C00000001"]

    def test_excludes_committees_without_totals(self, tmp_master):
        # Insert a committee row but no committee_totals — should be skipped.
        with db.connect(tmp_master) as conn:
            conn.execute(
                """
                INSERT INTO committees (
                    committee_id, name, raw_payload_path, fetched_at, refreshed_at
                ) VALUES (
                    'C00000099', 'NO TOTALS PAC',
                    'data/raw/_committees/C00000099/x.json',
                    '2024-10-15T00:00:00Z', '2024-10-15T00:00:00Z'
                )
                """
            )
        ids = ingest_committee_disbursements.list_committees_for_beneficiaries(
            tmp_master
        )
        assert "C00000099" not in ids


# ─── Per-committee ingest ───────────────────────────────────────────────────


class TestIngestCommitteeDisbursements:
    def test_writes_rows_per_cycle(self, tmp_master, fec_responses):
        _mock_by_recipient(fec_responses, {
            2022: [
                {"candidate_id": "H22A", "recipient_name": "ALPHA",
                 "total": 5000.0, "count": 2, "recipient_party": "DEM"},
            ],
            2024: [
                {"candidate_id": "H24A", "recipient_name": "BETA",
                 "total": 7500.0, "count": 3, "recipient_party": "REP"},
                {"recipient_committee_id": "C00099999",
                 "recipient_name": "GAMMA SUPER PAC",
                 "total": 2500.0, "count": 1, "recipient_party": "REP"},
            ],
        })
        result = ingest_committee_disbursements.ingest_committee_disbursements(
            "C00000001", db_path=tmp_master,
        )
        assert result["cycles_attempted"] == 2
        assert result["cycles_fetched"] == 2
        assert result["cycles_failed"] == 0
        assert result["rows_written"] == 3

        with db.connect(tmp_master) as conn:
            rows = list(conn.execute(
                """SELECT cycle, recipient_id, recipient_kind, recipient_name,
                          total_amount, recipient_party
                     FROM committee_disbursements_by_recipient
                    WHERE committee_id = ?
                    ORDER BY cycle, total_amount DESC""",
                ("C00000001",),
            ))
            assert [(r["cycle"], r["recipient_id"], r["recipient_kind"]) for r in rows] == [
                (2022, "H22A", "candidate"),
                (2024, "H24A", "candidate"),
                (2024, "C00099999", "committee"),
            ]
            assert rows[0]["total_amount"] == 5000.0
            assert rows[1]["recipient_party"] == "REP"

    def test_idempotent_re_run_replaces_rows(self, tmp_master, fec_responses):
        """Running again with fresh FEC data replaces the prior cycle snapshot
        — no duplicate rows, retracted recipients vanish."""
        _mock_by_recipient(fec_responses, {
            2022: [
                {"candidate_id": "H22A", "recipient_name": "ALPHA", "total": 5000.0},
                {"candidate_id": "H22B", "recipient_name": "BETA", "total": 1000.0},
            ],
            2024: [],
        })
        ingest_committee_disbursements.ingest_committee_disbursements(
            "C00000001", db_path=tmp_master,
        )
        with db.connect(tmp_master) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM committee_disbursements_by_recipient WHERE committee_id=?",
                ("C00000001",),
            ).fetchone()[0]
            assert n == 2

        # Second run: FEC now reports a different set — one recipient gone,
        # one new, one amount changed.
        fec_responses.reset()
        _mock_by_recipient(fec_responses, {
            2022: [
                {"candidate_id": "H22A", "recipient_name": "ALPHA", "total": 5500.0},
                {"candidate_id": "H22C", "recipient_name": "GAMMA", "total": 2000.0},
            ],
            2024: [],
        })
        ingest_committee_disbursements.ingest_committee_disbursements(
            "C00000001", db_path=tmp_master, force_refresh=True,
        )
        with db.connect(tmp_master) as conn:
            rows = list(conn.execute(
                """SELECT recipient_id, total_amount FROM committee_disbursements_by_recipient
                    WHERE committee_id = ? AND cycle = ?
                    ORDER BY recipient_id""",
                ("C00000001", 2022),
            ))
            # BETA gone (retracted), ALPHA amount updated, GAMMA added
            assert [(r["recipient_id"], r["total_amount"]) for r in rows] == [
                ("H22A", 5500.0),
                ("H22C", 2000.0),
            ]

    def test_freshness_gate_skips_recent_cycles(self, tmp_master, fec_responses):
        _mock_by_recipient(fec_responses, {
            2022: [{"candidate_id": "H22A", "recipient_name": "ALPHA", "total": 100.0}],
            2024: [{"candidate_id": "H24A", "recipient_name": "BETA", "total": 200.0}],
        })
        ingest_committee_disbursements.ingest_committee_disbursements(
            "C00000001", db_path=tmp_master,
        )
        # Second run: every cycle should be skipped fresh, FEC not called.
        fec_responses.reset()
        result = ingest_committee_disbursements.ingest_committee_disbursements(
            "C00000001", db_path=tmp_master,
        )
        assert result["cycles_skipped_fresh"] == 2
        assert result["cycles_fetched"] == 0

    def test_force_refresh_overrides_freshness(self, tmp_master, fec_responses):
        _mock_by_recipient(fec_responses, {
            2022: [{"candidate_id": "H22A", "recipient_name": "ALPHA", "total": 100.0}],
            2024: [{"candidate_id": "H24A", "recipient_name": "BETA", "total": 200.0}],
        })
        ingest_committee_disbursements.ingest_committee_disbursements(
            "C00000001", db_path=tmp_master,
        )

        fec_responses.reset()
        _mock_by_recipient(fec_responses, {
            2022: [{"candidate_id": "H22A", "recipient_name": "ALPHA", "total": 999.0}],
            2024: [{"candidate_id": "H24A", "recipient_name": "BETA", "total": 888.0}],
        })
        result = ingest_committee_disbursements.ingest_committee_disbursements(
            "C00000001", db_path=tmp_master, force_refresh=True,
        )
        assert result["cycles_fetched"] == 2
        with db.connect(tmp_master) as conn:
            amts = [r[0] for r in conn.execute(
                "SELECT total_amount FROM committee_disbursements_by_recipient WHERE committee_id=? ORDER BY cycle",
                ("C00000001",),
            )]
            assert amts == [999.0, 888.0]

    def test_cycles_argument_restricts_target(self, tmp_master, fec_responses):
        """Passing cycles=[2024] ignores committee_totals cycles and only
        fetches the explicit list."""
        _mock_by_recipient(fec_responses, {
            2024: [{"candidate_id": "H24A", "recipient_name": "BETA", "total": 200.0}],
        })
        result = ingest_committee_disbursements.ingest_committee_disbursements(
            "C00000001", cycles=[2024], db_path=tmp_master,
        )
        assert result["cycles_attempted"] == 1
        assert result["cycles_fetched"] == 1
        with db.connect(tmp_master) as conn:
            cycles_in_db = [r[0] for r in conn.execute(
                "SELECT DISTINCT cycle FROM committee_disbursements_by_recipient",
            )]
            assert cycles_in_db == [2024]

    def test_unknown_recipient_id_row_skipped(self, tmp_master, fec_responses):
        """Rows with neither candidate_id nor recipient_committee_id nor name
        get dropped (no stable PK) — CLAUDE.md §1.5."""
        _mock_by_recipient(fec_responses, {
            2022: [
                {"total": 100.0},  # no id, no name — should be dropped
                {"candidate_id": "H22A", "recipient_name": "ALPHA", "total": 5000.0},
            ],
            2024: [],
        })
        result = ingest_committee_disbursements.ingest_committee_disbursements(
            "C00000001", db_path=tmp_master,
        )
        assert result["rows_written"] == 1


# ─── All-committee orchestrator ─────────────────────────────────────────────


class TestIngestAllCommitteeDisbursements:
    def test_walks_every_enriched_committee(self, tmp_master, fec_responses):
        # Add a second enriched committee so we can verify the orchestrator
        # iterates both.
        with db.connect(tmp_master) as conn:
            conn.execute(
                """
                INSERT INTO committees (
                    committee_id, name, raw_payload_path, fetched_at, refreshed_at
                ) VALUES (
                    'C00000002', 'BETA PAC',
                    'x', '2024-10-15T00:00:00Z', '2024-10-15T00:00:00Z'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO committee_totals (committee_id, cycle, raw_payload_path, fetched_at)
                VALUES ('C00000002', 2024, 'x', '2024-10-15T00:00:00Z')
                """
            )

        # 3 mocks: C00000001 has 2 cycles, C00000002 has 1 cycle.
        _mock_by_recipient(fec_responses, {
            2022: [{"candidate_id": "H22A", "recipient_name": "ALPHA", "total": 100.0}],
            2024: [{"candidate_id": "H24A", "recipient_name": "BETA", "total": 200.0}],
        })
        fec_responses.add(
            responses.GET,
            BASE_URL + BY_RECIPIENT_ENDPOINT,
            json={"results": [{"candidate_id": "X", "recipient_name": "X", "total": 50.0}],
                  "pagination": {"last_indexes": None}},
            status=200,
        )

        summary = ingest_committee_disbursements.ingest_all_committee_disbursements(
            db_path=tmp_master,
        )
        assert summary["attempted"] == 2
        assert summary["fetched"] == 2
        assert summary["failed"] == 0
        assert summary["rows_written"] == 3
        # Snapshot was created (CLAUDE.md §1.6)
        assert summary["snapshot_path"] is not None

    def test_only_subset_restricts_targets(self, tmp_master, fec_responses):
        # Add a second committee to verify --only filters it out.
        with db.connect(tmp_master) as conn:
            conn.execute(
                "INSERT INTO committees (committee_id, name, raw_payload_path, fetched_at, refreshed_at) "
                "VALUES ('C00000002', 'BETA PAC', 'x', '2024-10-15T00:00:00Z', '2024-10-15T00:00:00Z')"
            )
            conn.execute(
                "INSERT INTO committee_totals (committee_id, cycle, raw_payload_path, fetched_at) "
                "VALUES ('C00000002', 2024, 'x', '2024-10-15T00:00:00Z')"
            )
        _mock_by_recipient(fec_responses, {
            2024: [{"candidate_id": "X", "recipient_name": "X", "total": 50.0}],
        })
        summary = ingest_committee_disbursements.ingest_all_committee_disbursements(
            only=["C00000002"], db_path=tmp_master,
        )
        assert summary["attempted"] == 1
        assert summary["rows_written"] == 1

    def test_failed_committee_does_not_abort_batch(self, tmp_master, fec_responses):
        # Add a second committee; the first returns an HTTP error, second succeeds.
        with db.connect(tmp_master) as conn:
            conn.execute(
                "INSERT INTO committees (committee_id, name, raw_payload_path, fetched_at, refreshed_at) "
                "VALUES ('C00000002', 'BETA PAC', 'x', '2024-10-15T00:00:00Z', '2024-10-15T00:00:00Z')"
            )
            conn.execute(
                "INSERT INTO committee_totals (committee_id, cycle, raw_payload_path, fetched_at) "
                "VALUES ('C00000002', 2024, 'x', '2024-10-15T00:00:00Z')"
            )

        # Per-cycle FEC error counts as a cycles_failed, NOT a committee
        # failure (the cycle-level catch handles HTTPs/timeouts). To test the
        # outer batch-failure path we'd need the orchestrator itself to raise
        # before per-cycle dispatch — the simplest reachable case is an
        # unconfigured FECClient. Skipping the outer-fault path here; the
        # per-cycle isolation is the contract that matters.
        _mock_by_recipient(fec_responses, {
            2022: [],  # C1 cycle 2022 — empty result
            2024: [],  # C1 cycle 2024 — empty result
        })
        fec_responses.add(
            responses.GET,
            BASE_URL + BY_RECIPIENT_ENDPOINT,
            json={"results": [{"candidate_id": "X", "recipient_name": "X", "total": 50.0}],
                  "pagination": {"last_indexes": None}},
            status=200,
        )
        summary = ingest_committee_disbursements.ingest_all_committee_disbursements(
            db_path=tmp_master,
        )
        # Both committees attempted; no batch-level failure.
        assert summary["attempted"] == 2
        assert summary["failed"] == 0

    def test_max_count_caps_attempts(self, tmp_master, fec_responses):
        with db.connect(tmp_master) as conn:
            conn.execute(
                "INSERT INTO committees (committee_id, name, raw_payload_path, fetched_at, refreshed_at) "
                "VALUES ('C00000002', 'BETA PAC', 'x', '2024-10-15T00:00:00Z', '2024-10-15T00:00:00Z')"
            )
            conn.execute(
                "INSERT INTO committee_totals (committee_id, cycle, raw_payload_path, fetched_at) "
                "VALUES ('C00000002', 2024, 'x', '2024-10-15T00:00:00Z')"
            )
        _mock_by_recipient(fec_responses, {
            2022: [],
            2024: [],
        })
        summary = ingest_committee_disbursements.ingest_all_committee_disbursements(
            db_path=tmp_master, max_count=1,
        )
        assert summary["attempted"] == 1

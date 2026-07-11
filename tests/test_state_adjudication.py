"""Tests for the §5.4 state review-queue adjudication CLI + db helpers."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from scripts import state_db
from scripts.cli import cli


@pytest.fixture
def state_env(tmp_path, monkeypatch):
    """Tmp state.db + patched snapshot/provenance paths, seeded with 3 open
    review-queue items for one owner across two jurisdictions."""
    db_path = tmp_path / "state.db"
    snaps = tmp_path / "snapshots"; snaps.mkdir()
    prov = tmp_path / "PROVENANCE_LOG.md"; prov.write_text("# log\n", encoding="utf-8")
    from scripts import paths
    # The CLI commands call state_db.init()/connect()/snapshot() with NO db_path,
    # and those defaults were bound to the real STATE_DB at def-time — so patch the
    # functions to default to the tmp db (module-attribute patch is looked up at
    # call time). PROVENANCE_LOG is imported inside each command from paths.
    monkeypatch.setattr(state_db, "SNAPSHOTS_DIR", snaps)
    monkeypatch.setattr(paths, "PROVENANCE_LOG", prov)
    _init, _connect, _snap = state_db.init, state_db.connect, state_db.snapshot
    monkeypatch.setattr(state_db, "init", lambda p=db_path: _init(p))
    monkeypatch.setattr(state_db, "connect", lambda p=db_path: _connect(p))
    monkeypatch.setattr(state_db, "snapshot", lambda run_id, p=db_path: _snap(run_id, p))
    state_db.init(db_path)

    def _q(txn, juris, reason="name match only"):
        with state_db.connect(db_path) as conn:
            state_db.insert_state_review_queue(conn, {
                "state_txn_id": txn, "entity_slug": "moreno-arte", "jurisdiction": juris,
                "source": "TEC" if juris == "TX" else "CAL-ACCESS", "reason": reason,
                "raw_payload_path": "data/raw/state/x.csv", "queued_at": "2026-07-11T00:00:00Z"})
    _q("TX:T1", "TX"); _q("TX:T2", "TX", "city_state only"); _q("CA:T3", "CA")
    return db_path


def _open(db_path, slug="moreno-arte"):
    with state_db.connect(db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM state_review_queue WHERE entity_slug=? AND resolution IS NULL",
            (slug,)).fetchone()[0]


class TestResolveState:
    def test_resolve_and_unresolve(self, state_env):
        r = CliRunner()
        assert _open(state_env) == 3
        out = r.invoke(cli, ["resolve-state", "TX:T1", "moreno-arte", "--reason", "different person"])
        assert out.exit_code == 0, out.output
        assert _open(state_env) == 2  # item now resolved
        with state_db.connect(state_env) as conn:
            assert "TX:T1" in state_db.discarded_txns_for_slug(conn, "moreno-arte")
        # undo
        r.invoke(cli, ["unresolve-state", "TX:T1", "moreno-arte"])
        assert _open(state_env) == 3
        with state_db.connect(state_env) as conn:
            assert "TX:T1" not in state_db.discarded_txns_for_slug(conn, "moreno-arte")


class TestBulkDiscardState:
    def test_bulk_discard_all(self, state_env):
        out = CliRunner().invoke(cli, ["bulk-discard-state", "moreno-arte", "--yes"])
        assert out.exit_code == 0, out.output
        assert _open(state_env) == 0

    def test_bulk_discard_scoped_by_jurisdiction(self, state_env):
        out = CliRunner().invoke(cli, ["bulk-discard-state", "moreno-arte", "--jurisdiction", "TX", "--yes"])
        assert out.exit_code == 0, out.output
        assert _open(state_env) == 1  # only the CA item remains open
        with state_db.connect(state_env) as conn:
            remaining = conn.execute(
                "SELECT jurisdiction FROM state_review_queue WHERE resolution IS NULL").fetchone()[0]
        assert remaining == "CA"

    def test_bulk_discard_scoped_by_reason(self, state_env):
        out = CliRunner().invoke(
            cli, ["bulk-discard-state", "moreno-arte", "--reason-like", "city_state%", "--yes"])
        assert out.exit_code == 0, out.output
        assert _open(state_env) == 2  # only the one "city_state only" item discarded


class TestAttributeExcludeState:
    def test_attribute_writes_override(self, state_env):
        out = CliRunner().invoke(
            cli, ["attribute-state", "TX:T1", "moreno-arte", "--reason", "verified", "--yes"])
        assert out.exit_code == 0, out.output
        with state_db.connect(state_env) as conn:
            assert state_db.state_manual_attributions_for_slug(conn, "moreno-arte")["TX:T1"] == "CONFIRMED"
        # undo
        CliRunner().invoke(cli, ["unattribute-state", "TX:T1", "moreno-arte"])
        with state_db.connect(state_env) as conn:
            assert "TX:T1" not in state_db.state_manual_attributions_for_slug(conn, "moreno-arte")

    def test_exclude_writes_excluded_override(self, state_env):
        out = CliRunner().invoke(
            cli, ["exclude-state", "TX:T1", "moreno-arte", "--reason", "brother", "--yes"])
        assert out.exit_code == 0, out.output
        with state_db.connect(state_env) as conn:
            assert state_db.state_manual_attributions_for_slug(conn, "moreno-arte")["TX:T1"] == "EXCLUDED"
        CliRunner().invoke(cli, ["unexclude-state", "TX:T1", "moreno-arte"])
        with state_db.connect(state_env) as conn:
            assert "TX:T1" not in state_db.state_manual_attributions_for_slug(conn, "moreno-arte")

    def test_attribute_rejects_bad_status(self, state_env):
        out = CliRunner().invoke(
            cli, ["attribute-state", "TX:T1", "moreno-arte", "--reason", "x", "--status", "SUPER", "--yes"])
        assert out.exit_code != 0

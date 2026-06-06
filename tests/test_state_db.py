"""Tests for the Phase 4 state campaign-finance DB (scripts/state_db.py)."""
from __future__ import annotations

from scripts import state_db


def _base_row(**over) -> dict:
    row = {
        "state_txn_id": state_db.compose_state_txn_id(
            jurisdiction="CA", source="CAL-ACCESS", source_filing_id="F100", source_tran_id="T1"
        ),
        "jurisdiction": "CA",
        "source": "CAL-ACCESS",
        "source_tran_id": "T1",
        "source_filing_id": "F100",
        "discovery_source": None,
        "entity_slug": "moreno-arte",
        "entity_kind": "owner",
        "parent_owner_slug": None,
        "status": "CONFIRMED",
        "status_reason": "two confirming signals",
        "signals_matched": "[]",
        "contributor_name_raw": "MORENO, ARTURO",
        "contributor_employer_raw": "Outdoor Systems",
        "contributor_occupation_raw": "Owner",
        "contributor_city": "Phoenix",
        "contributor_state": "AZ",
        "contributor_zip": "85016",
        "recipient_filer_id": "1234567",
        "recipient_name": "Friends of Some Assemblymember",
        "recipient_type": "candidate",
        "recipient_party": None,
        "recipient_office": None,
        "amount": 1500.0,
        "date": "2018-06-01",
        "election_cycle": 2018,
        "report_type": None,
        "raw_payload_path": "data/raw/state/ca/2026-06-03T00-00-00Z__rcpt.csv",
        "ingested_at": "2026-06-03T00:00:00Z",
    }
    row.update(over)
    return row


def test_init_is_idempotent(tmp_path):
    db_path = tmp_path / "state.db"
    state_db.init(db_path)
    state_db.init(db_path)  # second call must not error
    with state_db.connect(db_path) as conn:
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "state_donations" in tables
        assert "state_filers" in tables
        assert "state_review_queue" in tables
        version = conn.execute("SELECT MAX(version) AS v FROM state_schema_version").fetchone()["v"]
        assert version == state_db.STATE_SCHEMA_VERSION


def test_insert_then_unchanged_is_idempotent(tmp_path):
    db_path = tmp_path / "state.db"
    state_db.init(db_path)
    with state_db.connect(db_path) as conn:
        assert state_db.insert_state_donation(conn, _base_row()) == ("inserted", None)
    with state_db.connect(db_path) as conn:
        assert state_db.insert_state_donation(conn, _base_row()) == ("unchanged", None)
        n = conn.execute("SELECT COUNT(*) AS n FROM state_donations").fetchone()["n"]
        assert n == 1


def test_amendment_supersedes(tmp_path):
    db_path = tmp_path / "state.db"
    state_db.init(db_path)
    with state_db.connect(db_path) as conn:
        state_db.insert_state_donation(conn, _base_row())
    # Portal restates the amount.
    with state_db.connect(db_path) as conn:
        action, reason = state_db.insert_state_donation(conn, _base_row(amount=2500.0))
        assert action == "superseded"
        assert "amount" in reason
    with state_db.connect(db_path) as conn:
        live = conn.execute(
            "SELECT amount FROM state_donations WHERE status != 'SUPERSEDED'"
        ).fetchall()
        archived = conn.execute(
            "SELECT amount, superseded_by FROM state_donations WHERE status = 'SUPERSEDED'"
        ).fetchall()
        assert len(live) == 1 and live[0]["amount"] == 2500.0
        assert len(archived) == 1 and archived[0]["amount"] == 1500.0


def test_reclassify_deletes_donations_but_keeps_verdicts(tmp_path):
    db_path = tmp_path / "state.db"
    state_db.init(db_path)
    txn = _base_row()["state_txn_id"]
    with state_db.connect(db_path) as conn:
        state_db.insert_state_donation(conn, _base_row())
        state_db.upsert_state_manual_attribution(
            conn,
            state_txn_id=txn,
            entity_slug="moreno-arte",
            status="EXCLUDED",
            reason="not this owner",
            source="manual audit",
            attributed_at="2026-06-03T00:00:00Z",
        )
        state_db.upsert_state_review_resolution(
            conn,
            state_txn_id="other",
            entity_slug="moreno-arte",
            resolution="DISCARDED",
            resolution_reason="stranger",
            resolved_at="2026-06-03T00:00:00Z",
        )
    with state_db.connect(db_path) as conn:
        deleted = state_db.delete_donations_for_slug(conn, "moreno-arte")
        assert deleted == 1
    with state_db.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM state_donations").fetchone()["n"] == 0
        # Durable verdicts survive.
        assert state_db.state_manual_attributions_for_slug(conn, "moreno-arte") == {txn: "EXCLUDED"}
        assert state_db.discarded_txns_for_slug(conn, "moreno-arte") == {"other"}


def test_delete_donations_scoped_to_jurisdiction(tmp_path):
    # A multi-state owner: deleting one jurisdiction (for a per-state reclassify) must
    # NOT touch the owner's rows in other jurisdictions — they wouldn't be restored.
    db_path = tmp_path / "state.db"
    state_db.init(db_path)
    ca = _base_row()
    tx = _base_row(
        state_txn_id=state_db.compose_state_txn_id(
            jurisdiction="TX", source="TEC", source_filing_id="730", source_tran_id="100000001"
        ),
        jurisdiction="TX",
        source="TEC",
        source_tran_id="100000001",
        source_filing_id="730",
    )
    with state_db.connect(db_path) as conn:
        state_db.insert_state_donation(conn, ca)
        state_db.insert_state_donation(conn, tx)
    with state_db.connect(db_path) as conn:
        deleted = state_db.delete_donations_for_slug(conn, "moreno-arte", jurisdiction="CA")
        assert deleted == 1
    with state_db.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT jurisdiction FROM state_donations WHERE entity_slug='moreno-arte'"
        ).fetchall()
        assert [r["jurisdiction"] for r in rows] == ["TX"]  # CA gone, TX survives


def test_upsert_filer_overwrites(tmp_path):
    db_path = tmp_path / "state.db"
    state_db.init(db_path)
    filer = {
        "filer_id": "1234567",
        "jurisdiction": "CA",
        "source": "CAL-ACCESS",
        "name": "Friends of X",
        "filer_type": "candidate",
        "party": None,
        "office": "State Assembly",
        "raw_payload_path": "data/raw/state/ca/x.csv",
        "fetched_at": "2026-06-03T00:00:00Z",
        "refreshed_at": "2026-06-03T00:00:00Z",
    }
    with state_db.connect(db_path) as conn:
        state_db.upsert_state_filer(conn, filer)
        state_db.upsert_state_filer(conn, {**filer, "name": "Friends of X for Senate"})
        rows = conn.execute("SELECT name FROM state_filers").fetchall()
        assert len(rows) == 1 and rows[0]["name"] == "Friends of X for Senate"

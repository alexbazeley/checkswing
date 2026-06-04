"""Tests for the state ingestion orchestrator (scripts/ingest_state.py).

Uses a temp owners/ dir + temp state.db + fixture CAL-ACCESS rows — no network,
no giant files. Proves: classification routing into the right tables, idempotency,
amendment supersession, the EXCLUDED override, and the unparseable-date gate.
"""
from __future__ import annotations

import yaml

from scripts import ingest_state, state_db


OWNER_YAML = {
    "slug": "moreno-arte",
    "name": "Arturo Moreno",
    "name_variants": ["Arturo Moreno", "Arte Moreno", "Moreno, Arturo"],
    "verifying_signals": {
        "cities": ["phoenix"],
        "states": ["AZ"],
        "employers": ["Outdoor Systems", "Los Angeles Angels"],
        "occupations": ["owner"],
    },
    "strong_signals": {},
    "negative_signals": {},
}


def _rcpt(**over) -> dict:
    row = {
        "ENTITY_CD": "IND",
        "CTRIB_NAML": "MORENO",
        "CTRIB_NAMF": "ARTURO",
        "CTRIB_EMP": "Outdoor Systems",
        "CTRIB_OCC": "Owner",
        "CTRIB_CITY": "Phoenix",
        "CTRIB_ST": "AZ",
        "CTRIB_ZIP4": "85016",
        "AMOUNT": "1500.00",
        "RCPT_DATE": "2018-06-01",
        "TRAN_ID": "T1",
        "FILING_ID": "F100",
    }
    row.update(over)
    return row


def _resolver(rcpt: dict) -> dict:
    return {"filer_id": "C-" + rcpt["FILING_ID"], "name": "Friends of Someone", "type": "candidate"}


def _setup(tmp_path, monkeypatch):
    owners_dir = tmp_path / "owners"
    owners_dir.mkdir()
    (owners_dir / "moreno-arte.yaml").write_text(yaml.safe_dump(OWNER_YAML), encoding="utf-8")
    monkeypatch.setattr(ingest_state, "OWNERS_DIR", owners_dir)
    # Redirect the repo-global audit trail + snapshot dir to tmp so tests never
    # touch catalog/PROVENANCE_LOG.md or data/snapshots/ (test-isolation).
    monkeypatch.setattr(ingest_state, "PROVENANCE_LOG", tmp_path / "PROVENANCE_LOG.md")
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    monkeypatch.setattr(state_db, "SNAPSHOTS_DIR", snaps)
    db_path = tmp_path / "state.db"
    state_db.init(db_path)
    return db_path


def test_confirmed_row_lands_in_state_donations(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    res = ingest_state.ingest_state_entity(
        "moreno-arte",
        rcpt_rows=[_rcpt()],
        recipient_resolver=_resolver,
        raw_payload_path="data/raw/state/ca/x.csv",
        db_path=db_path,
    )
    assert res.confirmed == 1 and res.uncertain == 0
    with state_db.connect(db_path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM state_donations WHERE status='CONFIRMED'"
        ).fetchone()["n"]
        assert n == 1
        filer = conn.execute("SELECT name FROM state_filers").fetchone()
        assert filer["name"] == "Friends of Someone"


def test_uncertain_routes_to_review_queue(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    res = ingest_state.ingest_state_entity(
        "moreno-arte",
        rcpt_rows=[_rcpt(CTRIB_EMP="", CTRIB_OCC="", CTRIB_CITY="", CTRIB_ST="")],
        recipient_resolver=_resolver,
        db_path=db_path,
    )
    assert res.uncertain == 1 and res.confirmed == 0
    with state_db.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM state_review_queue").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM state_donations").fetchone()["n"] == 0


def test_non_matching_name_filtered(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    res = ingest_state.ingest_state_entity(
        "moreno-arte",
        rcpt_rows=[_rcpt(CTRIB_NAML="SMITH", CTRIB_NAMF="JOHN")],
        recipient_resolver=_resolver,
        db_path=db_path,
    )
    assert res.records_scanned == 1
    assert res.confirmed == res.probable == res.uncertain == 0


def test_idempotent_reingest(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    for _ in range(2):
        ingest_state.ingest_state_entity(
            "moreno-arte", rcpt_rows=[_rcpt()], recipient_resolver=_resolver, db_path=db_path
        )
    with state_db.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM state_donations").fetchone()["n"] == 1


def test_amendment_supersedes(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    ingest_state.ingest_state_entity(
        "moreno-arte", rcpt_rows=[_rcpt()], recipient_resolver=_resolver, db_path=db_path
    )
    res = ingest_state.ingest_state_entity(
        "moreno-arte", rcpt_rows=[_rcpt(AMOUNT="3000.00")], recipient_resolver=_resolver, db_path=db_path
    )
    assert res.superseded == 1
    with state_db.connect(db_path) as conn:
        live = conn.execute(
            "SELECT amount FROM state_donations WHERE status='CONFIRMED'"
        ).fetchone()
        assert live["amount"] == 3000.0


def test_excluded_override_drops_row(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    txn = state_db.compose_state_txn_id(
        jurisdiction="CA", source="CAL-ACCESS", source_filing_id="F100", source_tran_id="T1"
    )
    with state_db.connect(db_path) as conn:
        state_db.upsert_state_manual_attribution(
            conn,
            state_txn_id=txn,
            entity_slug="moreno-arte",
            status="EXCLUDED",
            reason="confirmed a same-named relative",
            source="manual audit",
            attributed_at="2026-06-03T00:00:00Z",
        )
    res = ingest_state.ingest_state_entity(
        "moreno-arte", rcpt_rows=[_rcpt()], recipient_resolver=_resolver, db_path=db_path
    )
    assert res.excluded == 1 and res.confirmed == 0
    with state_db.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM state_donations").fetchone()["n"] == 0


def test_unparseable_date_routes_to_review(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    res = ingest_state.ingest_state_entity(
        "moreno-arte", rcpt_rows=[_rcpt(RCPT_DATE="unknown")], recipient_resolver=_resolver, db_path=db_path
    )
    assert res.skipped_no_date == 1 and res.confirmed == 0
    with state_db.connect(db_path) as conn:
        row = conn.execute("SELECT reason FROM state_review_queue").fetchone()
        assert row is not None and "date" in row["reason"]


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    res = ingest_state.ingest_state_entity(
        "moreno-arte", rcpt_rows=[_rcpt()], recipient_resolver=_resolver, dry_run=True, db_path=db_path
    )
    assert res.confirmed == 1 and len(res.rows) == 1
    with state_db.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM state_donations").fetchone()["n"] == 0


def test_reclassify_rebuilds_from_extract(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    ingest_state.ingest_state_entity(
        "moreno-arte", rcpt_rows=[_rcpt()], recipient_resolver=_resolver, db_path=db_path
    )
    # Re-run as reclassify with an amended extract.
    res = ingest_state.reclassify_state_entity(
        "moreno-arte",
        rcpt_rows=[_rcpt(AMOUNT="999.00")],
        recipient_resolver=_resolver,
        reason="test",
        db_path=db_path,
    )
    assert res.confirmed == 1
    with state_db.connect(db_path) as conn:
        rows = conn.execute("SELECT amount FROM state_donations").fetchall()
        # Old row was deleted (not superseded) by reclassify; only the new one remains.
        assert len(rows) == 1 and rows[0]["amount"] == 999.0

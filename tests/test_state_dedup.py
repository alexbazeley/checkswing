"""Tests for the state cross-filing/fan-out dedup migration (§1.2).

scripts/dedupe_state_crossfilings collapses content-key duplicates (same donor+
date+amount+recipient) in IL/TX/AZ — losers marked SUPERSEDED (never deleted),
highest source_filing_id kept — and leaves CA/WA untouched.
"""
from __future__ import annotations

from scripts import state_db
from scripts.dedupe_state_crossfilings import dedupe


def _row(jur, filing, tran, amount=50000.0, donor="Reinsdorf, Jerry",
         recipient="Citizens for Rauner", date="2014-11-14", **over) -> dict:
    row = {
        "state_txn_id": state_db.compose_state_txn_id(
            jurisdiction=jur, source=f"{jur}-SRC", source_filing_id=filing, source_tran_id=tran),
        "jurisdiction": jur, "source": f"{jur}-SRC",
        "source_tran_id": tran, "source_filing_id": filing, "discovery_source": None,
        "entity_slug": "reinsdorf-jerry", "entity_kind": "owner", "parent_owner_slug": None,
        "status": "CONFIRMED", "status_reason": "two signals", "signals_matched": "[]",
        "contributor_name_raw": donor, "contributor_employer_raw": "", "contributor_occupation_raw": "",
        "contributor_city": "Chicago", "contributor_state": "IL", "contributor_zip": "60616",
        "recipient_filer_id": "C1", "recipient_name": recipient, "recipient_type": "candidate",
        "recipient_party": None, "recipient_office": None,
        "amount": amount, "date": date, "election_cycle": 2014, "report_type": None,
        "raw_payload_path": "data/raw/state/x", "ingested_at": "2026-07-10T00:00:00Z",
    }
    row.update(over)
    return row


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(state_db, "SNAPSHOTS_DIR", tmp_path / "snaps")
    (tmp_path / "snaps").mkdir()
    import scripts.dedupe_state_crossfilings as m
    monkeypatch.setattr(m, "PROVENANCE_LOG", tmp_path / "PROV.md")
    db_path = tmp_path / "state.db"
    state_db.init(db_path)
    return db_path


def _live_amount(db_path, jur):
    with state_db.connect(db_path) as conn:
        return conn.execute(
            "SELECT ROUND(COALESCE(SUM(amount),0),2) FROM state_donations "
            "WHERE status IN ('CONFIRMED','PROBABLE') AND jurisdiction=?", (jur,)
        ).fetchone()[0]


def test_collapses_cross_filing_keeps_latest(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    with state_db.connect(db_path) as conn:
        # Same $50k contribution re-filed under three IL filings.
        state_db.insert_state_donation(conn, _row("IL", "551825", "4113820"))
        state_db.insert_state_donation(conn, _row("IL", "558250", "4154023"))
        state_db.insert_state_donation(conn, _row("IL", "567111", "4189613"))
    res = dedupe(jurisdictions=("IL",), db_path=db_path)
    assert res["per_jurisdiction"]["IL"]["dup_rows_superseded"] == 2
    assert _live_amount(db_path, "IL") == 50000.0  # counted once
    with state_db.connect(db_path) as conn:
        live = conn.execute(
            "SELECT source_filing_id FROM state_donations WHERE status='CONFIRMED'").fetchall()
        assert len(live) == 1 and live[0]["source_filing_id"] == "567111"  # latest kept
        sup = conn.execute("SELECT COUNT(*) FROM state_donations WHERE status='SUPERSEDED'").fetchone()[0]
        assert sup == 2  # never deleted


def test_collapses_az_fanout_distinct_tran_ids(tmp_path, monkeypatch):
    """AZ fan-out: same filing, DIFFERENT tran-ids → still one contribution."""
    db_path = _setup(tmp_path, monkeypatch)
    with state_db.connect(db_path) as conn:
        for t in ("9519583", "9522276", "9524215"):
            state_db.insert_state_donation(conn, _row(
                "AZ", "41587", t, amount=15000.0, donor="Castellini, Robert",
                recipient="Arizonans For Responsible Drug Policy"))
    res = dedupe(jurisdictions=("AZ",), db_path=db_path)
    assert res["per_jurisdiction"]["AZ"]["dup_rows_superseded"] == 2
    assert _live_amount(db_path, "AZ") == 15000.0


def test_distinct_contributions_preserved(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    with state_db.connect(db_path) as conn:
        state_db.insert_state_donation(conn, _row("IL", "551825", "1", amount=50000.0))
        state_db.insert_state_donation(conn, _row("IL", "551826", "2", amount=25000.0))  # diff amount
        state_db.insert_state_donation(conn, _row("IL", "551827", "3", recipient="Other Cmte"))  # diff recipient
    res = dedupe(jurisdictions=("IL",), db_path=db_path)
    assert res["per_jurisdiction"]["IL"]["dup_rows_superseded"] == 0
    assert _live_amount(db_path, "IL") == 125000.0


def test_ca_wa_untouched_by_default(tmp_path, monkeypatch):
    """CA/WA are NOT in the default set — their preserved line items stay."""
    db_path = _setup(tmp_path, monkeypatch)
    with state_db.connect(db_path) as conn:
        state_db.insert_state_donation(conn, _row("CA", "1831533", "A-C58482", amount=27200.0, donor="Fisher, John"))
        state_db.insert_state_donation(conn, _row("CA", "1831533", "A-C58483", amount=27200.0, donor="Fisher, John"))
    res = dedupe(db_path=db_path)  # default IL,TX,AZ
    assert "CA" not in res["per_jurisdiction"]
    assert _live_amount(db_path, "CA") == 54400.0  # both preserved


def test_idempotent(tmp_path, monkeypatch):
    db_path = _setup(tmp_path, monkeypatch)
    with state_db.connect(db_path) as conn:
        state_db.insert_state_donation(conn, _row("IL", "551825", "1"))
        state_db.insert_state_donation(conn, _row("IL", "558250", "2"))
    dedupe(jurisdictions=("IL",), db_path=db_path)
    res2 = dedupe(jurisdictions=("IL",), db_path=db_path)
    assert res2["per_jurisdiction"]["IL"]["dup_rows_superseded"] == 0

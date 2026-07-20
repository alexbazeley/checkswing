"""Dollar-weighted tier split in data.json (league + per-owner).

The record counts and the dollar split diverge — archive-wide by ~11 points —
and the counts are the flattering half, because the largest checks are the ones
filed with employer RETIRED, leaving city as the only confirming signal. These
tests pin the dollar figures so a future change can't quietly drop back to
count-only reporting.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _seed(db_path: Path) -> None:
    """One owner: 2 CONFIRMED @ $100, 1 PROBABLE @ $9,800.

    Deliberately lopsided in the same direction as the real data — 67% of
    RECORDS are confirmed but only 2% of DOLLARS are.
    """
    from scripts import db

    db.init(db_path)
    rows = [
        ("t1", "CONFIRMED", 100.0),
        ("t2", "CONFIRMED", 100.0),
        ("t3", "PROBABLE", 9800.0),
    ]
    with db.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO entities (slug, kind, parent_slug, name, team, yaml_path,"
            " yaml_sha256, refreshed_at) VALUES ('owner-a','owner',NULL,'Owner A',"
            " 'Team A','owners/owner-a.yaml','abc','2024-01-01T00:00:00Z')"
        )
        for txn, status, amt in rows:
            conn.execute(
                "INSERT INTO donations (transaction_id, entity_slug, entity_kind, status,"
                " status_reason, signals_matched, contributor_name_raw,"
                " recipient_committee_id, recipient_committee_name, recipient_party,"
                " amount, date, election_cycle, filing_id, raw_payload_path, ingested_at)"
                " VALUES (?,'owner-a','owner',?,'','[]','Owner A','C00000001','Cmte','REP',"
                " ?, '2024-01-15', 2024, '5000', 'data/raw/owner-a/x.json',"
                " '2024-01-15T00:00:00Z')",
                (txn, status, amt),
            )


@pytest.fixture
def patched_build(tmp_path, monkeypatch):
    db_path = tmp_path / "master.db"
    out_path = tmp_path / "data.json"
    prov_out = tmp_path / "provenance.json"
    prov_src = tmp_path / "PROVENANCE_LOG.md"
    prov_src.write_text("# PROVENANCE LOG\n\n## Entries\n\n", encoding="utf-8")
    (tmp_path / "raw").mkdir()
    (tmp_path / "snapshots").mkdir()

    from scripts import db, paths
    from mockup import build_data

    monkeypatch.setattr(db, "MASTER_DB", db_path)
    monkeypatch.setattr(paths, "MASTER_DB", db_path)
    monkeypatch.setattr(paths, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(paths, "SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_data, "DB_PATH", db_path)
    monkeypatch.setattr(build_data, "OUT_PATH", out_path)
    monkeypatch.setattr(build_data, "PROVENANCE_OUT", prov_out)
    monkeypatch.setattr(build_data, "PROVENANCE_SRC", prov_src)
    monkeypatch.setattr(build_data, "REPO_ROOT", tmp_path)
    return {"db_path": db_path, "out_path": out_path}


def _build(patched_build):
    from mockup import build_data

    _seed(patched_build["db_path"])
    build_data.main()
    return json.loads(patched_build["out_path"].read_text())


class TestOwnerDollarSplit:
    def test_owner_carries_amount_confirmed_and_probable(self, patched_build):
        o = _build(patched_build)["owners"]["owner-a"]
        assert o["amount_confirmed"] == 200.0
        assert o["amount_probable"] == 9800.0

    def test_dollar_share_diverges_from_the_record_share(self, patched_build):
        """The whole reason this field exists."""
        o = _build(patched_build)["owners"]["owner-a"]
        record_share = o["n_confirmed"] / o["n_total"]
        dollar_share = o["amount_confirmed"] / (o["amount_confirmed"] + o["amount_probable"])
        assert round(record_share, 2) == 0.67   # counts look reassuring
        assert round(dollar_share, 2) == 0.02   # dollars do not
        assert record_share > dollar_share


class TestLeagueDollarSplit:
    def test_league_carries_the_split(self, patched_build):
        L = _build(patched_build)["league"]
        assert L["amount_confirmed"] == 200.0
        assert L["amount_probable"] == 9800.0

    def test_league_split_sums_to_the_headline_total(self, patched_build):
        """If these ever drift apart, the hero would show a share of a
        different number than the one printed beside it."""
        L = _build(patched_build)["league"]
        assert L["amount_confirmed"] + L["amount_probable"] == pytest.approx(
            L["total_amount"]
        )

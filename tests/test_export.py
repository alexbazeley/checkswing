"""Household export + `cli household` view (Phase C).

Both surface the owner→related rollup. The contract under test: related-entity
dollars are rolled up under the owner but ALWAYS decomposable by entity_kind —
the owner total and the household total are reported separately, never silently
merged (VERIFICATION.md anti-pattern).
"""
from __future__ import annotations

import csv

import pytest

from scripts import db, export


def _row(txn, entity_slug, entity_kind, parent, status, amount, **ov):
    base = {
        "transaction_id": txn,
        "entity_slug": entity_slug,
        "entity_kind": entity_kind,
        "parent_owner_slug": parent,
        "status": status,
        "status_reason": "test",
        "signals_matched": "[]",
        "contributor_name_raw": "X",
        "contributor_employer_raw": "",
        "contributor_occupation_raw": "",
        "contributor_city": "Greenwich",
        "contributor_state": "CT",
        "contributor_zip": "06830",
        "recipient_committee_id": "C1",
        "recipient_committee_name": "Cmte",
        "recipient_candidate_id": "",
        "recipient_candidate_name": "",
        "recipient_party": "DEM",
        "recipient_office": None,
        "amount": amount,
        "date": "2024-01-15",
        "election_cycle": 2024,
        "report_type": None,
        "filing_id": "F1",
        "raw_payload_path": "data/raw/x/x.json",
        "ingested_at": "2026-06-02T00:00:00Z",
    }
    base.update(ov)
    return base


@pytest.fixture
def patched(tmp_path, monkeypatch):
    """Temp DB (Cohen household + a solo owner) with db.connect re-pointed at it.

    db.connect()'s default arg is bound at import; we wrap it so every no-arg
    call in export/cli hits the temp DB. DONATIONS_DIR is redirected to tmp.
    """
    from contextlib import contextmanager

    p = tmp_path / "m.db"
    db.init(p)
    real = db.connect  # capture the genuine contextmanager before patching
    with real(p) as conn:
        # Cohen household: owner (CONFIRMED+PROBABLE) + spouse (CONFIRMED).
        db.insert_donation(conn, _row("O1", "cohen-steven", "owner", None, "CONFIRMED", 1000))
        db.insert_donation(conn, _row("O2", "cohen-steven", "owner", None, "PROBABLE", 500))
        db.insert_donation(conn, _row("S1", "cohen-alexandra", "spouse", "cohen-steven", "CONFIRMED", 2000))
        # Solo owner, no related entities.
        db.insert_donation(conn, _row("D1", "crane-jim", "owner", None, "CONFIRMED", 300))
        # UNCERTAIN must never appear in the rollup.
        db.insert_donation(conn, _row("U1", "cohen-steven", "owner", None, "UNCERTAIN", 9999))

    @contextmanager
    def _c(*a, **k):
        with real(p) as conn:
            yield conn

    monkeypatch.setattr(db, "connect", _c)
    monkeypatch.setattr(export, "DONATIONS_DIR", tmp_path / "donations")
    return p


class TestExportHousehold:
    def _read(self, tmp_path):
        path = tmp_path / "donations" / "_aggregate" / "by_household.csv"
        with path.open() as f:
            return list(csv.DictReader(f))

    def test_rolls_related_under_owner(self, patched, tmp_path):
        export.export_household()
        rows = self._read(tmp_path)
        cohen = [r for r in rows if r["household_slug"] == "cohen-steven"]
        slugs = {r["entity_slug"] for r in cohen}
        assert slugs == {"cohen-steven", "cohen-alexandra"}
        # Spouse row is parented to the owner household but keeps its own kind.
        spouse = [r for r in cohen if r["entity_slug"] == "cohen-alexandra"][0]
        assert spouse["entity_kind"] == "spouse"
        assert spouse["household_slug"] == "cohen-steven"

    def test_entity_kind_always_present_no_silent_merge(self, patched, tmp_path):
        export.export_household()
        rows = self._read(tmp_path)
        assert all(r["entity_kind"] for r in rows)

    def test_excludes_uncertain(self, patched, tmp_path):
        export.export_household()
        rows = self._read(tmp_path)
        # The $9999 UNCERTAIN row must not appear anywhere.
        assert all(float(r["total_amount"]) != 9999 for r in rows)

    def test_household_total_decomposable(self, patched, tmp_path):
        export.export_household()
        rows = self._read(tmp_path)
        cohen = [r for r in rows if r["household_slug"] == "cohen-steven"]
        owner = sum(float(r["total_amount"]) for r in cohen if r["entity_kind"] == "owner")
        spouse = sum(float(r["total_amount"]) for r in cohen if r["entity_kind"] == "spouse")
        assert owner == 1500.0          # 1000 CONFIRMED + 500 PROBABLE
        assert spouse == 2000.0
        assert owner + spouse == 3500.0  # the household total

    def test_solo_owner_is_own_household(self, patched, tmp_path):
        export.export_household()
        rows = self._read(tmp_path)
        crane = [r for r in rows if r["household_slug"] == "crane-jim"]
        assert len(crane) == 1
        assert crane[0]["entity_slug"] == "crane-jim"
        assert crane[0]["entity_kind"] == "owner"


class TestHouseholdCli:
    def _run(self, args):
        from click.testing import CliRunner

        from scripts.cli import cli

        return CliRunner().invoke(cli, args)

    def _seed_entities(self, p):
        # `household` checks the entities table to confirm SLUG is an owner.
        with db.connect(p) as conn:
            conn.execute(
                "INSERT INTO entities (slug, kind, parent_slug, name, yaml_path, yaml_sha256, refreshed_at) "
                "VALUES ('cohen-steven','owner',NULL,'Steven Cohen','owners/cohen-steven.yaml','x','t')"
            )

    def test_reports_owner_and_household_totals(self, patched, tmp_path):
        self._seed_entities(patched)
        res = self._run(["household", "cohen-steven", "--json"])
        assert res.exit_code == 0, res.output
        import json
        data = json.loads(res.output)
        assert data["owner_total"] == 1500.0
        assert data["related_total"] == 2000.0
        assert data["household_total"] == 3500.0
        kinds = {e["entity_kind"] for e in data["by_entity"]}
        assert "spouse" in kinds and "owner" in kinds

    def test_rejects_non_owner_slug(self, patched, tmp_path):
        res = self._run(["household", "cohen-alexandra", "--json"])
        assert res.exit_code == 1

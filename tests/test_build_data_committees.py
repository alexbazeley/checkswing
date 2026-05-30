"""Tests for the committees → recipients[] / committee_scale join in build_data.py.

This is a regression contract: the dashboard's renderCommittee assumes a
specific shape, so the build_data.py output has to keep producing it (or the
UI breaks silently in production). We exercise the join by populating a
temp master.db with a couple of donations and a committee row, running
build_data.main(), and inspecting the resulting mockup/data.json shape.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path

import pytest


def _seed_donations(db_path: Path) -> None:
    """Insert two donations to give recipients[] something to aggregate."""
    from scripts import db
    db.init(db_path)
    with db.connect(db_path) as conn:
        # Entity + donation rows
        conn.execute(
            """
            INSERT INTO entities (slug, kind, parent_slug, name, team, yaml_path, yaml_sha256, refreshed_at)
            VALUES ('owner-a', 'owner', NULL, 'Owner A', 'Team A', 'owners/owner-a.yaml', 'abc', '2024-01-01T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO donations
              (transaction_id, entity_slug, entity_kind, status, status_reason,
               signals_matched, contributor_name_raw, recipient_committee_id,
               recipient_committee_name, recipient_party,
               amount, date, election_cycle, filing_id,
               raw_payload_path, ingested_at)
            VALUES
              ('txn1', 'owner-a', 'owner', 'CONFIRMED', '',
               '[]', 'Owner A', 'C00000001', 'Cmte One (filer-typed)', 'REP',
               1000, '2024-01-15', 2024, '5000',
               'data/raw/owner-a/x.json', '2024-01-15T00:00:00Z')
            """
        )


def _seed_committee(db_path: Path, *, with_external_link=False) -> None:
    from scripts import db
    db.init(db_path)
    with db.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO committees (
                committee_id, name, designation, designation_label,
                committee_type, committee_type_label, party, organization_type,
                treasurer_name, city, state, first_file_date, last_file_date,
                is_terminated, cycles, candidate_ids,
                external_link, external_link_label, external_link_source,
                raw_payload_path, fetched_at, refreshed_at
            ) VALUES (
                'C00000001', 'ACME PAC (FEC canonical)', 'O', 'Super PAC',
                'O', 'Independent Expenditure-Only', 'REP', 'C',
                'Jane Doe', 'Washington', 'DC', '2014-05-01', '2024-10-15',
                0, '[2014, 2016, 2018, 2020, 2022, 2024]', '[]',
                ?, ?, ?,
                'data/raw/_committees/C00000001/x.json', '2024-10-15T00:00:00Z',
                '2024-10-15T00:00:00Z'
            )
            """,
            (
                "https://en.wikipedia.org/wiki/ACME_PAC" if with_external_link else None,
                "Wikipedia" if with_external_link else None,
                "manual; added by a maintainer 2025" if with_external_link else None,
            ),
        )
        conn.execute(
            """
            INSERT INTO committee_totals
              (committee_id, cycle, receipts, disbursements, cash_on_hand_end_period,
               coverage_start_date, coverage_end_date,
               raw_payload_path, fetched_at)
            VALUES
              ('C00000001', 2024, 5000000.0, 4800000.0, 200000.0,
               '2023-01-01', '2024-12-31',
               'data/raw/_committees/C00000001/x.json', '2024-10-15T00:00:00Z'),
              ('C00000001', 2022, 2000000.0, 1900000.0, 100000.0,
               '2021-01-01', '2022-12-31',
               'data/raw/_committees/C00000001/x.json', '2024-10-15T00:00:00Z')
            """
        )


@pytest.fixture
def patched_build(tmp_path, monkeypatch):
    """Re-roots master.db, data.json, and provenance into a temp dir for build_data."""
    db_path = tmp_path / "master.db"
    out_path = tmp_path / "data.json"
    prov_out = tmp_path / "provenance.json"
    prov_src = tmp_path / "PROVENANCE_LOG.md"
    prov_src.write_text("# PROVENANCE LOG\n\n## Entries\n\n", encoding="utf-8")

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()

    # Patch BOTH the build_data module and the paths module
    from scripts import db, paths
    from mockup import build_data

    monkeypatch.setattr(db, "MASTER_DB", db_path)
    monkeypatch.setattr(paths, "MASTER_DB", db_path)
    monkeypatch.setattr(paths, "RAW_DIR", raw_dir)
    monkeypatch.setattr(paths, "SNAPSHOTS_DIR", snap_dir)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_data, "DB_PATH", db_path)
    monkeypatch.setattr(build_data, "OUT_PATH", out_path)
    monkeypatch.setattr(build_data, "PROVENANCE_OUT", prov_out)
    monkeypatch.setattr(build_data, "PROVENANCE_SRC", prov_src)
    monkeypatch.setattr(build_data, "REPO_ROOT", tmp_path)

    return {"db_path": db_path, "out_path": out_path}


def test_recipients_get_enrichment_fields_when_committees_present(patched_build):
    _seed_donations(patched_build["db_path"])
    _seed_committee(patched_build["db_path"])

    from mockup import build_data
    build_data.main()

    data = json.loads(patched_build["out_path"].read_text())
    recipients = data["recipients"]
    assert len(recipients) == 1
    r = recipients[0]
    # Identity fields surfaced
    assert r["committee_id"] == "C00000001"
    assert r["committee"] == "ACME PAC (FEC canonical)"  # FEC name wins over donation name
    assert r["designation_label"] == "Super PAC"
    assert r["committee_type_label"] == "Independent Expenditure-Only"
    assert r["treasurer_name"] == "Jane Doe"
    assert r["city"] == "Washington"
    assert r["state_short"] == "DC"
    assert r["first_file_date"] == "2014-05-01"
    assert r["is_terminated"] is False
    # External link should be absent when DB row has none
    assert "external_link" not in r


def test_external_link_surfaces_when_set(patched_build):
    _seed_donations(patched_build["db_path"])
    _seed_committee(patched_build["db_path"], with_external_link=True)

    from mockup import build_data
    build_data.main()

    data = json.loads(patched_build["out_path"].read_text())
    r = data["recipients"][0]
    assert r["external_link"] == "https://en.wikipedia.org/wiki/ACME_PAC"
    assert r["external_link_label"] == "Wikipedia"


def test_committee_scale_block_emitted_with_cycles(patched_build):
    _seed_donations(patched_build["db_path"])
    _seed_committee(patched_build["db_path"])

    from mockup import build_data
    build_data.main()

    data = json.loads(patched_build["out_path"].read_text())
    scale = data["committee_scale"]
    assert "C00000001" in scale
    cycles = scale["C00000001"]
    assert len(cycles) == 2
    cycle_2024 = next(c for c in cycles if c["cycle"] == 2024)
    assert cycle_2024["receipts"] == 5000000.0
    assert cycle_2024["disbursements"] == 4800000.0
    assert cycle_2024["cash_on_hand_end_period"] == 200000.0


def test_legacy_render_when_committees_empty(patched_build):
    _seed_donations(patched_build["db_path"])
    # No _seed_committee() — recipients should still render the legacy shape

    from mockup import build_data
    build_data.main()

    data = json.loads(patched_build["out_path"].read_text())
    recipients = data["recipients"]
    assert len(recipients) == 1
    r = recipients[0]
    # Legacy fields preserved
    assert r["committee_id"] == "C00000001"
    assert r["committee"] == "Cmte One (filer-typed)"
    assert r["total_amount"] == 1000
    # No enrichment fields injected
    assert "designation_label" not in r
    assert "treasurer_name" not in r
    # Scale block is empty
    assert data["committee_scale"] == {}
    # Beneficiaries map is also empty
    assert data["committee_beneficiaries"] == {}


def _seed_beneficiaries(db_path: Path) -> None:
    """Seed beneficiaries for the same C00000001 committee across 2 cycles."""
    from scripts import db
    with db.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO committee_disbursements_by_recipient
              (committee_id, cycle, recipient_id, recipient_kind,
               recipient_name, recipient_party, recipient_office,
               total_amount, n_transactions,
               raw_payload_path, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'x', '2024-10-15T00:00:00Z')
            """,
            [
                ("C00000001", 2024, "H24A", "candidate", "JANE CANDIDATE",
                 "REP", "S", 50000.0, 3),
                ("C00000001", 2024, "C00099999", "committee", "ALLY SUPER PAC",
                 "REP", None, 25000.0, 1),
                ("C00000001", 2022, "H22A", "candidate", "JOE CANDIDATE",
                 "DEM", "H", 10000.0, 2),
            ],
        )


def test_committee_beneficiaries_map_emitted_with_cycles_and_top_25(patched_build):
    _seed_donations(patched_build["db_path"])
    _seed_committee(patched_build["db_path"])
    _seed_beneficiaries(patched_build["db_path"])

    from mockup import build_data
    build_data.main()

    data = json.loads(patched_build["out_path"].read_text())
    bene = data["committee_beneficiaries"]
    assert "C00000001" in bene
    by_cycle = bene["C00000001"]
    # JSON keys are strings (matches committee_scale / cycle_dollars convention).
    assert set(by_cycle.keys()) == {"2024", "2022"}
    cycle_2024 = by_cycle["2024"]
    assert len(cycle_2024) == 2
    # Sorted desc by total_amount.
    assert cycle_2024[0]["recipient_id"] == "H24A"
    assert cycle_2024[0]["total_amount"] == 50000.0
    assert cycle_2024[0]["recipient_kind"] == "candidate"
    assert cycle_2024[0]["recipient_party"] == "REP"
    assert cycle_2024[1]["recipient_id"] == "C00099999"
    assert cycle_2024[1]["recipient_kind"] == "committee"


def test_committee_beneficiaries_normalizes_party_codes(patched_build):
    """FEC's party strings are inconsistent — DEM/DEMOCRAT/DEMOCRATIC all
    represent the same thing. build_data normalizes them so the UI's chip
    rendering doesn't have to."""
    _seed_donations(patched_build["db_path"])
    _seed_committee(patched_build["db_path"])
    from scripts import db
    with db.connect(patched_build["db_path"]) as conn:
        conn.execute(
            """
            INSERT INTO committee_disbursements_by_recipient
              (committee_id, cycle, recipient_id, recipient_kind,
               recipient_name, recipient_party, total_amount,
               raw_payload_path, fetched_at)
            VALUES ('C00000001', 2024, 'H24A', 'candidate', 'X',
                    'DEMOCRAT', 100.0, 'x', '2024-10-15T00:00:00Z')
            """
        )

    from mockup import build_data
    build_data.main()

    data = json.loads(patched_build["out_path"].read_text())
    b = data["committee_beneficiaries"]["C00000001"]["2024"][0]
    assert b["recipient_party"] == "DEM"


def test_committee_beneficiaries_empty_when_pre_v5_db(patched_build):
    """An archive that's not yet been ingested for beneficiaries should still
    render — the map is just empty."""
    _seed_donations(patched_build["db_path"])
    _seed_committee(patched_build["db_path"])
    # Intentionally do NOT call _seed_beneficiaries.

    from mockup import build_data
    build_data.main()

    data = json.loads(patched_build["out_path"].read_text())
    assert data["committee_beneficiaries"] == {}

"""Tests for mockup/build_state_data.py — the Phase-4 state dashboard payload."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from scripts import state_db

REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_state_data", REPO_ROOT / "mockup" / "build_state_data.py"
)
build_state_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_state_data)


def _seed(db_path: Path) -> None:
    state_db.init(db_path)
    with state_db.connect(db_path) as conn:
        # (status, amount, recipient_name, recipient_filer_id) — same-named recipient
        # shares a filer id, as a real portal would.
        for i, (status, amt, recip, fid) in enumerate([
            ("CONFIRMED", 5000.0, "No on 30", "C_NO30"),
            ("CONFIRMED", 2000.0, "No on 30", "C_NO30"),
            ("PROBABLE", 500.0, "Some Assembly Cmte", "C_ASM"),
        ]):
            txn = state_db.compose_state_txn_id(
                jurisdiction="CA", source="CAL-ACCESS", source_filing_id=f"F{i}", source_tran_id=f"T{i}"
            )
            state_db.insert_state_donation(conn, {
                "state_txn_id": txn, "jurisdiction": "CA", "source": "CAL-ACCESS",
                "source_tran_id": f"T{i}", "source_filing_id": f"F{i}", "discovery_source": None,
                "entity_slug": "fisher-john", "entity_kind": "owner", "parent_owner_slug": None,
                "status": status, "status_reason": "x", "signals_matched": "[]",
                "contributor_name_raw": "Fisher, John J.", "contributor_employer_raw": "Pisces Inc",
                "contributor_occupation_raw": "Investor", "contributor_city": "San Francisco",
                "contributor_state": "CA", "contributor_zip": "94111",
                "recipient_filer_id": fid, "recipient_name": recip, "recipient_type": "committee",
                "recipient_party": None, "recipient_office": None,
                "amount": amt, "date": f"2022-0{i+1}-01", "election_cycle": 2022, "report_type": None,
                "raw_payload_path": "data/raw/state/ca/x.csv", "ingested_at": "2026-06-04T00:00:00Z",
            })


def test_build_produces_expected_shape(tmp_path):
    db = tmp_path / "state.db"
    out = tmp_path / "state_data.json"
    _seed(db)
    build_state_data.main(db_path=db, out_path=out)
    d = json.loads(out.read_text())

    assert d["n_donations"] == 3
    assert len(d["donations"]) == 3
    # Jurisdiction rollup
    assert len(d["jurisdictions"]) == 1
    ca = d["jurisdictions"][0]
    assert ca["code"] == "CA" and ca["n_confirmed"] == 2 and ca["n_probable"] == 1
    assert ca["total_amount"] == 7500.0
    # Owner rollup + top recipients (No on 30 = 7000 aggregated, leads)
    o = d["owners"]["fisher-john"]
    assert o["n_confirmed"] == 2 and o["n_probable"] == 1
    assert o["total_amount"] == 7500.0
    assert o["top_recipients"][0]["recipient"] == "No on 30"
    assert o["top_recipients"][0]["amount"] == 7000.0
    # Real-dollar mirror present (for the site's $/$↑ toggle)
    assert all("amount_2026" in dn for dn in d["donations"])
    assert "total_amount_2026" in o
    # Enriched owner rollup (richer per-owner table / drawer)
    assert o["n_recipients"] == 2  # "No on 30" + "Some Assembly Cmte"
    assert o["cycles"] == [2022]


def test_donations_carry_provenance_and_filing_links(tmp_path):
    """Each donation must expose the drawer's provenance fields + a best-effort
    official-source link built per portal (CAL-ACCESS → filed PDF)."""
    db = tmp_path / "state.db"
    out = tmp_path / "state_data.json"
    _seed(db)
    build_state_data.main(db_path=db, out_path=out)
    d = json.loads(out.read_text())

    dn = d["donations"][0]
    for k in ("id", "status_reason", "signals", "recipient_filer_id", "recipient_key",
              "zip", "source_tran_id", "ingested_at", "filing_url"):
        assert k in dn, f"missing {k}"
    assert dn["id"].startswith("CA:CAL-ACCESS:")
    assert isinstance(dn["signals"], list)
    # CAL-ACCESS filing link points at the filed PDF, keyed on source_filing_id.
    assert dn["filing_url"] == (
        f"https://cal-access.sos.ca.gov/PDFGen/pdfgen.prg?filingid={dn['source_filing_id']}&amendid=0"
    )


def test_source_links_per_portal():
    """Best-effort, per-portal, never fabricated."""
    f = build_state_data._source_links
    assert f("CAL-ACCESS", "12345", "T1") == (
        "https://cal-access.sos.ca.gov/PDFGen/pdfgen.prg?filingid=12345&amendid=0", None)
    ny_filing, ny_dataset = f("NYSBOE", "filer99", "TRANS7")
    assert ny_filing == "https://data.ny.gov/resource/4j2b-6a2j.json?trans_number=TRANS7"
    assert ny_dataset == "https://data.ny.gov/d/4j2b-6a2j"
    # PA has no verified per-record deep link; cite the official bulk-export
    # dataset page (filing_url stays None — never fabricated).
    pa_filing, pa_dataset = f("PA-DOS", "x", "y")
    assert pa_filing is None
    assert pa_dataset == (
        "https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/campaign-finance-data")
    assert f("CAL-ACCESS", None, "T1") == (None, None)


def test_recipients_rollup(tmp_path):
    """recipients[] rolls donations up by recipient, mirroring federal recipients[]."""
    db = tmp_path / "state.db"
    out = tmp_path / "state_data.json"
    _seed(db)
    build_state_data.main(db_path=db, out_path=out)
    d = json.loads(out.read_text())

    assert "recipients" in d
    by_name = {r["name"]: r for r in d["recipients"]}
    assert "No on 30" in by_name
    no30 = by_name["No on 30"]
    assert no30["n_donations"] == 2 and no30["total_amount"] == 7000.0
    assert no30["n_owners"] == 1
    assert no30["top_donors"][0]["slug"] == "fisher-john"
    # Sorted by total desc — "No on 30" (7000) leads "Some Assembly Cmte" (500)
    assert d["recipients"][0]["name"] == "No on 30"


def test_build_handles_missing_db(tmp_path):
    out = tmp_path / "state_data.json"
    build_state_data.main(db_path=tmp_path / "nope.db", out_path=out)
    d = json.loads(out.read_text())
    assert d.get("empty") is True

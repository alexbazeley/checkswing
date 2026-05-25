"""FEC committee enrichment fetcher.

Wraps the existing scripts/fetch_fec.py:FECClient (throttle, retry, raw-payload
envelope) and adds two endpoints used by the recipient-page enrichment:

  /committee/{committee_id}/         — identity (designation, type, treasurer, …)
  /committee/{committee_id}/totals/  — per-cycle scale (receipts, disbursements, …)

Raw payloads land at:

    data/raw/_committees/<committee_id>/<UTC>__<endpoint-suffix>.json

Leading-underscore directory name distinguishes committee scope from per-owner
slug directories under data/raw/. CLAUDE.md §1.4: raw payloads are ground
truth; the DB is a derivative.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fetch_fec import FECClient, _utc_now_filename, _utc_now_iso
from .paths import RAW_DIR, relpath


COMMITTEE_DETAIL_ENDPOINT = "/committee/{committee_id}/"
COMMITTEE_TOTALS_ENDPOINT = "/committee/{committee_id}/totals/"

# Subdirectory under data/raw/ for committee payloads.
COMMITTEES_RAW_SUBDIR = "_committees"


def committee_raw_dir(committee_id: str) -> Path:
    p = RAW_DIR / COMMITTEES_RAW_SUBDIR / committee_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _persist_committee_raw(
    committee_id: str,
    endpoint_suffix: str,
    params: dict[str, Any],
    payload: dict[str, Any],
) -> Path:
    """Write one FEC response to a timestamped JSON file under the committee's raw dir.

    Envelope shape matches scripts/fetch_fec.py:_persist_raw — _meta + response.
    """
    raw_path = committee_raw_dir(committee_id) / f"{_utc_now_filename()}__{endpoint_suffix}.json"
    envelope = {
        "_meta": {
            "endpoint": f"/committee/{committee_id}/{endpoint_suffix}/".replace("//", "/"),
            "params": {k: v for k, v in params.items() if k != "api_key"},
            "committee_id": committee_id,
            "fetched_at": _utc_now_iso(),
        },
        "response": payload,
    }
    raw_path.write_text(json.dumps(envelope, indent=2, default=str))
    return raw_path


def fetch_committee_detail(
    client: FECClient, committee_id: str
) -> tuple[dict[str, Any], Path]:
    """Fetch the committee record from /committee/<id>/.

    FEC returns `results: [<one row>]` for this endpoint. Returns the row dict
    and the raw payload path. Raises if FEC returns zero results — the caller
    decides how to handle a missing committee.
    """
    endpoint = COMMITTEE_DETAIL_ENDPOINT.format(committee_id=committee_id)
    payload = client._request(endpoint, {})
    raw_path = _persist_committee_raw(committee_id, "committee_detail", {}, payload)
    results = payload.get("results") or []
    if not results:
        raise RuntimeError(f"FEC returned no committee record for {committee_id}")
    return results[0], raw_path


def fetch_committee_totals(
    client: FECClient, committee_id: str
) -> tuple[list[dict[str, Any]], Path]:
    """Fetch per-cycle totals from /committee/<id>/totals/.

    Returns the list of cycle rows and the raw payload path. FEC paginates this
    endpoint but in practice ≤14 cycles fits one page for any committee in our
    archive — we read page 1 only. If a committee ever exceeds one page, the
    raw payload still records the truth; we just won't surface the older cycles.
    """
    endpoint = COMMITTEE_TOTALS_ENDPOINT.format(committee_id=committee_id)
    # Per_page=100 is FEC's max; covers any realistic per-committee history.
    params = {"per_page": 100, "sort": "-cycle"}
    payload = client._request(endpoint, params)
    raw_path = _persist_committee_raw(committee_id, "committee_totals", params, payload)
    results = payload.get("results") or []
    return results, raw_path


# ─── Field extractors ────────────────────────────────────────────────────────


def parse_committee_detail(row: dict[str, Any]) -> dict[str, Any]:
    """Project FEC's committee row into the columns of the committees table.

    FEC field names follow OpenFEC's `/committee/<id>/` schema. Where FEC names
    differ from our column names, we map them here (e.g. cash_on_hand fields
    live on /totals/, not on /committee/). Unknown/missing fields default None.

    The `cycles` field on FEC's response is a list of integers — we store it
    JSON-encoded for forward-compatibility (SQLite has no array type).
    """
    candidate_ids = row.get("candidate_ids") or []
    if not isinstance(candidate_ids, list):
        candidate_ids = [candidate_ids]
    cycles = row.get("cycles") or []
    if not isinstance(cycles, list):
        cycles = [cycles]

    last_f1_date = row.get("last_f1_date")
    last_file_date = row.get("last_file_date")
    first_file_date = row.get("first_file_date")
    return {
        "committee_id": row.get("committee_id"),
        "name": row.get("name") or row.get("committee_name") or "",
        "designation": row.get("designation"),
        "designation_label": row.get("designation_full"),
        "committee_type": row.get("committee_type"),
        "committee_type_label": row.get("committee_type_full"),
        "party": row.get("party"),
        "party_full": row.get("party_full"),
        "organization_type": row.get("organization_type") or row.get("organization_type_full"),
        "affiliated_committee_name": row.get("affiliated_committee_name"),
        "candidate_ids": json.dumps(candidate_ids),
        "treasurer_name": row.get("treasurer_name"),
        "custodian_name": row.get("custodian_name_full") or row.get("custodian_name_1"),
        "city": row.get("city"),
        "state": row.get("state"),
        "zip": row.get("zip"),
        "filing_frequency": row.get("filing_frequency"),
        "first_file_date": first_file_date,
        "last_file_date": last_file_date,
        "last_f1_date": last_f1_date,
        # FEC reports termination via a separate /committee/<id>/history/ endpoint;
        # the closest signal on /committee/<id>/ is `last_file_date` being old
        # plus no recent f3/f3x. We capture the explicit terminated marker if
        # FEC surfaces one; otherwise default 0.
        "is_terminated": 1 if row.get("is_terminated") else 0,
        "cycles": json.dumps(sorted({int(c) for c in cycles if c is not None})),
    }


def parse_committee_totals_row(committee_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """Project one cycle of FEC totals into the committee_totals schema."""
    cycle = row.get("cycle")
    if cycle is None:
        # Fall back to coverage_end_date year if FEC omits cycle (rare).
        end = row.get("coverage_end_date") or ""
        try:
            yr = int(str(end)[:4])
            cycle = yr if yr % 2 == 0 else yr + 1
        except (TypeError, ValueError):
            cycle = None
    return {
        "committee_id": committee_id,
        "cycle": cycle,
        "receipts": row.get("receipts"),
        "disbursements": row.get("disbursements"),
        "cash_on_hand_end_period": row.get("cash_on_hand_end_period"),
        "individual_contributions": row.get("individual_contributions"),
        "other_political_committee_contributions": row.get(
            "other_political_committee_contributions"
        ),
        "independent_expenditures": row.get("independent_expenditures"),
        "coverage_start_date": row.get("coverage_start_date"),
        "coverage_end_date": row.get("coverage_end_date"),
    }

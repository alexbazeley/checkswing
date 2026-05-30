"""FEC committee beneficiaries fetcher.

For a given committee (the spender — typically a PAC an MLB owner gave to),
fetch the aggregated top-N recipients per cycle from OpenFEC's Schedule B
by_recipient endpoint:

  /schedules/schedule_b/by_recipient/?committee_id=<id>&cycle=<c>

FEC returns one row per (committee_id, cycle, recipient_id) — the total
amount the committee disbursed to that recipient in that cycle plus the
transaction count. We persist the raw envelope per cycle, then project each
row into the columns of committee_disbursements_by_recipient.

GOVERNANCE.md §1.4: raw payloads land under data/raw/_committee_disbursements/
BEFORE parsing. Underscore-prefixed dir matches the `_committees` and
`_filings` conventions.

GOVERNANCE.md §6: this is factual recipient metadata (name, amount). It is NEVER
editorial framing of what the committee's spending "means." Cross-referencing
to legislation / votes / policy outcomes is Phase 3.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fetch_fec import FECClient, _utc_now_filename, _utc_now_iso
from .paths import RAW_DIR


# Canonical FEC endpoint. The /committee/<id>/schedules/schedule_b/by_recipient/
# convenience path also exists, but the cycle-filtered query form is what
# the official OpenFEC OpenAPI documents as the primary entry point and it
# composes cleanly with other filters if we ever extend.
BY_RECIPIENT_ENDPOINT = "/schedules/schedule_b/by_recipient/"

# Subdirectory under data/raw/ for per-committee beneficiary payloads.
BENEFICIARIES_RAW_SUBDIR = "_committee_disbursements"

# Top-N recipients per (committee, cycle) we keep. FEC sorts the by_recipient
# results by total amount descending; 200 covers virtually all meaningful
# spending for any committee in this archive (the long tail is small refunds
# and de-minimis transfers).
DEFAULT_TOP_N = 200

# FEC max per_page on /schedules/schedule_b/by_recipient/.
PER_PAGE = 100


def beneficiaries_raw_dir(committee_id: str) -> Path:
    p = RAW_DIR / BENEFICIARIES_RAW_SUBDIR / committee_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _persist_beneficiaries_raw(
    committee_id: str,
    cycle: int,
    page: int,
    params: dict[str, Any],
    payload: dict[str, Any],
) -> Path:
    """Write one FEC by_recipient page to a timestamped JSON file.

    Filename convention: <UTC>__by_recipient_cycle_<N>_p<page>.json.
    The page suffix is only used when pagination runs longer than one page;
    page 1 is the common case and the only one we strictly need to keep
    rebuildable (top_n=200 < per_page * 2 always covers the meaningful tail).
    """
    fname = f"{_utc_now_filename()}__by_recipient_cycle_{cycle}_p{page}.json"
    raw_path = beneficiaries_raw_dir(committee_id) / fname
    envelope = {
        "_meta": {
            "endpoint": BY_RECIPIENT_ENDPOINT,
            "params": {k: v for k, v in params.items() if k != "api_key"},
            "committee_id": committee_id,
            "cycle": cycle,
            "page": page,
            "fetched_at": _utc_now_iso(),
        },
        "response": payload,
    }
    raw_path.write_text(json.dumps(envelope, indent=2, default=str))
    return raw_path


def fetch_by_recipient(
    client: FECClient,
    committee_id: str,
    cycle: int,
    top_n: int = DEFAULT_TOP_N,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Fetch up to top_n recipients of a committee's Schedule B disbursements for one cycle.

    Returns (recipient_rows, raw_payload_paths). Recipient rows are FEC's
    aggregate-by-recipient payloads, ordered as FEC returned them
    (top-amount-first by default). The list is capped at top_n.

    Pagination: FEC paginates by last_indexes. We stop when we've collected
    top_n rows or FEC reports no more pages.
    """
    all_rows: list[dict[str, Any]] = []
    raw_paths: list[Path] = []
    params: dict[str, Any] = {
        "committee_id": committee_id,
        "cycle": cycle,
        "per_page": PER_PAGE,
        "sort": "-total",
    }
    page = 0
    while len(all_rows) < top_n:
        page += 1
        payload = client._request(BY_RECIPIENT_ENDPOINT, params)
        raw_path = _persist_beneficiaries_raw(
            committee_id, cycle, page, params, payload
        )
        raw_paths.append(raw_path)
        results = payload.get("results") or []
        all_rows.extend(results)
        pagination = payload.get("pagination") or {}
        last_indexes = pagination.get("last_indexes")
        if not last_indexes or not results:
            break
        params = {**params, **last_indexes}

    return all_rows[:top_n], raw_paths


def _classify_recipient(row: dict[str, Any]) -> tuple[str | None, str]:
    """Resolve (recipient_id, recipient_kind) from a Schedule B by_recipient row.

    FEC's by_recipient response has both candidate_id and recipient_committee_id
    fields. A candidate ID indicates the funds went toward a candidate's
    campaign (kind='candidate'); otherwise the recipient is treated as another
    committee (kind='committee'), keyed by recipient_committee_id. When neither
    is present, fall back to the recipient name with kind='committee' — this is
    rare (de-minimis refunds, miscellaneous transfers) and lets us still
    record the row uniquely under the PK.
    """
    cand = row.get("candidate_id")
    if cand:
        return str(cand), "candidate"
    cmte = row.get("recipient_committee_id")
    if cmte:
        return str(cmte), "committee"
    # Last-resort fallback so the row isn't silently dropped. The PK still
    # holds because the (committee, cycle, recipient_id) tuple stays unique
    # if FEC's name is consistent across re-fetches.
    name = row.get("recipient_name") or row.get("recipient_nm") or ""
    if name:
        return f"NAME:{name}", "committee"
    return None, "committee"


def parse_by_recipient_row(
    committee_id: str,
    cycle: int,
    row: dict[str, Any],
) -> dict[str, Any] | None:
    """Project one FEC by_recipient row into a committee_disbursements_by_recipient row.

    Returns None when the row has no recipient identifier at all — we'd have
    no stable PK to insert under. (GOVERNANCE.md §1.5 — idempotency requires a
    stable key.)
    """
    rid, rkind = _classify_recipient(row)
    if rid is None:
        return None

    # FEC names the recipient field inconsistently across endpoints; check
    # both common shapes.
    name = (
        row.get("recipient_name")
        or row.get("recipient_nm")
        or row.get("name")
    )
    total = row.get("total")
    if total is None:
        # Some FEC responses use total_amount; fall back to either it or 0
        # so the column's NOT NULL is satisfied. Zero-amount rows would be
        # filtered upstream in practice (sort=-total puts them last).
        total = row.get("total_amount") or 0.0
    return {
        "committee_id": committee_id,
        "cycle": int(cycle),
        "recipient_id": rid,
        "recipient_kind": rkind,
        "recipient_name": name,
        "recipient_party": row.get("recipient_party") or row.get("party"),
        "recipient_office": row.get("recipient_office") or row.get("office"),
        "total_amount": float(total),
        "n_transactions": row.get("count") or row.get("n_transactions"),
    }

"""FEC filing enrichment fetcher.

Wraps the existing scripts/fetch_fec.py:FECClient and adds one endpoint used
by the donation-card "Full filing PDF" link:

  /v1/filings/?file_number=<id>&file_number=<id>...

FEC supports passing multiple `file_number` params per request, returning up
to per_page filings in a single response. We batch at FILINGS_BATCH_SIZE per
request and paginate inside each batch if needed (rare — usually one page
per batch).

Raw payloads land at:

    data/raw/_filings/<UTC>__batch.json

(Underscore prefix matches the committees convention; filings aren't per-owner.)
CLAUDE.md §1.4: raw payloads are ground truth; the DB is a derivative.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .fetch_fec import FECClient, _utc_now_filename, _utc_now_iso
from .paths import RAW_DIR


FILINGS_ENDPOINT = "/filings/"
FILINGS_RAW_SUBDIR = "_filings"

# How many file_numbers to pass per request. FEC accepts multi-valued query
# params; per_page caps at 100. We stay at 50 to leave headroom for filings
# that have amendments (each amendment may show up as its own row in the
# response).
FILINGS_BATCH_SIZE = 50


def filings_raw_dir() -> Path:
    p = RAW_DIR / FILINGS_RAW_SUBDIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _persist_filings_raw(
    params: dict,
    payload: dict,
    batch_label: str,
) -> Path:
    """Write one FEC response to a timestamped JSON file under the filings raw dir."""
    raw_path = filings_raw_dir() / f"{_utc_now_filename()}__{batch_label}.json"
    envelope = {
        "_meta": {
            "endpoint": FILINGS_ENDPOINT,
            "params": {k: v for k, v in params.items() if k != "api_key"},
            "batch_label": batch_label,
            "fetched_at": _utc_now_iso(),
        },
        "response": payload,
    }
    raw_path.write_text(json.dumps(envelope, indent=2, default=str))
    return raw_path


def _batch(seq: list, n: int) -> Iterator[list]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def fetch_filings_batch(
    client: FECClient,
    file_numbers: list[str],
    batch_label: str | None = None,
) -> tuple[list[dict], list[Path]]:
    """Fetch a batch of filings by file_number. Returns (results, raw_payload_paths).

    FEC's /filings/ paginates by last_indexes; we walk pages until exhausted.
    Per-batch request count is typically 1 (per_page=100 covers 50 input ids
    with their amendments).
    """
    if not file_numbers:
        return [], []

    label = batch_label or f"filings_batch_{len(file_numbers)}"
    all_results: list[dict] = []
    raw_paths: list[Path] = []
    params: dict = {
        "file_number": list(file_numbers),
        "per_page": 100,
        "sort": "-receipt_date",
    }
    page = 0
    while True:
        page += 1
        payload = client._request(FILINGS_ENDPOINT, params)
        raw_path = _persist_filings_raw(params, payload, f"{label}_p{page}")
        raw_paths.append(raw_path)
        results = payload.get("results") or []
        all_results.extend(results)
        pagination = payload.get("pagination") or {}
        last_indexes = pagination.get("last_indexes")
        if not last_indexes or not results:
            break
        # Update params for the next page; preserve file_number list
        params = {**params, **last_indexes}
    return all_results, raw_paths


def parse_filing_row(row: dict) -> dict:
    """Project an FEC filing into the filings table column shape."""
    amendment_chain = row.get("amendment_chain") or []
    if not isinstance(amendment_chain, list):
        amendment_chain = [amendment_chain]
    return {
        "file_number": str(row["file_number"]) if row.get("file_number") is not None else None,
        "pdf_url": row.get("pdf_url"),
        "form_type": row.get("form_type"),
        "document_type": row.get("document_type"),
        "document_type_full": row.get("document_type_full"),
        "filed_date": row.get("filed_date"),
        "receipt_date": row.get("receipt_date"),
        "coverage_start_date": row.get("coverage_start_date"),
        "coverage_end_date": row.get("coverage_end_date"),
        "committee_id": row.get("committee_id"),
        "committee_name": row.get("committee_name"),
        "is_amended": 1 if row.get("is_amended") else 0,
        "amendment_chain": json.dumps(amendment_chain),
        "cycle": row.get("cycle") or row.get("election_year"),
    }

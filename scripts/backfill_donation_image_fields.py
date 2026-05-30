"""One-shot backfill for the v3 per-transaction FEC columns on donations.

Background: before the v3 schema bump, image_number/pdf_url/filing_form/
line_number/receipt_type_full/recipient_committee_type were re-derived from
raw payloads at build_data.py time. That broke whenever raw payloads weren't
locally available — most notably for donations ingested by GHA matrix runs,
whose runner-side data/raw/ filesystem is destroyed after the job. The v3
columns persist these fields on the donations row itself.

This script populates those columns for donation rows ingested before v3 by
scanning data/raw/<slug>/*.json for each affected owner. Rows whose raw
payload still exists locally get fully recovered; rows whose raw payload is
gone leave the columns NULL (same UX as today — "Image link not available").

Idempotent: skips rows where image_number is already populated.

GOVERNANCE.md §1.4 / §1.5: this script does not change the underlying truth, only
rehydrates a derived view of fields that have always been authoritatively
in raw payloads. Snapshots master.db before writing.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import db
from .paths import MASTER_DB, RAW_DIR


def _resolve_committee_type(record: dict) -> str | None:
    """Same precedence as scripts/ingest.py:_committee_type_of, duplicated to
    keep this script standalone."""
    top = record.get("recipient_committee_type")
    if top:
        return top
    cmt = record.get("committee")
    if isinstance(cmt, dict):
        return cmt.get("committee_type")
    return None


def _scan_owner_dir(slug_dir: Path) -> dict[str, dict]:
    """Walk every JSON payload under one owner's data/raw/<slug>/ and return a
    map from transaction_id → projected fields."""
    out: dict[str, dict] = {}
    for path in sorted(slug_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        results = (data.get("response") or {}).get("results") or []
        for r in results:
            txn = r.get("transaction_id")
            if not txn:
                continue
            image_number = r.get("image_number")
            out[str(txn)] = {
                "image_number": str(image_number) if image_number is not None else None,
                "pdf_url": r.get("pdf_url"),
                "filing_form": r.get("filing_form"),
                "line_number": (
                    str(r["line_number"]) if r.get("line_number") is not None else None
                ),
                "receipt_type_full": r.get("receipt_type_full"),
                "recipient_committee_type": _resolve_committee_type(r),
            }
    return out


def backfill(db_path: Path = MASTER_DB, raw_dir: Path = RAW_DIR) -> dict:
    """Scan local raw payloads per owner; UPDATE donation rows missing the v3
    image fields. Returns a summary dict."""
    db.init(db_path)

    summary: dict = {
        "db_path": str(db_path),
        "raw_dir": str(raw_dir),
        "owners_scanned": 0,
        "txn_index_size": 0,
        "rows_with_null_image_number": 0,
        "rows_updated": 0,
        "rows_unrecoverable": 0,  # row needs backfill but txn not found in any local raw payload
        "per_owner": {},
    }

    with db.connect(db_path) as conn:
        # Snapshot before writes. GOVERNANCE.md §1.6.
        snap = db.snapshot("backfill_donation_image_fields", db_path)
        summary["snapshot_path"] = str(snap) if snap else None

        # Which slugs have donation rows that still need backfill?
        slugs = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT entity_slug
                  FROM donations
                 WHERE image_number IS NULL
                   AND status IN ('CONFIRMED', 'PROBABLE')
                 ORDER BY entity_slug
                """
            )
        ]

        for slug in slugs:
            slug_dir = raw_dir / slug
            if not slug_dir.is_dir():
                summary["per_owner"][slug] = {
                    "txns_in_index": 0,
                    "rows_updated": 0,
                    "rows_unrecoverable": _count_null_image_for_slug(conn, slug),
                }
                summary["rows_unrecoverable"] += summary["per_owner"][slug]["rows_unrecoverable"]
                summary["owners_scanned"] += 1
                continue

            index = _scan_owner_dir(slug_dir)
            updated_here = 0
            unrecoverable_here = 0

            rows = list(
                conn.execute(
                    """
                    SELECT transaction_id FROM donations
                     WHERE entity_slug = ?
                       AND image_number IS NULL
                       AND status IN ('CONFIRMED', 'PROBABLE')
                    """,
                    (slug,),
                )
            )
            for row in rows:
                txn = row[0]
                fields = index.get(str(txn))
                if not fields:
                    unrecoverable_here += 1
                    continue
                conn.execute(
                    """
                    UPDATE donations
                       SET image_number = :image_number,
                           pdf_url = :pdf_url,
                           filing_form = :filing_form,
                           line_number = :line_number,
                           receipt_type_full = :receipt_type_full,
                           recipient_committee_type = :recipient_committee_type
                     WHERE transaction_id = :transaction_id
                    """,
                    {**fields, "transaction_id": txn},
                )
                updated_here += 1

            summary["owners_scanned"] += 1
            summary["txn_index_size"] += len(index)
            summary["rows_updated"] += updated_here
            summary["rows_unrecoverable"] += unrecoverable_here
            summary["per_owner"][slug] = {
                "txns_in_index": len(index),
                "rows_updated": updated_here,
                "rows_unrecoverable": unrecoverable_here,
            }

        summary["rows_with_null_image_number"] = conn.execute(
            "SELECT COUNT(*) FROM donations WHERE image_number IS NULL AND status IN ('CONFIRMED','PROBABLE')"
        ).fetchone()[0]

    return summary


def _count_null_image_for_slug(conn: sqlite3.Connection, slug: str) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM donations
         WHERE entity_slug = ?
           AND image_number IS NULL
           AND status IN ('CONFIRMED', 'PROBABLE')
        """,
        (slug,),
    ).fetchone()[0]

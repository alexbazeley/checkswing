"""One-shot backfill: stamp the documented sentinel on rows whose filing_id is
blank (CLAUDE.md §1.3, audit H3).

These are pre-2006 FEC paper filings that genuinely lack a file number. Rather
than leave a silent empty string, set filing_id = FEC-PRE2006-NOID so the
"no FEC file number" case is explicit and queryable. Real file-number recovery
is infeasible for these rows (there is no file_number to query FEC with, and
pdf_url is NULL), so the sentinel is the terminal state. Rows retain their
raw_payload_path, so traceability is unchanged.

GATED DATA OPERATION: snapshots master.db first and appends a PROVENANCE_LOG
entry (§1.10). It is NOT wired into any automated workflow — run it deliberately
(`python -m scripts.cli backfill-pre2006-filing-id`). Idempotent: a second run
finds no blank rows and does nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import db
from .ingest import SENTINEL_FILING_ID
from .paths import PROVENANCE_LOG


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _apply_sentinel(conn) -> int:
    """Set filing_id = SENTINEL_FILING_ID on every row with a blank filing_id.
    Returns the number of rows changed."""
    cur = conn.execute(
        "UPDATE donations SET filing_id = ? WHERE filing_id = ''",
        (SENTINEL_FILING_ID,),
    )
    return cur.rowcount


def backfill(db_path=None) -> dict:
    """Stamp the sentinel on blank-filing_id rows, snapshotting + logging first."""
    path = db_path or db.MASTER_DB
    db.init(path)

    with db.connect(path) as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM donations WHERE filing_id = ''"
        ).fetchone()[0]
    if before == 0:
        return {"updated": 0, "note": "no blank filing_id rows; nothing to do"}

    snap = db.snapshot("pre-filing-id-sentinel", path)
    with db.connect(path) as conn:
        sample = [
            r[0]
            for r in conn.execute(
                "SELECT transaction_id FROM donations WHERE filing_id = '' LIMIT 5"
            ).fetchall()
        ]
        updated = _apply_sentinel(conn)

    # PROVENANCE_LOG entry (§1.10 — no data change without a record).
    ts = _utc_now_iso()
    block = [
        f"\n### {ts[:10]} — BACKFILL — filing_id sentinel (H3)",
        "",
        f"- **rows_updated**: `{updated}`",
        f"- **sentinel**: `{SENTINEL_FILING_ID}`",
        f"- **snapshot_path**: `{snap}`",
        f"- **sample_txns**: `{sample}`",
        "- **note**: Pre-2006 paper filings with no FEC file number; the sentinel "
        "makes the gap explicit (CLAUDE.md §1.3). Rows retain raw_payload_path.",
        "",
    ]
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")

    return {
        "updated": updated,
        "snapshot_path": str(snap) if snap else None,
        "sample_txns": sample,
    }

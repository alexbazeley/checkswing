"""One-shot backfill for the v9 `sub_id` column on donations (§1.3).

`sub_id` is FEC's globally-unique record id — the authoritative identity that
distinguishes a genuine restatement from a cross-committee `transaction_id`
collision (transaction_id is filer-assigned and not unique across committees).
New rows store it at ingest; this rehydrates it for rows ingested before v9 by
scanning data/raw/<slug>/*.json. Rows whose raw payload is gone stay NULL (they
fall back to the transaction_id identity, which is safe — no live collisions).

Idempotent: skips rows that already have sub_id. Snapshots master.db first
(GOVERNANCE §1.6). This does not change any published total — sub_id is an
identity/provenance field, not a dollar field — so no CORRECTION entry is needed,
only the snapshot.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import db
from .paths import MASTER_DB, RAW_DIR


def _scan_owner_dir(slug_dir: Path) -> dict[str, str]:
    """transaction_id → sub_id from one owner's raw payloads."""
    out: dict[str, str] = {}
    for path in sorted(slug_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        for r in (data.get("response") or {}).get("results") or []:
            tid, sid = r.get("transaction_id"), r.get("sub_id")
            if tid is not None and sid is not None:
                out[str(tid)] = str(sid)
    return out


def backfill(db_path: Path = MASTER_DB, raw_dir: Path = RAW_DIR) -> dict:
    db.init(db_path)
    summary: dict = {"owners_scanned": 0, "rows_updated": 0, "rows_unrecoverable": 0, "per_owner": {}}
    with db.connect(db_path) as conn:
        snap = db.snapshot("backfill_subid", db_path)
        summary["snapshot_path"] = str(snap) if snap else None
        slugs = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT entity_slug FROM donations "
                "WHERE sub_id IS NULL AND status IN ('CONFIRMED','PROBABLE') ORDER BY entity_slug"
            )
        ]
        for slug in slugs:
            slug_dir = raw_dir / slug
            index = _scan_owner_dir(slug_dir) if slug_dir.is_dir() else {}
            updated = unrec = 0
            for (txn,) in conn.execute(
                "SELECT transaction_id FROM donations WHERE entity_slug = ? "
                "AND sub_id IS NULL AND status IN ('CONFIRMED','PROBABLE')",
                (slug,),
            ).fetchall():
                sid = index.get(str(txn))
                if sid is None:
                    unrec += 1
                    continue
                conn.execute(
                    "UPDATE donations SET sub_id = ? WHERE transaction_id = ?", (sid, txn)
                )
                updated += 1
            summary["owners_scanned"] += 1
            summary["rows_updated"] += updated
            summary["rows_unrecoverable"] += unrec
            summary["per_owner"][slug] = {"rows_updated": updated, "rows_unrecoverable": unrec}
    return summary

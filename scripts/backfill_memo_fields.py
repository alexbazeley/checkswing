"""One-shot backfill for the v8 earmark/conduit columns on donations.

Background: an earmarked contribution routed through a conduit (ActBlue/WinRed)
is reported twice in FEC Schedule A data — once by the conduit and once by the
ultimate recipient — under distinct transaction_ids. A naive SUM double-counts
it. FEC's own convention distinguishes the two legs via `is_individual` (the
conduit passthrough leg is is_individual=False); `memo_code`/`memo_text` are kept
for provenance. Before v8 none of these fields were stored, so both legs summed.

This script populates memo_code/memo_text/is_individual for donation rows
ingested before v8 by scanning data/raw/<slug>/*.json for each affected owner,
then recomputes the derived `counted` dedup flag (db.recompute_counted) for the
whole table. Rows whose raw payload still exists locally get is_individual
recovered; rows whose raw payload is gone leave the columns NULL and are treated
as countable (never silently dropped).

Idempotent: re-running only re-reads raw and re-derives counted; no row is ever
deleted (GOVERNANCE.md §1.10). Snapshots master.db and appends a PROVENANCE_LOG
entry before writing, because this changes published totals (§1.1 of
docs/IMPROVEMENT_PLAN_2026-07.md).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import db
from .paths import MASTER_DB, PROVENANCE_LOG, RAW_DIR
from .provenance import append_provenance


def _scan_owner_dir(slug_dir: Path) -> dict[str, dict]:
    """transaction_id → {memo_code, memo_text, is_individual} from one owner's raw."""
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
            ii = r.get("is_individual")
            out[str(txn)] = {
                "memo_code": r.get("memo_code") or None,
                "memo_text": r.get("memo_text") or None,
                "is_individual": int(ii) if ii is not None else None,
            }
    return out


def backfill(db_path: Path = MASTER_DB, raw_dir: Path = RAW_DIR) -> dict:
    """Scan local raw payloads per owner; UPDATE the v8 columns; recompute counted.

    Returns a summary dict."""
    db.init(db_path)

    summary: dict = {
        "db_path": str(db_path),
        "raw_dir": str(raw_dir),
        "owners_scanned": 0,
        "rows_updated": 0,
        "rows_unrecoverable": 0,
        "per_owner": {},
    }

    with db.connect(db_path) as conn:
        snap = db.snapshot("backfill_memo_fields", db_path)
        summary["snapshot_path"] = str(snap) if snap else None

        # Totals before, for the correction record.
        before = conn.execute(
            "SELECT COUNT(*) n, ROUND(SUM(amount),2) amt FROM donations "
            "WHERE status IN ('CONFIRMED','PROBABLE') AND counted = 1"
        ).fetchone()

        slugs = [
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT entity_slug FROM donations
                 WHERE is_individual IS NULL
                   AND status IN ('CONFIRMED', 'PROBABLE')
                 ORDER BY entity_slug
                """
            )
        ]

        for slug in slugs:
            slug_dir = raw_dir / slug
            index = _scan_owner_dir(slug_dir) if slug_dir.is_dir() else {}
            updated_here = 0
            unrecoverable_here = 0
            rows = list(
                conn.execute(
                    """
                    SELECT transaction_id FROM donations
                     WHERE entity_slug = ?
                       AND is_individual IS NULL
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
                       SET memo_code = :memo_code,
                           memo_text = :memo_text,
                           is_individual = :is_individual
                     WHERE transaction_id = :transaction_id
                    """,
                    {**fields, "transaction_id": txn},
                )
                updated_here += 1

            summary["owners_scanned"] += 1
            summary["rows_updated"] += updated_here
            summary["rows_unrecoverable"] += unrecoverable_here
            summary["per_owner"][slug] = {
                "txns_in_index": len(index),
                "rows_updated": updated_here,
                "rows_unrecoverable": unrecoverable_here,
            }

        # Recompute the derived counted flag over the whole table now that
        # is_individual is populated.
        excluded = db.recompute_counted(conn, None)
        summary["counted_excluded_rows"] = excluded

        after = conn.execute(
            "SELECT COUNT(*) n, ROUND(SUM(amount),2) amt FROM donations "
            "WHERE status IN ('CONFIRMED','PROBABLE') AND counted = 1"
        ).fetchone()
        excl_amt = conn.execute(
            "SELECT COUNT(*) n, ROUND(SUM(amount),2) amt FROM donations "
            "WHERE status IN ('CONFIRMED','PROBABLE') AND counted = 0"
        ).fetchone()
        summary["counted_before"] = {"rows": before["n"], "amount": before["amt"]}
        summary["counted_after"] = {"rows": after["n"], "amount": after["amt"]}
        summary["excluded_total"] = {"rows": excl_amt["n"], "amount": excl_amt["amt"]}

    _append_provenance(summary)
    return summary


def _append_provenance(summary: dict) -> None:
    PROVENANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = db._utc_now_iso()
    b = summary["counted_before"]
    a = summary["counted_after"]
    x = summary["excluded_total"]
    block = [
        f"\n### {ts[:10]} — CORRECTION — earmark/conduit dedup (schema v8, §1.1)",
        "",
        "- **what**: added FEC memo_code/memo_text/is_individual to donations and a "
        "derived `counted` flag; a conduit passthrough leg (is_individual=0) with a "
        "countable sibling in the same (owner, donor, date, amount) group is now "
        "excluded from every published SUM (double-count removed). Lone conduit legs "
        "are preserved (counted=1).",
        f"- **rows_updated (is_individual backfilled from raw)**: `{summary['rows_updated']}`",
        f"- **rows_unrecoverable (raw gone, left NULL / countable)**: `{summary['rows_unrecoverable']}`",
        f"- **counted excluded**: `{x['rows']}` rows / `${x['amount']:,.2f}`",
        f"- **published CONFIRMED+PROBABLE total**: `${b['amount']:,.2f}` ({b['rows']} rows) "
        f"→ `${a['amount']:,.2f}` ({a['rows']} rows)",
        f"- **snapshot_path**: `{summary.get('snapshot_path')}`",
        "- **note**: rows are never deleted (§1.10); the excluded legs stay queryable "
        "(counted=0). Mirrors FEC's own is_individual dedup convention.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)

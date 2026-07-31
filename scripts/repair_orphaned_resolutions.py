#!/usr/bin/env python3
"""One-off repair: make orphaned queue verdicts durable.

Background. Adjudication is stored in two places with different lifetimes:

  - `review_resolutions` — the DURABLE verdict store, keyed
    (transaction_id, entity_slug). Survives reclassify.
  - `review_queue.resolution` — a column on a table that
    `reclassify_entity` DELETEs and rebuilds from raw.

On rebuild, the only thing that stops a previously-discarded record from
re-entering the open queue is `db.discarded_txns_for_slug`, which reads
**`review_resolutions` alone**. So a verdict written to the queue column
without a matching durable row is not a weaker record of the decision — it is
a decision that silently un-makes itself the next time its owner is
reclassified, with no error and no log line.

Two such rows are live, both `fisher-john`, both from the 2026-05-30 audit
triage, both DISCARDED as same-name strangers in an undocumented city:

  A-TI470  MENLO PARK, CA
  A-TI471  MENLO PARK, CA

Neither has a `donations` row (they were correctly discarded, not attributed),
so nothing about the published totals changes here — this is purely about
whether that human judgment survives the next `reclassify fisher-john`.

What this does, per row: copies the verdict already recorded on the queue row
(`resolution`, `resolution_reason`, `resolution_at`, `resolved_by`) into
`review_resolutions` via the normal `db.upsert_review_resolution` helper. It
invents nothing and changes no verdict — every value written is already stored
on the row it is read from.

Idempotent: re-running finds no orphans and reports zero repairs.
GOVERNANCE.md §1.6 — snapshots master.db before writing.
"""
from __future__ import annotations

import sqlite3
import sys

from . import db
from .paths import MASTER_DB
from .provenance import append_provenance


def find_orphans(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Queue rows carrying a resolution with no durable counterpart."""
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT q.transaction_id, q.entity_slug, q.resolution,
               q.resolution_reason, q.resolution_at, q.resolved_by
          FROM review_queue q
          LEFT JOIN review_resolutions r
            ON r.transaction_id = q.transaction_id
           AND r.entity_slug = q.entity_slug
         WHERE q.resolution IS NOT NULL
           AND r.transaction_id IS NULL
         ORDER BY q.entity_slug, q.transaction_id
        """
    ).fetchall()


def repair(db_path=MASTER_DB, *, dry_run: bool = False) -> list[dict]:
    db.init(db_path)
    repaired: list[dict] = []

    with db.connect(db_path) as conn:
        orphans = find_orphans(conn)
        for row in orphans:
            repaired.append(
                {
                    "transaction_id": row["transaction_id"],
                    "entity_slug": row["entity_slug"],
                    "resolution": row["resolution"],
                    "resolution_reason": row["resolution_reason"],
                    "resolution_at": row["resolution_at"],
                }
            )
            if dry_run:
                continue
            db.upsert_review_resolution(
                conn,
                transaction_id=row["transaction_id"],
                entity_slug=row["entity_slug"],
                resolution=row["resolution"],
                resolution_reason=row["resolution_reason"],
                # Preserve the ORIGINAL adjudication timestamp. This repair is
                # not a new decision, and back-dating it to today would falsify
                # when the judgment was actually made.
                resolved_at=row["resolution_at"],
                resolved_by=row["resolved_by"],
            )

    return repaired


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv

    if not dry_run:
        snap = db.snapshot("pre-repair-orphaned-resolutions")
        print(f"Snapshot: {snap}")

    repaired = repair(dry_run=dry_run)
    if not repaired:
        print("No orphaned queue verdicts found — nothing to repair.")
        return 0

    for r in repaired:
        print(
            f"  {r['entity_slug']}/{r['transaction_id']} → {r['resolution']} "
            f"(originally adjudicated {r['resolution_at']})"
        )
    print(f"{'Would repair' if dry_run else 'Repaired'} {len(repaired)} orphaned verdict(s).")

    if dry_run:
        return 0

    lines = [
        f"\n### {repaired[0]['resolution_at'][:10]} — REVIEW_RESOLUTION — orphaned queue verdicts made durable",
        "",
        f"Copied **{len(repaired)}** verdict(s) from `review_queue.resolution` into "
        "`review_resolutions`, the durable store that `reclassify` reads when "
        "rebuilding the queue. Without a durable row these verdicts would have "
        "reverted to open on the next reclassify of their owner, silently "
        "discarding the adjudication.",
        "",
        "No verdict was changed and no value was invented — each field was copied "
        "from the queue row that already carried it, including the original "
        "`resolution_at` timestamp. None of these transactions has a `donations` "
        "row, so no published total is affected.",
        "",
    ]
    for r in repaired:
        lines.append(
            f"- `{r['entity_slug']}` / `{r['transaction_id']}` → **{r['resolution']}** "
            f"(adjudicated {r['resolution_at']}) — {r['resolution_reason']}"
        )
    lines.append("")
    lines.append(
        "Recurrence is now caught by `db.check_adjudication_integrity()`, wired "
        "into `cli validate`."
    )
    append_provenance("\n".join(lines))
    print("Logged to catalog/PROVENANCE_LOG.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

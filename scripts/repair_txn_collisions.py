#!/usr/bin/env python3
"""One-off repair: undo the two wrong supersessions caused by the §1.3 gap.

Background. `donations.transaction_id` is the PRIMARY KEY, but FEC transaction
ids are filer-assigned and unique only WITHIN a filing. `insert_donation` had a
collision guard for this, but it could only fire when BOTH the stored and
incoming rows carried a globally-unique `sub_id` — and sub_id is populated on a
few percent of the archive. For everything else the guard was inert, so a real
collision fell through to the supersede path and was recorded as a fabricated
"FEC restatement" of one owner's donation into a different owner's.

Two such rows are live in the archive:

  SA11AI.4319  kendrick-ken $2,800 (2020-05-30, GOP WINNING WOMEN)
               "restated into" reinsdorf-jerry $8,400
  SA11AI.4164  dewitt-bill  $5,000 (2020-07-10, SENATE MAJORITY FIREWALL 2020)
               "restated into" reinsdorf-jerry $5,000

Both displaced rows were CONFIRMED (their `status_reason` records "two
confirming signals") and both dropped out of every published total the moment
they were marked SUPERSEDED. Neither was deleted — §1.10 held — so this repair
is a status/provenance correction, not a re-fetch.

What this does, per row:
  - restores `status` from the preserved `status_reason` ("two confirming
    signals" → CONFIRMED; "one confirming signal" → PROBABLE). The classifier's
    own verdict was preserved on the row, so nothing is re-derived by hand.
  - clears the false `superseded_by` / `superseded_reason`.
  - re-keys `transaction_id` from `<canonical>~superseded~<ts>` to
    `<canonical>~collision~<entity_slug>`, which is honest about why the row
    cannot hold the canonical id: the colliding row legitimately holds it, and
    transaction_id is still a single-column PRIMARY KEY.

The re-key is a WORKAROUND, not the fix. The fix is a composite primary key
`(transaction_id, entity_slug)` — which `review_queue` already uses, for exactly
this reason — so both real contributions can hold the same filer-assigned id.
That migration is tracked separately; this script only stops two real donations
from being invisible in the meantime.

Idempotent: re-running finds no matching rows and reports zero repairs.
GOVERNANCE.md §1.6 — snapshots master.db before writing.
"""
from __future__ import annotations

import sqlite3
import sys

from . import db
from .paths import MASTER_DB
from .provenance import append_provenance, PROVENANCE_LOG

# The two known-bad supersessions, identified by the canonical id they were
# wrongly filed under plus the owner whose donation was displaced.
TARGETS = [
    ("SA11AI.4319", "kendrick-ken"),
    ("SA11AI.4164", "dewitt-bill"),
]


def _status_from_reason(reason: str | None) -> str:
    r = (reason or "").lower()
    if "two confirming signals" in r or "strong signal" in r:
        return "CONFIRMED"
    if "one confirming signal" in r:
        return "PROBABLE"
    return "UNCERTAIN"


def repair(db_path=MASTER_DB, *, dry_run: bool = False) -> list[dict]:
    db.init(db_path)
    repaired: list[dict] = []

    with db.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for canonical, slug in TARGETS:
            row = conn.execute(
                """
                SELECT * FROM donations
                 WHERE status = 'SUPERSEDED'
                   AND superseded_by = ?
                   AND entity_slug = ?
                """,
                (canonical, slug),
            ).fetchone()
            if row is None:
                continue

            new_status = _status_from_reason(row["status_reason"])
            new_txn = f"{canonical}~collision~{slug}"
            repaired.append(
                {
                    "canonical_transaction_id": canonical,
                    "entity_slug": slug,
                    "old_transaction_id": row["transaction_id"],
                    "new_transaction_id": new_txn,
                    "restored_status": new_status,
                    "amount": row["amount"],
                    "date": row["date"],
                    "recipient": row["recipient_committee_name"],
                    "false_reason": row["superseded_reason"],
                }
            )
            if dry_run:
                continue

            conn.execute(
                """
                UPDATE donations
                   SET transaction_id = ?,
                       status = ?,
                       superseded_by = NULL,
                       superseded_reason = NULL
                 WHERE transaction_id = ?
                """,
                (new_txn, new_status, row["transaction_id"]),
            )

        if repaired and not dry_run:
            # `counted` is derived from status, so it has to be recomputed for
            # the affected owners now that these rows are live again.
            for slug in {r["entity_slug"] for r in repaired}:
                db.recompute_counted(conn, slug)

    return repaired


def main() -> None:
    dry = "--dry-run" in sys.argv
    if not dry:
        snap = db.snapshot("pre-repair-txn-collisions", MASTER_DB)
        print(f"snapshot: {snap}")

    rows = repair(dry_run=dry)
    if not rows:
        print("No wrong supersessions found — nothing to repair (already clean).")
        return

    for r in rows:
        print(
            f"{'WOULD REPAIR' if dry else 'REPAIRED'} {r['entity_slug']}: "
            f"${r['amount']:,.0f} {r['date']} -> {r['recipient']}\n"
            f"    {r['old_transaction_id']}\n"
            f"      -> {r['new_transaction_id']}  status={r['restored_status']}"
        )

    if dry:
        return

    block = [
        "",
        "### 2026-07-19 — REPAIR (wrong supersessions from the §1.3 transaction_id gap)",
        "",
        "- **what**: two donations were marked SUPERSEDED with a fabricated "
        '"FEC restatement" reason and dropped out of all published totals, because '
        "a filer-assigned `transaction_id` was reused across owners and the "
        "collision guard could not fire without `sub_id` on both rows.",
        "- **rows repaired**:",
    ]
    for r in rows:
        block.append(
            f"  - `{r['entity_slug']}` ${r['amount']:,.0f} {r['date']} → "
            f"{r['recipient']}; status restored to `{r['restored_status']}`, "
            f"false reason cleared (`{(r['false_reason'] or '')[:60]}`), "
            f"re-keyed `{r['old_transaction_id']}` → `{r['new_transaction_id']}`"
        )
    block += [
        "- **not a re-fetch**: no FEC data was retrieved; the rows were never "
        "deleted (§1.10), so this restores status and provenance only.",
        "- **guard**: `insert_donation` now detects a collision without `sub_id` "
        "(differing `entity_slug`, or committee+date+amount all differing), so "
        "this class of corruption cannot recur.",
        "- **follow-up**: the durable fix is a composite primary key "
        "`(transaction_id, entity_slug)` — which `review_queue` already uses — so "
        "both real contributions can hold the same filer-assigned id. The "
        "`~collision~` re-key here is a workaround until that migration lands.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)
    print(f"\nLogged {len(rows)} repair(s) to {PROVENANCE_LOG}")


if __name__ == "__main__":
    main()

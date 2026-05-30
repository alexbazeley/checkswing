"""Merge per-bucket master.db artifacts back into the consolidated master.db.

The weekly refresh runs as 4 parallel GHA matrix jobs (see refresh.yml). Each
bucket starts from the same pre-refresh master.db (the one checked into git)
and writes its own deltas. The consolidate job downloads all bucket DB
artifacts, runs this script to merge them, then commits the result.

Approach
--------
Buckets process disjoint owner slugs, so each owner is touched by exactly one
bucket. For every new ingestion_run in a bucket DB, we take its entity_slug as
"in-scope for this bucket" and per-slug replace donations + review_queue +
ingestion_runs in the consolidated DB from the bucket DB.

This avoids row-level UPSERT logic and the related supersession/PK edge cases
— the bucket already ran the full ingest pipeline against that owner, so its
rows for that owner are the authoritative state. We just adopt them.

The pre-refresh master.db is the starting point (from the checkout). We
sequentially apply each bucket on top.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


def _existing_run_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT run_id FROM ingestion_runs")}


def _bucket_touched_slugs(consolidated_db: Path, bucket_db: Path) -> set[str]:
    """Owner slugs a bucket would adopt: entity_slugs of its ingestion_runs not
    already present in the consolidated DB. Read-only — used for the pre-merge
    disjointness check, computed against the same pre-merge baseline for every
    bucket so overlap is detected before any mutation."""
    cons = sqlite3.connect(consolidated_db)
    cons.row_factory = sqlite3.Row
    bucket = sqlite3.connect(bucket_db)
    bucket.row_factory = sqlite3.Row
    try:
        pre = _existing_run_ids(cons)
        rows = bucket.execute(
            "SELECT DISTINCT entity_slug FROM ingestion_runs WHERE run_id NOT IN ({seq})".format(
                seq=",".join(["?"] * len(pre)) or "''"
            ),
            tuple(pre) or (),
        ).fetchall()
        return {r["entity_slug"] for r in rows}
    finally:
        cons.close()
        bucket.close()


def _assert_disjoint(consolidated_db: Path, bucket_dbs: list[Path]) -> None:
    """Verify no owner is touched by more than one bucket (the invariant that
    select_bucket's round-robin guarantees). The per-slug merge is DELETE +
    INSERT — last writer wins — so overlap would silently drop a bucket's rows.
    Fail loudly BEFORE any merge instead. Raises RuntimeError on overlap."""
    owner_to_bucket: dict[str, str] = {}
    for bdb in bucket_dbs:
        if not bdb.exists():
            continue
        for slug in _bucket_touched_slugs(consolidated_db, bdb):
            prior = owner_to_bucket.get(slug)
            if prior is not None:
                raise RuntimeError(
                    f"owner {slug!r} is touched by both {prior} and {bdb.name}; "
                    f"buckets must be disjoint (per-slug merge is last-writer-wins, "
                    f"so overlap would silently drop one bucket's rows)."
                )
            owner_to_bucket[slug] = bdb.name


def _merge_one_bucket(consolidated_db: Path, bucket_db: Path) -> dict:
    """Apply one bucket's deltas to the consolidated DB. Returns a stats dict."""
    if not bucket_db.exists():
        raise RuntimeError(f"Bucket DB not found: {bucket_db}")

    cons = sqlite3.connect(consolidated_db)
    cons.row_factory = sqlite3.Row
    bucket = sqlite3.connect(bucket_db)
    bucket.row_factory = sqlite3.Row

    pre_run_ids = _existing_run_ids(cons)
    new_runs = [
        dict(r)
        for r in bucket.execute(
            "SELECT * FROM ingestion_runs WHERE run_id NOT IN ({seq})".format(
                seq=",".join(["?"] * len(pre_run_ids)) or "''"
            ),
            tuple(pre_run_ids) or (),
        )
    ]
    touched_slugs = sorted({r["entity_slug"] for r in new_runs})

    if not touched_slugs:
        cons.close()
        bucket.close()
        return {"bucket_db": str(bucket_db), "touched_slugs": [], "new_runs": 0,
                "donations_replaced": 0, "review_queue_replaced": 0}

    # Per-slug replace donations + review_queue from bucket DB.
    n_donations = 0
    n_review = 0
    cons.execute("BEGIN")
    try:
        for slug in touched_slugs:
            cons.execute("DELETE FROM donations WHERE entity_slug = ?", (slug,))
            cons.execute("DELETE FROM review_queue WHERE entity_slug = ?", (slug,))

            don_rows = list(bucket.execute(
                "SELECT * FROM donations WHERE entity_slug = ?", (slug,)
            ))
            for row in don_rows:
                cols = row.keys()
                placeholders = ",".join("?" * len(cols))
                cons.execute(
                    f"INSERT INTO donations ({','.join(cols)}) VALUES ({placeholders})",
                    tuple(row[c] for c in cols),
                )
            n_donations += len(don_rows)

            rq_rows = list(bucket.execute(
                "SELECT * FROM review_queue WHERE entity_slug = ?", (slug,)
            ))
            for row in rq_rows:
                cols = row.keys()
                placeholders = ",".join("?" * len(cols))
                cons.execute(
                    f"INSERT INTO review_queue ({','.join(cols)}) VALUES ({placeholders})",
                    tuple(row[c] for c in cols),
                )
            n_review += len(rq_rows)

            # Adopt the bucket's entities.refreshed_at for this slug.
            ent = bucket.execute(
                "SELECT refreshed_at FROM entities WHERE slug = ?", (slug,)
            ).fetchone()
            if ent is not None and ent["refreshed_at"] is not None:
                cons.execute(
                    "UPDATE entities SET refreshed_at = ? WHERE slug = ?",
                    (ent["refreshed_at"], slug),
                )

        # Insert the new ingestion_runs.
        for row in new_runs:
            cols = list(row.keys())
            placeholders = ",".join("?" * len(cols))
            cons.execute(
                f"INSERT INTO ingestion_runs ({','.join(cols)}) VALUES ({placeholders})",
                tuple(row[c] for c in cols),
            )

        cons.commit()
    except Exception:
        cons.rollback()
        raise
    finally:
        cons.close()
        bucket.close()

    return {
        "bucket_db": str(bucket_db),
        "touched_slugs": touched_slugs,
        "new_runs": len(new_runs),
        "donations_replaced": n_donations,
        "review_queue_replaced": n_review,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--consolidated", required=True, type=Path,
                   help="Path to the consolidated master.db (will be modified in place).")
    p.add_argument("--bucket-db", action="append", default=[], type=Path,
                   help="Path to a bucket master.db artifact. Pass multiple times.")
    args = p.parse_args(argv)

    if not args.consolidated.exists():
        print(f"consolidated DB not found: {args.consolidated}", file=sys.stderr)
        return 2
    if not args.bucket_db:
        print("no --bucket-db provided; nothing to merge", file=sys.stderr)
        return 2

    # Pre-flight: buckets must be disjoint by owner. Checked against the
    # pre-merge baseline so a future bucketing change that breaks the invariant
    # aborts loudly here instead of silently losing rows during the merge.
    try:
        _assert_disjoint(args.consolidated, args.bucket_db)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3

    print(f"consolidated: {args.consolidated}")
    for bdb in args.bucket_db:
        if not bdb.exists():
            # The consolidate job tolerates a missing bucket DB (a bucket may
            # have produced no artifact). Skip rather than abort.
            print(f"  skipping {bdb} (not found)", file=sys.stderr)
            continue
        stats = _merge_one_bucket(args.consolidated, bdb)
        print(
            f"  merged {bdb.name}: {stats['new_runs']} new run(s), "
            f"{len(stats['touched_slugs'])} owner(s), "
            f"{stats['donations_replaced']} donations, "
            f"{stats['review_queue_replaced']} review_queue"
        )
        if stats["touched_slugs"]:
            print(f"    owners: {stats['touched_slugs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

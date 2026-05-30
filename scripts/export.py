"""CSV exports.

Per-entity: data/donations/<slug>/all.csv (CONFIRMED + PROBABLE; status column
always present so consumers cannot accidentally treat PROBABLE as canonical).

Per-cycle: data/donations/<slug>/by_cycle/<cycle>.csv (same schema).

Aggregate: data/donations/_aggregate/by_owner.csv (CONFIRMED only) and
by_owner_with_probable.csv (both tiers, status preserved).
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from . import db
from .paths import DONATIONS_DIR, donations_dir_for


def _csv_safe(v):
    """Neutralize spreadsheet formula injection in exported CSVs.

    A donor-filed cell beginning with = + - @ (or tab/CR) is evaluated as a
    formula by Excel/Sheets; prefix it with a single quote so the cell is
    treated as text. `csv` already handles delimiter quoting — this only guards
    the leading-character formula trigger on free-text fields (employer,
    occupation, names)."""
    if isinstance(v, str) and v and v[0] in "=+-@\t\r":
        return "'" + v
    return v

EXPORT_COLUMNS = [
    "transaction_id",
    "entity_slug",
    "entity_kind",
    "parent_owner_slug",
    "status",
    "status_reason",
    "signals_matched",
    "contributor_name_raw",
    "contributor_employer_raw",
    "contributor_occupation_raw",
    "contributor_city",
    "contributor_state",
    "contributor_zip",
    "recipient_committee_id",
    "recipient_committee_name",
    "recipient_candidate_id",
    "recipient_candidate_name",
    "recipient_party",
    "recipient_office",
    "amount",
    "date",
    "election_cycle",
    "report_type",
    "filing_id",
    "raw_payload_path",
    "ingested_at",
]


def export_entity(slug: str) -> dict:
    """Write all.csv and by_cycle/*.csv for one entity.

    Returns counts.
    """
    out_dir = donations_dir_for(slug)
    all_path = out_dir / "all.csv"

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM donations
            WHERE entity_slug = ? AND status IN ('CONFIRMED', 'PROBABLE')
            ORDER BY date DESC, transaction_id
            """,
            (slug,),
        ).fetchall()

    with all_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for r in rows:
            d = {k: _csv_safe(r[k]) for k in EXPORT_COLUMNS if k in r.keys()}
            writer.writerow(d)

    # Partition by cycle.
    by_cycle: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        cycle = r["election_cycle"]
        if cycle is None:
            continue
        by_cycle[int(cycle)].append({k: _csv_safe(r[k]) for k in EXPORT_COLUMNS if k in r.keys()})

    cycle_dir = out_dir / "by_cycle"
    cycle_dir.mkdir(parents=True, exist_ok=True)
    # Wipe existing cycle files (cheap, deterministic).
    for old in cycle_dir.glob("*.csv"):
        old.unlink()
    for cycle, recs in by_cycle.items():
        p = cycle_dir / f"{cycle}.csv"
        with p.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
            writer.writeheader()
            for d in recs:
                writer.writerow(d)

    return {
        "slug": slug,
        "rows": len(rows),
        "all_csv": str(all_path),
        "cycle_files": len(by_cycle),
    }


def export_aggregate() -> dict:
    """Write data/donations/_aggregate/by_owner.csv (CONFIRMED only)
    and by_owner_with_probable.csv (both tiers, status preserved per row)."""
    agg_dir = DONATIONS_DIR / "_aggregate"
    agg_dir.mkdir(parents=True, exist_ok=True)

    confirmed_only = agg_dir / "by_owner.csv"
    with_probable = agg_dir / "by_owner_with_probable.csv"

    with db.connect() as conn:
        rows_conf = conn.execute(
            """
            SELECT entity_slug,
                   parent_owner_slug,
                   entity_kind,
                   election_cycle,
                   recipient_party,
                   recipient_office,
                   COUNT(*) AS donations,
                   SUM(amount) AS total_amount
            FROM donations
            WHERE status = 'CONFIRMED'
            GROUP BY entity_slug, parent_owner_slug, entity_kind, election_cycle, recipient_party, recipient_office
            ORDER BY entity_slug, election_cycle
            """
        ).fetchall()
        rows_with = conn.execute(
            """
            SELECT entity_slug,
                   parent_owner_slug,
                   entity_kind,
                   status,
                   election_cycle,
                   recipient_party,
                   recipient_office,
                   COUNT(*) AS donations,
                   SUM(amount) AS total_amount
            FROM donations
            WHERE status IN ('CONFIRMED', 'PROBABLE')
            GROUP BY entity_slug, parent_owner_slug, entity_kind, status, election_cycle, recipient_party, recipient_office
            ORDER BY entity_slug, election_cycle, status
            """
        ).fetchall()

    def _write(path: Path, rows, cols):
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            for r in rows:
                writer.writerow([_csv_safe(r[c]) for c in cols])

    _write(
        confirmed_only,
        rows_conf,
        ["entity_slug", "parent_owner_slug", "entity_kind", "election_cycle",
         "recipient_party", "recipient_office", "donations", "total_amount"],
    )
    _write(
        with_probable,
        rows_with,
        ["entity_slug", "parent_owner_slug", "entity_kind", "status", "election_cycle",
         "recipient_party", "recipient_office", "donations", "total_amount"],
    )
    return {
        "confirmed_only": str(confirmed_only),
        "with_probable": str(with_probable),
        "confirmed_rows": len(rows_conf),
        "with_probable_rows": len(rows_with),
    }

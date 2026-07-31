"""CSV exports.

Per-entity: data/donations/<slug>/all.csv (CONFIRMED + PROBABLE; status column
always present so consumers cannot accidentally treat PROBABLE as canonical).

Per-cycle: data/donations/<slug>/by_cycle/<cycle>.csv (same schema).

Aggregate: data/donations/_aggregate/by_owner.csv (CONFIRMED only) and
by_owner_with_probable.csv (both tiers, status preserved).

Household: data/donations/_aggregate/by_household.csv — owner + related entities
(spouse/family) rolled up under the owner, with an explicit entity_kind column on
every row so a consumer can total "the household" while always seeing which dollars
came from the owner vs. a spouse. Never a silent merge (VERIFICATION.md anti-pattern).

All exports filter `counted = 1` — v8 excludes earmark/conduit passthrough legs
(ActBlue/WinRed) that FEC double-reports against the ultimate recipient, so a
naive SUM of an exported CSV matches the dashboard totals (see
db.recompute_counted / DONATION_SCHEMA.md).
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
              AND counted = 1
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
            WHERE status = 'CONFIRMED' AND counted = 1
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
            WHERE status IN ('CONFIRMED', 'PROBABLE') AND counted = 1
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


def export_household() -> dict:
    """Write data/donations/_aggregate/by_household.csv.

    One row per (household, entity, entity_kind, status) — both CONFIRMED and
    PROBABLE, status preserved. The household key rolls related entities up under
    their owner (COALESCE(parent_owner_slug, entity_slug)); the explicit
    entity_slug/entity_kind columns mean a household total is always decomposable
    into owner vs. spouse/family dollars, never a silent merge
    (VERIFICATION.md anti-pattern). An owner with no related entities simply
    appears as its own single-entity household."""
    agg_dir = DONATIONS_DIR / "_aggregate"
    agg_dir.mkdir(parents=True, exist_ok=True)
    out_path = agg_dir / "by_household.csv"

    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(parent_owner_slug, entity_slug) AS household_slug,
                   entity_slug,
                   entity_kind,
                   status,
                   COUNT(*) AS donations,
                   SUM(amount) AS total_amount
            FROM donations
            WHERE status IN ('CONFIRMED', 'PROBABLE') AND counted = 1
            GROUP BY household_slug, entity_slug, entity_kind, status
            ORDER BY household_slug, (entity_kind = 'owner') DESC, entity_kind, entity_slug, status
            """
        ).fetchall()

    cols = ["household_slug", "entity_slug", "entity_kind", "status", "donations", "total_amount"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for r in rows:
            writer.writerow([_csv_safe(r[c]) for c in cols])

    return {"path": str(out_path), "rows": len(rows)}


# ── Freshness gate ──────────────────────────────────────────────────────────
#
# `data/donations/` is git-tracked *so that people can cite it* (GOVERNANCE
# §2.4), but `cli export` only runs on the monthly cron. Any PR that mutates
# master.db and forgets to re-export leaves the citable surface stale, and until
# this check existed nothing noticed: on 2026-07-31 the rollup was found
# **$786,754.70 and 38 owner-rows behind** master.db, drift accumulated across
# three PRs (#135 schema v12, #139 ZIP+4, #142 Todd Ricketts).
#
# The invariant is exact, not approximate — the CSVs are a plain GROUP BY over
# the same `counted = 1` filter, so a correct export reconciles to the cent:
#   by_owner.csv                → status = 'CONFIRMED'
#   by_owner_with_probable.csv  → status IN ('CONFIRMED','PROBABLE')

# Which aggregate file mirrors which slice of master.db.
_AGGREGATE_CONTRACT = (
    ("by_owner.csv", "status = 'CONFIRMED'"),
    ("by_owner_with_probable.csv", "status IN ('CONFIRMED','PROBABLE')"),
)

# Tolerance for the float sum only. SUM(REAL) in SQLite and Python's float sum
# over the same values can differ in the last bit; a cent is far below any real
# drift (the observed failure was $786,754.70) and far above float noise.
_CENT = 0.01


def check_export_freshness(db_path=None) -> list[str]:
    """Do the tracked aggregate CSVs still reconcile to master.db?

    Returns a list of error strings; empty means in sync. A missing DB, an
    unreadable DB (in CI master.db is checked out as a Git LFS pointer — see
    db.check_record_uid_integrity), or a missing CSV is **not** an error here:
    absent is not stale, and failing on it would make the gate fire in every
    environment that legitimately has no database.
    """
    import sqlite3

    errors: list[str] = []
    agg_dir = DONATIONS_DIR / "_aggregate"
    path = db_path or db.MASTER_DB
    if not Path(path).exists():
        return errors

    for filename, where in _AGGREGATE_CONTRACT:
        csv_path = agg_dir / filename
        if not csv_path.exists():
            continue
        try:
            with db.connect(path) as conn:
                row = conn.execute(
                    f"SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS amt FROM donations "
                    f"WHERE superseded_by IS NULL AND counted = 1 AND {where}"
                ).fetchone()
        except sqlite3.DatabaseError:
            return errors  # LFS pointer, not a database — nothing to compare
        db_n, db_amt = row["n"], row["amt"]

        with csv_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        try:
            csv_n = sum(int(r["donations"]) for r in rows)
            csv_amt = sum(float(r["total_amount"]) for r in rows)
        except (KeyError, ValueError) as exc:
            errors.append(f"{filename} is not readable as an aggregate export ({exc})")
            continue

        if csv_n != db_n or abs(csv_amt - db_amt) > _CENT:
            errors.append(
                f"{filename} is STALE — it reports {csv_n:,} donations / ${csv_amt:,.2f} "
                f"but master.db holds {db_n:,} / ${db_amt:,.2f} "
                f"(off by {csv_n - db_n:+,} rows / ${csv_amt - db_amt:+,.2f}). "
                f"This is a citable surface (GOVERNANCE §2.4). "
                f"Fix: python -m scripts.cli export"
            )
    return errors

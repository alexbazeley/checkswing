"""Committee enrichment ingest orchestrator.

For each committee_id that appears as a recipient on a CONFIRMED/PROBABLE
donation, fetch:
  - /committee/<id>/         — identity (one row)
  - /committee/<id>/totals/  — per-cycle scale (≤ ~14 rows)

Upsert into the committees and committee_totals tables (schema v2). Idempotent:
re-running within FRESHNESS_DAYS of the last refresh skips the FEC fetch.

CLAUDE.md §1.4: raw payloads land under data/raw/_committees/<id>/ BEFORE
parsing — see scripts/fetch_committees.py:_persist_committee_raw.
CLAUDE.md §1.5: idempotent (INSERT OR REPLACE on PKs).
CLAUDE.md §1.6: master.db snapshotted before first row write.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import db
from .fetch_committees import (
    fetch_committee_detail,
    fetch_committee_totals,
    parse_committee_detail,
    parse_committee_totals_row,
)
from .fetch_fec import FECClient
from .paths import DATA_DIR, MASTER_DB, relpath


COMMITTEES_LOCK = DATA_DIR / ".committees_ingest.lock"

# How long a committees row is considered fresh. Re-fetches inside this window
# are skipped unless force_refresh=True. 30 days matches FEC's typical filing
# cadence — leadership PACs file quarterly, super PACs monthly.
FRESHNESS_DAYS = 30


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── File lock (separate from refresh.py's; that one guards donation ingest) ─


@contextmanager
def _acquire_lock(path: Path | None = None) -> Iterator[None]:
    """Exclusive lock; raises if another committee ingest is in flight.

    Default lock path is resolved at call time (not function-definition time)
    so monkeypatching `ingest_committees.COMMITTEES_LOCK` in tests works.
    """
    if path is None:
        path = COMMITTEES_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = path.read_text(encoding="utf-8") if path.exists() else "(empty)"
        raise RuntimeError(
            f"Committee ingest already running (or stale lock at {path}). "
            f"Lock contents: {existing.strip() or '(empty)'}. "
            f"If you're sure no run is in flight, delete the lock and retry."
        )
    try:
        os.write(fd, f"{_utc_now_iso()} · pid={os.getpid()}\n".encode())
        os.close(fd)
        yield
    finally:
        try:
            path.unlink()
        except OSError:
            pass


# ─── Per-committee freshness check ───────────────────────────────────────────


def _committee_is_fresh(conn: sqlite3.Connection, committee_id: str) -> bool:
    row = conn.execute(
        "SELECT refreshed_at FROM committees WHERE committee_id = ?", (committee_id,)
    ).fetchone()
    if row is None or row["refreshed_at"] is None:
        return False
    try:
        refreshed = datetime.fromisoformat(row["refreshed_at"].replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    age_days = (datetime.now(timezone.utc) - refreshed).total_seconds() / 86400
    return age_days < FRESHNESS_DAYS


# ─── Per-committee ingest ────────────────────────────────────────────────────


def ingest_committee(
    committee_id: str,
    *,
    client: FECClient | None = None,
    force_refresh: bool = False,
    db_path: Path = MASTER_DB,
) -> dict:
    """Fetch + upsert one committee's identity and totals.

    Skips the FEC fetch if the committee's row is < FRESHNESS_DAYS old, unless
    force_refresh=True. Returns a small summary dict.
    """
    db.init(db_path)
    with db.connect(db_path) as conn:
        if not force_refresh and _committee_is_fresh(conn, committee_id):
            return {
                "committee_id": committee_id,
                "status": "skipped_fresh",
                "totals_rows": 0,
            }

    client = client or FECClient()
    detail_row, detail_raw_path = fetch_committee_detail(client, committee_id)
    totals_rows, totals_raw_path = fetch_committee_totals(client, committee_id)

    parsed_detail = parse_committee_detail(detail_row)
    parsed_totals = [
        parse_committee_totals_row(committee_id, r) for r in totals_rows
    ]
    parsed_totals = [t for t in parsed_totals if t["cycle"] is not None]

    now = _utc_now_iso()
    with db.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO committees (
                committee_id, name, designation, designation_label,
                committee_type, committee_type_label, party, party_full,
                organization_type, affiliated_committee_name, candidate_ids,
                treasurer_name, custodian_name, city, state, zip,
                filing_frequency, first_file_date, last_file_date, last_f1_date,
                is_terminated, cycles,
                external_link, external_link_label, external_link_source,
                raw_payload_path, fetched_at, refreshed_at
            )
            VALUES (
                :committee_id, :name, :designation, :designation_label,
                :committee_type, :committee_type_label, :party, :party_full,
                :organization_type, :affiliated_committee_name, :candidate_ids,
                :treasurer_name, :custodian_name, :city, :state, :zip,
                :filing_frequency, :first_file_date, :last_file_date, :last_f1_date,
                :is_terminated, :cycles,
                -- Preserve any existing external_link* (set by the YAML applier)
                COALESCE((SELECT external_link FROM committees WHERE committee_id = :committee_id), NULL),
                COALESCE((SELECT external_link_label FROM committees WHERE committee_id = :committee_id), NULL),
                COALESCE((SELECT external_link_source FROM committees WHERE committee_id = :committee_id), NULL),
                :raw_payload_path, :fetched_at, :refreshed_at
            )
            """,
            {
                **parsed_detail,
                "raw_payload_path": relpath(detail_raw_path),
                "fetched_at": now,
                "refreshed_at": now,
            },
        )

        # Replace totals rows for this committee from the fresh payload — FEC
        # may amend a cycle's totals retroactively, so re-fetched cycles win.
        # INSERT OR REPLACE handles the case where FEC's /totals/ endpoint
        # returns multiple rows for the same cycle on a candidate committee
        # (primary + general elections each get their own row). The PK is
        # (committee_id, cycle) so the last row per cycle survives. Less
        # accurate than aggregating subtotals — acceptable trade-off given
        # FEC returns these sorted most-recent-coverage first.
        conn.execute("DELETE FROM committee_totals WHERE committee_id = ?", (committee_id,))
        for t in parsed_totals:
            conn.execute(
                """
                INSERT OR REPLACE INTO committee_totals (
                    committee_id, cycle,
                    receipts, disbursements, cash_on_hand_end_period,
                    individual_contributions, other_political_committee_contributions,
                    independent_expenditures,
                    coverage_start_date, coverage_end_date,
                    raw_payload_path, fetched_at
                )
                VALUES (
                    :committee_id, :cycle,
                    :receipts, :disbursements, :cash_on_hand_end_period,
                    :individual_contributions, :other_political_committee_contributions,
                    :independent_expenditures,
                    :coverage_start_date, :coverage_end_date,
                    :raw_payload_path, :fetched_at
                )
                """,
                {
                    **t,
                    "raw_payload_path": relpath(totals_raw_path),
                    "fetched_at": now,
                },
            )

    return {
        "committee_id": committee_id,
        "status": "fetched",
        "totals_rows": len(parsed_totals),
        "name": parsed_detail.get("name"),
    }


# ─── Enumeration over all donation-recipient committees ──────────────────────


def list_committees_from_donations(db_path: Path = MASTER_DB) -> list[str]:
    """All distinct committee_ids referenced by CONFIRMED/PROBABLE donations.

    These are the only committees the dashboard surfaces — there's no value in
    enriching committees that the archive doesn't talk about.
    """
    db.init(db_path)
    with db.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT recipient_committee_id
              FROM donations
             WHERE status IN ('CONFIRMED', 'PROBABLE')
               AND recipient_committee_id IS NOT NULL
             ORDER BY recipient_committee_id
            """
        ).fetchall()
    return [r["recipient_committee_id"] for r in rows]


def ingest_all_committees(
    *,
    only: list[str] | None = None,
    force_refresh: bool = False,
    max_count: int | None = None,
    db_path: Path = MASTER_DB,
) -> dict:
    """Ingest every committee referenced by donations.

    Returns a summary dict. Per-committee failures are caught and recorded;
    they do NOT abort the run (CLAUDE.md §1.9 — prefer try-again-next-time).
    """
    started_at = _utc_now_iso()
    candidates = only or list_committees_from_donations(db_path)
    if max_count is not None:
        candidates = candidates[:max_count]

    summary: dict = {
        "started_at": started_at,
        "completed_at": None,
        "attempted": 0,
        "fetched": 0,
        "skipped_fresh": 0,
        "failed": 0,
        "failed_ids": [],
        "totals_rows_written": 0,
        "snapshot_path": None,
    }

    if not candidates:
        summary["completed_at"] = _utc_now_iso()
        return summary

    # Snapshot before we touch any rows. CLAUDE.md §1.6.
    snap = db.snapshot(f"committees_ingest_{started_at.replace(':', '-')}", db_path)
    summary["snapshot_path"] = str(snap) if snap else None

    with _acquire_lock():
        client: FECClient | None = None
        for cid in candidates:
            summary["attempted"] += 1
            try:
                # Lazily construct the FEC client so an all-fresh dry run (where
                # every committee is skipped) doesn't even require FEC_API_KEY.
                if client is None and not force_refresh:
                    # Cheap pre-check: does this committee already pass the
                    # freshness gate? If yes, no client needed for it.
                    with db.connect(db_path) as conn:
                        if _committee_is_fresh(conn, cid):
                            summary["skipped_fresh"] += 1
                            continue
                if client is None:
                    client = FECClient()
                result = ingest_committee(
                    cid, client=client, force_refresh=force_refresh, db_path=db_path
                )
                if result["status"] == "skipped_fresh":
                    summary["skipped_fresh"] += 1
                else:
                    summary["fetched"] += 1
                    summary["totals_rows_written"] += result["totals_rows"]
                    print(
                        f"[committees] {cid} ✓ "
                        f"({result['totals_rows']} cycle rows) {result.get('name') or ''}"
                    )
            except Exception as e:
                summary["failed"] += 1
                summary["failed_ids"].append(cid)
                print(f"[committees] {cid} ERROR: {e}")

    summary["completed_at"] = _utc_now_iso()
    return summary

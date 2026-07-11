"""Committee enrichment ingest orchestrator.

For each committee_id that appears as a recipient on a CONFIRMED/PROBABLE
donation, fetch:
  - /committee/<id>/         — identity (one row)
  - /committee/<id>/totals/  — per-cycle scale (≤ ~14 rows)

Upsert into the committees and committee_totals tables (schema v2). Idempotent:
re-running within FRESHNESS_DAYS of the last refresh skips the FEC fetch.

GOVERNANCE.md §1.4: raw payloads land under data/raw/_committees/<id>/ BEFORE
parsing — see scripts/fetch_committees.py:_persist_committee_raw.
GOVERNANCE.md §1.5: idempotent (INSERT OR REPLACE on PKs).
GOVERNANCE.md §1.6: master.db snapshotted before first row write.
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
from .fetch_fec import FECClient, FECPermanentError
from .enrichment_base import fresh_within_days
from .paths import DATA_DIR, MASTER_DB, relpath


COMMITTEES_LOCK = DATA_DIR / ".committees_ingest.lock"


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
    return row is not None and fresh_within_days(row["refreshed_at"])


# ─── Committee tombstones (§4.4) ─────────────────────────────────────────────
#
# A committee_id that permanently fails (dissolved/merged — 404, or a 200 with
# zero results) has no `committees` row to carry a freshness stamp, so it is a
# candidate every convergence run and fails every run. Recording it in
# `committee_tombstones` lets `ingest_all_committees` skip it. A force_refresh run
# re-attempts and clears the tombstone on success (in case FEC restores the id).


def _tombstoned_committee_ids(conn: sqlite3.Connection) -> set[str]:
    return {
        r["committee_id"]
        for r in conn.execute("SELECT committee_id FROM committee_tombstones")
    }


def _record_committee_tombstone(
    conn: sqlite3.Connection,
    committee_id: str,
    *,
    http_status: int | None,
    reason: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO committee_tombstones
            (committee_id, not_found_at, http_status, endpoint, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            committee_id,
            _utc_now_iso(),
            http_status,
            f"/committee/{committee_id}/",
            reason,
        ),
    )


def _clear_committee_tombstone(conn: sqlite3.Connection, committee_id: str) -> None:
    conn.execute(
        "DELETE FROM committee_tombstones WHERE committee_id = ?", (committee_id,)
    )


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

        # A previously-tombstoned id that now fetches (FEC restored it, or a
        # force_refresh re-attempt succeeded) is no longer a permanent failure.
        _clear_committee_tombstone(conn, committee_id)

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


def _order_by_staleness(conn: sqlite3.Connection, committee_ids: list[str]) -> list[str]:
    """Return committee_ids oldest-refreshed first (never-refreshed first).

    §4.3: all committees share one `refreshed_at` stamp, so with a 45-day window
    ≈ 31-day cadence the whole cohort expires together — a run either skips all
    (~27 min) or re-fetches all (~5.5h convergence, a ratchet toward GitHub's 6h
    ceiling). Fetching the OLDEST-first + capping how many are fetched per run
    (max_fetch) refreshes the cohort in slices at different times, which both
    bounds each run's wall-clock AND permanently de-synchronizes the stamps."""
    order = {
        r["committee_id"]: r["refreshed_at"]
        for r in conn.execute("SELECT committee_id, refreshed_at FROM committees")
    }
    # Never-seen committees (not in the table) sort first via the "" sentinel.
    return sorted(committee_ids, key=lambda cid: order.get(cid) or "")


def ingest_all_committees(
    *,
    only: list[str] | None = None,
    force_refresh: bool = False,
    max_count: int | None = None,
    max_fetch: int | None = None,
    db_path: Path = MASTER_DB,
) -> dict:
    """Ingest every committee referenced by donations.

    `max_fetch` caps how many committees are actually FETCHED from FEC this run
    (skipped-fresh ones don't count) — the oldest are fetched first, the rest
    deferred to the next run, so a full convergence is spread across runs and no
    single run approaches the 6h ceiling (§4.3).

    Returns a summary dict. Per-committee failures are caught and recorded;
    they do NOT abort the run (GOVERNANCE.md §1.9 — prefer try-again-next-time).
    """
    started_at = _utc_now_iso()
    db.init(db_path)  # ensure schema (incl. committee_tombstones, v10) exists
    candidates = only or list_committees_from_donations(db_path)
    # Oldest-refreshed first so max_fetch trims the freshest tail, not arbitrary
    # committees (skip for an explicit `only` set — caller controls that order).
    if not only:
        with db.connect(db_path) as conn:
            candidates = _order_by_staleness(conn, candidates)
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
        "deferred": 0,  # §4.3: stale committees left for the next run (max_fetch)
        "tombstoned_skipped": 0,  # §4.4: known-dissolved ids skipped without a fetch
        "tombstoned_new": 0,      # §4.4: ids newly tombstoned this run
        "tombstoned_ids": [],
        "totals_rows_written": 0,
        "snapshot_path": None,
    }

    if not candidates:
        summary["completed_at"] = _utc_now_iso()
        return summary

    # §4.4: skip ids already tombstoned as permanent failures — unless the caller
    # forced a refresh (which re-attempts them and clears the tombstone on success).
    if not force_refresh:
        with db.connect(db_path) as conn:
            tombstoned = _tombstoned_committee_ids(conn)
        if tombstoned:
            kept = [cid for cid in candidates if cid not in tombstoned]
            summary["tombstoned_skipped"] = len(candidates) - len(kept)
            candidates = kept

    if not candidates:
        summary["completed_at"] = _utc_now_iso()
        return summary

    # Snapshot before we touch any rows. GOVERNANCE.md §1.6.
    snap = db.snapshot(f"committees_ingest_{started_at.replace(':', '-')}", db_path)
    summary["snapshot_path"] = str(snap) if snap else None

    with _acquire_lock():
        client: FECClient | None = None
        for cid in candidates:
            # §4.3: once this run has fetched its cap, defer the rest (they're the
            # freshest of the stale, safe to pick up next run). A cheap freshness
            # pre-check still lets us tally skips without a fetch.
            if max_fetch is not None and summary["fetched"] >= max_fetch:
                with db.connect(db_path) as conn:
                    if _committee_is_fresh(conn, cid):
                        summary["attempted"] += 1
                        summary["skipped_fresh"] += 1
                        continue
                summary["deferred"] += 1
                continue
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
            except FECPermanentError as e:
                # §4.4: dissolved/merged id — tombstone it so future convergence
                # runs skip it instead of re-failing every month.
                with db.connect(db_path) as conn:
                    _record_committee_tombstone(
                        conn, cid, http_status=e.status, reason=str(e)
                    )
                summary["failed"] += 1
                summary["failed_ids"].append(cid)
                summary["tombstoned_new"] += 1
                summary["tombstoned_ids"].append(cid)
                print(f"[committees] {cid} TOMBSTONED (permanent): {e}")
            except Exception as e:
                summary["failed"] += 1
                summary["failed_ids"].append(cid)
                print(f"[committees] {cid} ERROR: {e}")

    summary["completed_at"] = _utc_now_iso()
    return summary

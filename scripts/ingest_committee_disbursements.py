"""Committee beneficiaries ingest orchestrator.

For each committee in the archive (one that received an attributed donation
and has been enriched into the committees table), walk every cycle FEC
reports totals for and fetch the top-N recipients of that committee's
Schedule B disbursements. Upsert into committee_disbursements_by_recipient.

Freshness gate is per (committee, cycle): if the row set for one cycle is
< FRESHNESS_DAYS old, we skip that cycle. New cycles trigger a fetch.

CLAUDE.md §1.4: raw payloads land under data/raw/_committee_disbursements/
BEFORE parsing — see scripts/fetch_committee_disbursements.py.
CLAUDE.md §1.5: idempotent (INSERT OR REPLACE keyed on the table's PK; FEC
may amend a cycle's aggregates retroactively, so the fresh fetch wins).
CLAUDE.md §1.6: master.db snapshotted before first row write.
CLAUDE.md §6: this is names + amounts only. Never cross-referenced to
legislation, votes, or policy outcomes (Phase 3 territory if ever).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import db
from .fetch_committee_disbursements import (
    DEFAULT_TOP_N,
    fetch_by_recipient,
    parse_by_recipient_row,
)
from .fetch_fec import FECClient
from .enrichment_base import fresh_within_days
from .paths import DATA_DIR, MASTER_DB, relpath


BENEFICIARIES_LOCK = DATA_DIR / ".committee_disbursements_ingest.lock"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── File lock — separate from refresh.py's and the other enrichers' ────────


@contextmanager
def _acquire_lock(path: Path | None = None) -> Iterator[None]:
    """Exclusive lock; raises if another beneficiaries ingest is in flight.

    Default lock path resolved at call time (not function-definition time)
    so monkeypatching BENEFICIARIES_LOCK in tests works.
    """
    if path is None:
        path = BENEFICIARIES_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = path.read_text(encoding="utf-8") if path.exists() else "(empty)"
        raise RuntimeError(
            f"Beneficiaries ingest already running (or stale lock at {path}). "
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


# ─── Per-(committee, cycle) freshness check ─────────────────────────────────


def _cycle_is_fresh(
    conn: sqlite3.Connection, committee_id: str, cycle: int
) -> bool:
    """Has this (committee, cycle) been fetched within FRESHNESS_DAYS?

    Uses MAX(fetched_at) across all recipient rows for the pair — they're
    all written in the same transaction so they share a timestamp, but MAX
    is robust to mixed re-runs across past dev sessions.
    """
    row = conn.execute(
        """
        SELECT MAX(fetched_at) AS f
          FROM committee_disbursements_by_recipient
         WHERE committee_id = ? AND cycle = ?
        """,
        (committee_id, cycle),
    ).fetchone()
    return row is not None and fresh_within_days(row["f"])


# ─── Cycle enumeration per committee ────────────────────────────────────────


def list_cycles_for_committee(
    committee_id: str, db_path: Path = MASTER_DB
) -> list[int]:
    """Cycles FEC has totals for on this committee, sorted ascending.

    We drive beneficiary fetches off committee_totals (Phase 1's table) — if
    FEC didn't report receipts for a cycle, FEC won't report disbursements
    either, so there's nothing to fetch. Cycles are the FEC two_year_transaction
    bucket (even years).
    """
    db.init(db_path)
    with db.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT cycle
              FROM committee_totals
             WHERE committee_id = ?
               AND cycle IS NOT NULL
             ORDER BY cycle
            """,
            (committee_id,),
        ).fetchall()
    return [int(r["cycle"]) for r in rows]


def list_committees_for_beneficiaries(db_path: Path = MASTER_DB) -> list[str]:
    """Every committee_id present in committees that also has totals rows.

    We require a committees row so we don't fetch beneficiaries for a
    recipient that's never been ingested into Phase 1 — that would be a
    schema ordering bug, not a data gap.
    """
    db.init(db_path)
    with db.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT c.committee_id
              FROM committees c
              JOIN committee_totals t ON t.committee_id = c.committee_id
             ORDER BY c.committee_id
            """
        ).fetchall()
    return [r["committee_id"] for r in rows]


# ─── Per-committee ingest ───────────────────────────────────────────────────


def ingest_committee_disbursements(
    committee_id: str,
    *,
    cycles: list[int] | None = None,
    top_n: int = DEFAULT_TOP_N,
    force_refresh: bool = False,
    client: FECClient | None = None,
    db_path: Path = MASTER_DB,
) -> dict:
    """Fetch + upsert the top-N recipients per cycle for one committee.

    cycles=None means "every cycle the committees_totals table has for this
    committee." Otherwise queries only the cycles passed in (used by --cycles
    on the CLI for targeted backfills).

    Per cycle:
      - skip if (committee, cycle) is fresh and not force_refresh
      - fetch top_n FEC rows
      - DELETE existing rows for (committee_id, cycle) and re-INSERT — FEC
        amends aggregates retroactively, so the fresh fetch supersedes the
        prior snapshot. The delete-then-insert is wrapped in a single
        transaction (db.connect's commit-on-success).

    Returns a per-committee summary dict.
    """
    db.init(db_path)
    target_cycles = cycles if cycles is not None else list_cycles_for_committee(
        committee_id, db_path
    )

    summary: dict = {
        "committee_id": committee_id,
        "cycles_attempted": 0,
        "cycles_fetched": 0,
        "cycles_skipped_fresh": 0,
        "cycles_failed": 0,
        "rows_written": 0,
    }

    if not target_cycles:
        return summary

    client = client or FECClient()
    now = _utc_now_iso()

    for cycle in target_cycles:
        summary["cycles_attempted"] += 1

        with db.connect(db_path) as conn:
            if not force_refresh and _cycle_is_fresh(conn, committee_id, cycle):
                summary["cycles_skipped_fresh"] += 1
                continue

        try:
            rows, raw_paths = fetch_by_recipient(
                client, committee_id, cycle, top_n=top_n
            )
        except Exception as e:
            summary["cycles_failed"] += 1
            print(f"[beneficiaries] {committee_id} cycle {cycle} ERROR: {e}")
            continue

        # Project to schema. _last_ raw_path is the one we point rows at —
        # the per-cycle envelope is sufficient to rebuild the cycle from §1.4
        # if needed; FEC reads are deterministic given the same params.
        parsed: list[dict] = []
        for r in rows:
            row = parse_by_recipient_row(committee_id, cycle, r)
            if row is None:
                continue
            parsed.append(row)

        raw_payload_path = relpath(raw_paths[-1]) if raw_paths else ""

        with db.connect(db_path) as conn:
            # Replace this cycle's rows wholesale — FEC may have dropped a
            # recipient between fetches as the spending PAC amended its
            # filings. Keeping stale rows alongside fresh ones would leak
            # ghost beneficiaries into the dashboard.
            conn.execute(
                """
                DELETE FROM committee_disbursements_by_recipient
                 WHERE committee_id = ? AND cycle = ?
                """,
                (committee_id, cycle),
            )
            for p in parsed:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO committee_disbursements_by_recipient (
                        committee_id, cycle, recipient_id, recipient_kind,
                        recipient_name, recipient_party, recipient_office,
                        total_amount, n_transactions,
                        raw_payload_path, fetched_at
                    )
                    VALUES (
                        :committee_id, :cycle, :recipient_id, :recipient_kind,
                        :recipient_name, :recipient_party, :recipient_office,
                        :total_amount, :n_transactions,
                        :raw_payload_path, :fetched_at
                    )
                    """,
                    {
                        **p,
                        "raw_payload_path": raw_payload_path,
                        "fetched_at": now,
                    },
                )
        summary["cycles_fetched"] += 1
        summary["rows_written"] += len(parsed)

    return summary


# ─── All-committee orchestrator ─────────────────────────────────────────────


def ingest_all_committee_disbursements(
    *,
    only: list[str] | None = None,
    cycles: list[int] | None = None,
    top_n: int = DEFAULT_TOP_N,
    force_refresh: bool = False,
    max_count: int | None = None,
    db_path: Path = MASTER_DB,
) -> dict:
    """Walk every enriched committee and ingest beneficiaries for each cycle.

    Per-committee failures are caught and recorded; they do NOT abort the
    run (CLAUDE.md §1.9 — prefer try-again-next-time). A single timed-out
    FEC request shouldn't lose the rest of the batch.

    Returns a summary dict suitable for emitting from the CLI.
    """
    started_at = _utc_now_iso()
    candidates = only or list_committees_for_beneficiaries(db_path)
    if max_count is not None:
        candidates = candidates[:max_count]

    summary: dict = {
        "started_at": started_at,
        "completed_at": None,
        "attempted": 0,
        "fetched": 0,
        "skipped_no_fresh_cycles": 0,
        "failed": 0,
        "failed_ids": [],
        "rows_written": 0,
        "snapshot_path": None,
    }

    if not candidates:
        summary["completed_at"] = _utc_now_iso()
        return summary

    # Snapshot before we touch any rows. CLAUDE.md §1.6.
    snap = db.snapshot(
        f"beneficiaries_ingest_{started_at.replace(':', '-')}",
        db_path,
    )
    summary["snapshot_path"] = str(snap) if snap else None

    with _acquire_lock():
        client: FECClient | None = None
        for cid in candidates:
            summary["attempted"] += 1
            try:
                # Lazy client construction so a "everything still fresh"
                # dry-run doesn't even require FEC_API_KEY.
                if client is None:
                    client = FECClient()
                result = ingest_committee_disbursements(
                    cid,
                    cycles=cycles,
                    top_n=top_n,
                    force_refresh=force_refresh,
                    client=client,
                    db_path=db_path,
                )
                if result["cycles_fetched"] == 0 and result["cycles_skipped_fresh"] > 0:
                    summary["skipped_no_fresh_cycles"] += 1
                if result["cycles_fetched"] > 0:
                    summary["fetched"] += 1
                summary["rows_written"] += result["rows_written"]
                print(
                    f"[beneficiaries] {cid} "
                    f"cycles_fetched={result['cycles_fetched']} "
                    f"skipped={result['cycles_skipped_fresh']} "
                    f"failed={result['cycles_failed']} "
                    f"rows={result['rows_written']}"
                )
            except Exception as e:
                summary["failed"] += 1
                summary["failed_ids"].append(cid)
                print(f"[beneficiaries] {cid} ERROR: {e}")

    summary["completed_at"] = _utc_now_iso()
    return summary

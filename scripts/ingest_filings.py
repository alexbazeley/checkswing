"""Filing enrichment ingest orchestrator.

For every distinct filing_id on a CONFIRMED/PROBABLE donation, fetch the
filing record from OpenFEC and upsert into the filings table. The filings
table backs the donation card's "Full filing PDF" link — replacing the
fec.gov HTML-page stopgap with a real PDF where available.

Idempotent (30-day freshness). Snapshots master.db before writing per
CLAUDE.md §1.6. Batches up to FILINGS_BATCH_SIZE file_numbers per FEC
request to keep the wall-clock manageable.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import db
from .fetch_fec import FECClient
from .fetch_filings import (
    FILINGS_BATCH_SIZE,
    fetch_filings_batch,
    parse_filing_row,
)
from .paths import DATA_DIR, MASTER_DB, relpath


FILINGS_LOCK = DATA_DIR / ".filings_ingest.lock"
FRESHNESS_DAYS = 30


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _acquire_lock(path: Path | None = None) -> Iterator[None]:
    """Exclusive lock; raises if another filings ingest is in flight."""
    if path is None:
        path = FILINGS_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = path.read_text(encoding="utf-8") if path.exists() else "(empty)"
        raise RuntimeError(
            f"Filings ingest already running (or stale lock at {path}). "
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


def list_filings_from_donations(db_path: Path = MASTER_DB) -> list[str]:
    """All distinct non-empty filing_ids referenced by CONFIRMED/PROBABLE donations."""
    db.init(db_path)
    with db.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT filing_id
              FROM donations
             WHERE filing_id IS NOT NULL
               AND filing_id != ''
               AND status IN ('CONFIRMED', 'PROBABLE')
             ORDER BY filing_id
            """
        ).fetchall()
    return [r["filing_id"] for r in rows]


def _is_fresh(conn: sqlite3.Connection, file_number: str) -> bool:
    row = conn.execute(
        "SELECT refreshed_at FROM filings WHERE file_number = ?", (file_number,)
    ).fetchone()
    if row is None or row["refreshed_at"] is None:
        return False
    try:
        refreshed = datetime.fromisoformat(row["refreshed_at"].replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    age_days = (datetime.now(timezone.utc) - refreshed).total_seconds() / 86400
    return age_days < FRESHNESS_DAYS


def _filter_stale(conn: sqlite3.Connection, file_numbers: list[str]) -> list[str]:
    return [fid for fid in file_numbers if not _is_fresh(conn, fid)]


def ingest_filings(
    *,
    only: list[str] | None = None,
    force_refresh: bool = False,
    max_count: int | None = None,
    db_path: Path = MASTER_DB,
) -> dict:
    """Fetch + upsert filings for every donation-referenced filing_id.

    Returns a summary dict. Batches up to FILINGS_BATCH_SIZE per FEC request.
    """
    started_at = _utc_now_iso()
    candidates = only or list_filings_from_donations(db_path)
    if max_count is not None:
        candidates = candidates[:max_count]

    summary: dict = {
        "started_at": started_at,
        "completed_at": None,
        "candidates": len(candidates),
        "stale_to_fetch": 0,
        "fetched": 0,
        "upserted": 0,
        "missing_from_fec": 0,
        "snapshot_path": None,
    }

    if not candidates:
        summary["completed_at"] = _utc_now_iso()
        return summary

    db.init(db_path)

    # Compute the stale subset before acquiring the lock — this is read-only.
    with db.connect(db_path) as conn:
        to_fetch = candidates if force_refresh else _filter_stale(conn, candidates)
    summary["stale_to_fetch"] = len(to_fetch)

    if not to_fetch:
        summary["completed_at"] = _utc_now_iso()
        return summary

    snap = db.snapshot(f"filings_ingest_{started_at.replace(':', '-')}", db_path)
    summary["snapshot_path"] = str(snap) if snap else None

    client = FECClient()
    upserted_ids: set[str] = set()
    with _acquire_lock():
        # Batch through to_fetch
        for i in range(0, len(to_fetch), FILINGS_BATCH_SIZE):
            batch = to_fetch[i : i + FILINGS_BATCH_SIZE]
            label = f"batch_{i // FILINGS_BATCH_SIZE:04d}"
            try:
                results, raw_paths = fetch_filings_batch(client, batch, batch_label=label)
            except Exception as e:
                print(f"[filings] batch {label} ERROR: {e}")
                continue
            summary["fetched"] += len(results)
            if not raw_paths:
                continue
            raw_payload_path = relpath(raw_paths[-1])  # most recent page; envelope covers all
            now = _utc_now_iso()
            with db.connect(db_path) as conn:
                for r in results:
                    parsed = parse_filing_row(r)
                    if not parsed["file_number"]:
                        continue
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO filings (
                            file_number, pdf_url, form_type, document_type,
                            document_type_full, filed_date, receipt_date,
                            coverage_start_date, coverage_end_date,
                            committee_id, committee_name,
                            is_amended, amendment_chain, cycle,
                            raw_payload_path, fetched_at, refreshed_at
                        )
                        VALUES (
                            :file_number, :pdf_url, :form_type, :document_type,
                            :document_type_full, :filed_date, :receipt_date,
                            :coverage_start_date, :coverage_end_date,
                            :committee_id, :committee_name,
                            :is_amended, :amendment_chain, :cycle,
                            :raw_payload_path, :fetched_at, :refreshed_at
                        )
                        """,
                        {
                            **parsed,
                            "raw_payload_path": raw_payload_path,
                            "fetched_at": now,
                            "refreshed_at": now,
                        },
                    )
                    upserted_ids.add(parsed["file_number"])
            print(
                f"[filings] {label}: requested {len(batch)}, got {len(results)} results, "
                f"upserted ({len(upserted_ids)} cumulative)"
            )

    summary["upserted"] = len(upserted_ids)
    summary["missing_from_fec"] = len(set(to_fetch) - upserted_ids)
    summary["completed_at"] = _utc_now_iso()
    return summary

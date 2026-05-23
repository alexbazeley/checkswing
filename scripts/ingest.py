"""Ingestion orchestrator.

Per CLAUDE.md §2 workflow:
  1. Validate owner YAMLs (validate_owners)
  2. Snapshot the master DB (db.snapshot)
  3. Refresh entities table from YAMLs
  4. Fetch via fetch_fec (raw payloads persisted before parsing)
  5. Classify each record via resolve_entities
  6. Write CONFIRMED + PROBABLE → donations
  7. Write UNCERTAIN → review_queue + REVIEW_QUEUE.md
  8. Log the run → ingestion_runs + PROVENANCE_LOG.md

Dry-run mode: steps 1-5 run, raw payloads ARE still persisted (CLAUDE.md §1.4
applies regardless of whether we write to the DB — raw is the ground truth),
but steps 6-8 are skipped. The classifier output is returned for inspection.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

from . import db
from .fetch_fec import DEFAULT_MIN_DATE, FECClient, load_raw_payloads
from .paths import OWNERS_DIR, PROVENANCE_LOG, REVIEW_QUEUE_MD
from .resolve_entities import (
    CONFIRMED,
    PROBABLE,
    UNCERTAIN,
    Classification,
    classify,
)
from .validate_owners import format_report, validate_all


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_owner(slug: str) -> dict:
    path = OWNERS_DIR / f"{slug}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"owners/{slug}.yaml not found")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _record_to_donation_row(
    record: dict,
    classification: Classification,
    ingested_at: str,
) -> dict:
    """Map a raw FEC record + classification → donations table row."""
    return {
        "transaction_id": str(
            record.get("transaction_id") or record.get("sub_id") or ""
        ),
        "entity_slug": classification.entity_slug,
        "entity_kind": classification.entity_kind,
        "parent_owner_slug": classification.parent_owner_slug,
        "status": classification.status,
        "status_reason": classification.status_reason,
        "signals_matched": json.dumps(classification.signals_matched),
        "contributor_name_raw": record.get("contributor_name") or "",
        "contributor_employer_raw": record.get("contributor_employer") or "",
        "contributor_occupation_raw": record.get("contributor_occupation") or "",
        "contributor_city": record.get("contributor_city") or "",
        "contributor_state": record.get("contributor_state") or "",
        "contributor_zip": record.get("contributor_zip") or "",
        "recipient_committee_id": record.get("committee_id") or "",
        "recipient_committee_name": record.get("committee", {}).get("name")
        if isinstance(record.get("committee"), dict)
        else record.get("committee_name") or "",
        "recipient_candidate_id": record.get("candidate_id") or "",
        "recipient_candidate_name": record.get("candidate_name") or "",
        "recipient_party": (record.get("committee") or {}).get("party")
        if isinstance(record.get("committee"), dict)
        else None,
        "recipient_office": record.get("candidate_office") or None,
        "amount": float(record.get("contribution_receipt_amount") or 0.0),
        "date": str(record.get("contribution_receipt_date") or "")[:10],
        "election_cycle": record.get("two_year_transaction_period"),
        "report_type": record.get("report_type") or None,
        "filing_id": str(record.get("file_number") or record.get("report_id") or ""),
        "raw_payload_path": record.get("_raw_payload_path") or "",
        "ingested_at": ingested_at,
    }


def _record_to_review_row(
    record: dict,
    classification: Classification,
    queued_at: str,
) -> dict:
    return {
        "transaction_id": str(
            record.get("transaction_id") or record.get("sub_id") or ""
        ),
        "entity_slug": classification.entity_slug,
        "reason": classification.status_reason,
        "raw_payload_path": record.get("_raw_payload_path") or "",
        "queued_at": queued_at,
    }


def _append_review_queue_md(items: list[tuple[dict, Classification]], run_id: str) -> None:
    if not items:
        return
    REVIEW_QUEUE_MD.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"\n<!-- ingestion run {run_id} — {_utc_now_iso()} -->")
    for record, c in items:
        txn = record.get("transaction_id") or record.get("sub_id") or "?"
        committee = ""
        if isinstance(record.get("committee"), dict):
            committee = record["committee"].get("name") or ""
        committee = committee or record.get("committee_name") or ""
        committee_id = record.get("committee_id") or ""
        lines.append(f"\n### {txn} — {c.entity_slug} — {_utc_now_iso()}")
        lines.append("")
        lines.append(f"- Reason: {c.status_reason}")
        lines.append(f"- Raw payload: {record.get('_raw_payload_path', '')}")
        lines.append(f"- Contributor name: {record.get('contributor_name', '')}")
        lines.append(f"- Contributor employer: {record.get('contributor_employer', '')}")
        lines.append(f"- Contributor occupation: {record.get('contributor_occupation', '')}")
        city = record.get("contributor_city", "")
        state = record.get("contributor_state", "")
        zip_ = record.get("contributor_zip", "")
        lines.append(f"- Contributor city/state/zip: {city} / {state} / {zip_}")
        cand = record.get("candidate_name") or ""
        party = (record.get("committee") or {}).get("party") if isinstance(record.get("committee"), dict) else ""
        lines.append(f"- Recipient: {committee} ({committee_id}); candidate: {cand}; party: {party}")
        amt = record.get("contribution_receipt_amount")
        d = str(record.get("contribution_receipt_date") or "")[:10]
        lines.append(f"- Amount / date: ${amt} / {d}")
        lines.append("")
        lines.append(f"**Resolution**: pending")
        lines.append(f"**Resolved at**: —")
        lines.append(f"**Resolved by**: —")
        lines.append(f"**Reason**: —")
    # Append under the "Open" section. We use a simple append; a real
    # operator pass can reorganize.
    existing = REVIEW_QUEUE_MD.read_text(encoding="utf-8") if REVIEW_QUEUE_MD.exists() else ""
    REVIEW_QUEUE_MD.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")


def _append_provenance_log(run_summary: dict) -> None:
    PROVENANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    block = []
    ts = _utc_now_iso()
    block.append(f"\n### {ts[:10]} — INGESTION")
    block.append("")
    for k in (
        "run_id",
        "entity_slug",
        "dry_run",
        "period_start",
        "period_end",
        "name_variants_queried",
        "api_calls_made",
        "records_fetched",
        "confirmed_count",
        "probable_count",
        "uncertain_count",
        "snapshot_path",
    ):
        if k in run_summary:
            v = run_summary[k]
            block.append(f"- **{k}**: `{v}`")
    if run_summary.get("notes"):
        block.append(f"- **notes**: {run_summary['notes']}")
    PROVENANCE_LOG.write_text(existing + "\n".join(block) + "\n", encoding="utf-8")


def ingest_entity(
    slug: str,
    *,
    dry_run: bool = False,
    min_date: str = DEFAULT_MIN_DATE,
    max_pages: int | None = None,
    process_related_entities: bool = False,
    state_filter: bool = True,
    from_raw: bool = False,
) -> dict:
    """Run the full pipeline for one entity.

    Returns a summary dict (counts, run_id, etc).
    """
    # ── Step 1: validate ───────────────────────────────────────────────────
    results = validate_all()
    failing = [r for r in results if not r.ok]
    if failing:
        print(format_report(failing))
        raise RuntimeError(
            f"validation failed for {len(failing)} owner YAML(s) — fix before ingesting"
        )

    owner = _load_owner(slug)

    # ── Step 2: snapshot ───────────────────────────────────────────────────
    run_id = uuid.uuid4().hex[:8]
    started_at = _utc_now_iso()
    snapshot_path = None
    if not dry_run:
        db.init()
        snap = db.snapshot(run_id)
        snapshot_path = str(snap) if snap else None

    # ── Step 3: refresh entities ───────────────────────────────────────────
    if not dry_run:
        db.refresh_entities()

    # ── Step 4: fetch ──────────────────────────────────────────────────────
    name_variants = owner.get("name_variants") or []
    if not name_variants:
        raise RuntimeError(f"owner {slug} has no name_variants")

    states: list[str] | None = None
    if state_filter:
        states = list((owner.get("verifying_signals") or {}).get("states") or []) or None

    api_calls_made = 0
    if from_raw:
        print(f"[{slug}] Reading records from existing raw payloads (no FEC calls)…")
        records, raw_paths = load_raw_payloads(slug)
        print(f"[{slug}] Loaded {len(records)} unique records from {len(raw_paths)} raw file(s).")
    else:
        client = FECClient()
        state_label = f" states={states}" if states else " (no state filter)"
        print(f"[{slug}] Fetching schedule_a for {len(name_variants)} name variants since {min_date}{state_label}…")
        records, raw_paths = client.fetch_all_name_variants(
            slug, name_variants, min_date=min_date, max_pages=max_pages, states=states
        )
        api_calls_made = client.calls_made
        print(f"[{slug}] Fetched {len(records)} unique records ({api_calls_made} API calls).")
        print(f"[{slug}] Raw payloads persisted to {len(raw_paths)} file(s) in data/raw/{slug}/")

    # ── Step 5: classify ───────────────────────────────────────────────────
    confirmed: list[tuple[dict, Classification]] = []
    probable: list[tuple[dict, Classification]] = []
    uncertain: list[tuple[dict, Classification]] = []
    skipped_no_name_match = 0

    for r in records:
        c = classify(r, owner, process_related_entities=process_related_entities)
        if c is None:
            skipped_no_name_match += 1
            continue
        if c.status == CONFIRMED:
            confirmed.append((r, c))
        elif c.status == PROBABLE:
            probable.append((r, c))
        elif c.status == UNCERTAIN:
            uncertain.append((r, c))

    print(f"[{slug}] Classification: CONFIRMED={len(confirmed)} · PROBABLE={len(probable)} · UNCERTAIN={len(uncertain)} · skipped(name no-match)={skipped_no_name_match}")

    completed_at = _utc_now_iso()
    summary: dict = {
        "run_id": run_id,
        "entity_slug": slug,
        "started_at": started_at,
        "completed_at": completed_at,
        "period_start": min_date,
        "period_end": None,
        "name_variants_queried": json.dumps(name_variants),
        "api_calls_made": api_calls_made,
        "records_fetched": len(records),
        "confirmed_count": len(confirmed),
        "probable_count": len(probable),
        "uncertain_count": len(uncertain),
        "snapshot_path": snapshot_path,
        "notes": (
            f"skipped(no-name-match)={skipped_no_name_match}"
            + (" · FROM-RAW" if from_raw else (f" · states={states}" if states else " · NO STATE FILTER"))
            + (" · DRY RUN" if dry_run else "")
        ),
        "dry_run": 1 if dry_run else 0,
    }

    if dry_run:
        print(f"[{slug}] DRY RUN — no DB writes. Raw payloads were still persisted.")
        return summary

    # ── Steps 6-7: write donations + review queue ──────────────────────────
    with db.connect() as conn:
        for record, c in confirmed + probable:
            row = _record_to_donation_row(record, c, completed_at)
            if not row["transaction_id"]:
                continue
            db.insert_donation(conn, row)
        for record, c in uncertain:
            review_row = _record_to_review_row(record, c, completed_at)
            if not review_row["transaction_id"]:
                continue
            db.insert_review_queue(conn, review_row)
        db.insert_ingestion_run(conn, summary)

    # ── Step 8: append markdown logs ───────────────────────────────────────
    _append_review_queue_md(uncertain, run_id)
    _append_provenance_log(summary)

    return summary


def reclassify_entity(slug: str, *, reason: str = "") -> dict:
    """Re-run classification for an entity against its existing raw payloads.

    Workflow:
      1. Snapshot the master DB (audit safety net before any deletion).
      2. Delete this entity's rows from `donations` and `review_queue`.
      3. Run ingest_entity(slug, from_raw=True) — re-reads raw payloads,
         re-applies the (possibly updated) owner YAML, writes fresh rows.
      4. Append a DELETION entry to PROVENANCE_LOG.md documenting the wipe.
         The ingest itself appends its own INGESTION entry.

    Use after editing the owner YAML (signal additions, negative_signals,
    related_entity changes) to apply the new rules without re-hitting FEC.

    CLAUDE.md §1.10 ("no deletion without record") is satisfied via:
      - the pre-wipe DB snapshot (rows are recoverable)
      - the DELETION log entry below
      - the raw payloads in data/raw/<slug>/ which are the ground truth.
    """
    db.init()

    # Snapshot before deletion.
    snap_id = f"pre-reclassify-{slug}"
    snap_path = db.snapshot(snap_id)

    # Count what we're wiping (for the audit entry).
    with db.connect() as conn:
        donations_before = conn.execute(
            "SELECT COUNT(*) FROM donations WHERE entity_slug = ?", (slug,)
        ).fetchone()[0]
        review_before = conn.execute(
            "SELECT COUNT(*) FROM review_queue WHERE entity_slug = ?", (slug,)
        ).fetchone()[0]
        resolved_before = conn.execute(
            "SELECT COUNT(*) FROM review_queue WHERE entity_slug = ? AND resolution IS NOT NULL",
            (slug,),
        ).fetchone()[0]

        # Wipe.
        conn.execute("DELETE FROM donations    WHERE entity_slug = ?", (slug,))
        conn.execute("DELETE FROM review_queue WHERE entity_slug = ?", (slug,))

    # Log the deletion (DELETION entry — distinct from the INGESTION entry
    # that ingest_entity will append for the re-classification itself).
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    ts = _utc_now_iso()
    block = [
        f"\n### {ts[:10]} — DELETION — reclassify {slug}",
        "",
        f"- **entity_slug**: `{slug}`",
        f"- **reason**: {reason or 'reclassification after signal/schema change'}",
        f"- **rows_deleted_donations**: `{donations_before}`",
        f"- **rows_deleted_review_queue**: `{review_before}` (of which {resolved_before} had resolutions)",
        f"- **snapshot_path**: `{snap_path}`",
        f"- **note**: Rows are recoverable from the snapshot above and from data/raw/{slug}/ payloads. Re-classification follows in the next INGESTION entry.",
        "",
    ]
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")

    # Run the classify-from-raw path.
    summary = ingest_entity(slug, from_raw=True)
    summary["_reclassify"] = {
        "rows_deleted_donations": donations_before,
        "rows_deleted_review_queue": review_before,
        "resolved_items_lost": resolved_before,
        "pre_wipe_snapshot": str(snap_path) if snap_path else None,
        "reason": reason,
    }
    return summary

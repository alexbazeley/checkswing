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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml
from ruamel.yaml import YAML as _RoundTripYAML

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


def _utc_today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_owner(slug: str) -> dict:
    path = OWNERS_DIR / f"{slug}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"owners/{slug}.yaml not found")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolve_min_date(owner: dict, explicit_min_date: str | None, full_refetch: bool) -> tuple[str, str]:
    """Resolve the effective min_date for this ingestion.

    Returns (min_date, source) where source explains the choice for logging:
      - "user --min-date" — explicit CLI override
      - "audit.last_ingestion" — incremental refresh from prior run
      - "--full-refetch" — explicit override back to project floor
      - "default (no prior ingestion)" — first-run for this owner
    """
    if full_refetch:
        return DEFAULT_MIN_DATE, "--full-refetch"
    if explicit_min_date is not None:
        return explicit_min_date, "user --min-date"
    last = (owner.get("audit") or {}).get("last_ingestion")
    if last:
        # YAML may load this as a date object or a string.
        return str(last), "audit.last_ingestion"
    return DEFAULT_MIN_DATE, "default (no prior ingestion)"


def _write_audit_last_ingestion(slug: str, date_iso: str) -> None:
    """Patch owners/<slug>.yaml so audit.last_ingestion = date_iso.

    Uses ruamel.yaml round-trip to preserve comments and ordering. This is the
    only field refresh.py / ingest is allowed to mutate on the YAML — signal
    blocks remain read-only (CLAUDE.md §1.7).
    """
    path = OWNERS_DIR / f"{slug}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"owners/{slug}.yaml not found")
    yaml_rt = _RoundTripYAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.width = 4096  # don't re-wrap long lines
    with path.open("r", encoding="utf-8") as f:
        data = yaml_rt.load(f)
    if data is None:
        raise ValueError(f"owners/{slug}.yaml is empty or unparseable")
    audit = data.get("audit")
    if audit is None:
        # Add the audit block. Rare — every existing owner has it.
        data["audit"] = {"last_ingestion": date_iso}
    else:
        audit["last_ingestion"] = date_iso
    with path.open("w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)


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
        # v3 per-transaction FEC fields. Persisted at ingest time so the
        # dashboard doesn't need to re-read raw payloads at build time, which
        # broke when GHA runner-side payloads got destroyed. Recipient
        # committee type can live top-level or nested under committee{}; prefer
        # top-level (cleaner pull), fall back to committee.committee_type.
        "image_number": (
            str(record["image_number"]) if record.get("image_number") is not None else None
        ),
        "pdf_url": record.get("pdf_url") or None,
        "filing_form": record.get("filing_form") or None,
        "line_number": (
            str(record["line_number"]) if record.get("line_number") is not None else None
        ),
        "receipt_type_full": record.get("receipt_type_full") or None,
        "recipient_committee_type": _committee_type_of(record),
    }


def _committee_type_of(record: dict) -> str | None:
    """Resolve recipient_committee_type from a FEC record.

    Some FEC responses have it top-level; others only under nested committee{}.
    Prefer top-level; fall back to the nested struct's committee_type. Returns
    None when neither is present.
    """
    top = record.get("recipient_committee_type")
    if top:
        return top
    cmt = record.get("committee")
    if isinstance(cmt, dict):
        return cmt.get("committee_type")
    return None


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
    min_date: str | None = None,
    max_pages: int | None = None,
    process_related_entities: bool = False,
    state_filter: bool = True,
    from_raw: bool = False,
    full_refetch: bool = False,
    chunk_by_cycle: bool = False,
    force_resume: bool = False,
) -> dict:
    """Run the full pipeline for one entity.

    Returns a summary dict (counts, run_id, etc).

    min_date semantics:
      - None (default): use owner's audit.last_ingestion if set; else DEFAULT_MIN_DATE.
      - explicit str: use that exact date.
      - full_refetch=True: ignore everything else and use DEFAULT_MIN_DATE.

    On successful (non-dry-run, non-from-raw) completion, owner's
    audit.last_ingestion is patched to today's UTC date so the next run
    fetches incrementally. CLAUDE.md §1.7 — signal blocks are NOT touched.
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

    # Resolve effective min_date from CLI + YAML state.
    effective_min_date, min_date_source = _resolve_min_date(owner, min_date, full_refetch)

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
        chunk_label = " · chunk-by-cycle" if chunk_by_cycle else ""
        print(
            f"[{slug}] Fetching schedule_a for {len(name_variants)} name variants "
            f"since {effective_min_date} ({min_date_source}){state_label}{chunk_label}…"
        )
        if min_date_source == "audit.last_ingestion":
            print(f"[{slug}]   (incremental refresh — use --full-refetch for complete history)")
        records, raw_paths = client.fetch_all_name_variants(
            slug,
            name_variants,
            min_date=effective_min_date,
            max_pages=max_pages,
            states=states,
            chunk_by_cycle=chunk_by_cycle,
            force_resume=force_resume,
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
    # period_end = latest contribution date among all classified records in
    # this run; falls back to None if nothing matched at all.
    all_dates = [
        d for d in (
            str(r.get("contribution_receipt_date") or "")[:10]
            for r, _ in confirmed + probable + uncertain
        ) if d
    ]
    period_end = max(all_dates) if all_dates else None
    summary: dict = {
        "run_id": run_id,
        "entity_slug": slug,
        "started_at": started_at,
        "completed_at": completed_at,
        "period_start": effective_min_date,
        "period_end": period_end,
        "name_variants_queried": json.dumps(name_variants),
        "api_calls_made": api_calls_made,
        "records_fetched": len(records),
        "confirmed_count": len(confirmed),
        "probable_count": len(probable),
        "uncertain_count": len(uncertain),
        "snapshot_path": snapshot_path,
        "notes": (
            f"skipped(no-name-match)={skipped_no_name_match}"
            + f" · min_date={min_date_source}"
            + (" · FROM-RAW" if from_raw else (f" · states={states}" if states else " · NO STATE FILTER"))
            + (" · chunk-by-cycle" if chunk_by_cycle else "")
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

    # ── Step 9: write audit.last_ingestion ─────────────────────────────────
    # Records today's UTC date as the freshness watermark so the NEXT ingest
    # fetches incrementally (B.5 reads this back). Skipped for `from_raw`
    # runs — those don't fetch from FEC, so they shouldn't move the watermark.
    # CLAUDE.md §1.7 boundary: this only touches audit.last_ingestion;
    # signal blocks remain untouched.
    if not from_raw:
        today_iso = _utc_today_iso()
        _write_audit_last_ingestion(slug, today_iso)
        summary["audit_last_ingestion_set"] = today_iso

    return summary


def reclassify_entity(slug: str, *, reason: str = "", include_related: bool = False) -> dict:
    """Re-run classification for an entity against its existing raw payloads.

    Workflow:
      1. Snapshot the master DB (audit safety net before any deletion).
      2. Delete this entity's rows from `donations` and `review_queue`. If
         `include_related` is True, also delete rows whose `parent_owner_slug`
         is this entity (the related-entity rows roll up under the owner).
      3. Run ingest_entity(slug, from_raw=True, process_related_entities=...).
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
            "SELECT COUNT(*) FROM donations WHERE entity_slug = ? OR parent_owner_slug = ?",
            (slug, slug),
        ).fetchone()[0]
        review_before = conn.execute(
            "SELECT COUNT(*) FROM review_queue WHERE entity_slug = ?", (slug,)
        ).fetchone()[0]
        resolved_before = conn.execute(
            "SELECT COUNT(*) FROM review_queue WHERE entity_slug = ? AND resolution IS NOT NULL",
            (slug,),
        ).fetchone()[0]

        # Wipe both owner-attributed rows and any related-entity rows that
        # roll up to this owner. parent_owner_slug = slug catches spouses /
        # children / business entities; entity_slug = slug catches the owner.
        conn.execute(
            "DELETE FROM donations WHERE entity_slug = ? OR parent_owner_slug = ?",
            (slug, slug),
        )
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
        f"- **include_related**: `{include_related}`",
        f"- **snapshot_path**: `{snap_path}`",
        f"- **note**: Rows are recoverable from the snapshot above and from data/raw/{slug}/ payloads. Re-classification follows in the next INGESTION entry.",
        "",
    ]
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")

    # Run the classify-from-raw path.
    summary = ingest_entity(slug, from_raw=True, process_related_entities=include_related)
    summary["_reclassify"] = {
        "rows_deleted_donations": donations_before,
        "rows_deleted_review_queue": review_before,
        "resolved_items_lost": resolved_before,
        "pre_wipe_snapshot": str(snap_path) if snap_path else None,
        "reason": reason,
        "include_related": include_related,
    }
    return summary

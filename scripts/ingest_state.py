"""Phase 4 state-contribution ingestion orchestrator (data/state.db).

Mirrors scripts/ingest.py for the federal pipeline, but for state-portal data.
The flow per owner:

  1. snapshot state.db (gated mutation, GOVERNANCE.md §1.6);
  2. scan the portal extract's receipt rows for this owner's name_variants,
     mapping each via the source adapter (e.g. calaccess_adapter) into the record
     shape the UNCHANGED classifier reads;
  3. run resolve_entities.classify — same three-tier verdict as federal;
  4. apply durable overrides (state_manual_attributions: EXCLUDED drops the row,
     CONFIRMED/PROBABLE force the status; state_review_resolutions: DISCARDED
     suppresses re-queuing);
  5. write CONFIRMED/PROBABLE → state_donations (recipient resolved to a
     state_filer), UNCERTAIN → state_review_queue;
  6. log to PROVENANCE_LOG + state_ingestion_runs.

The extract rows and the recipient resolver are INJECTED (the fetcher supplies
real ones from CAL-ACCESS; tests supply fixtures), so this module is pure of any
network or giant-file dependency and fully unit-testable.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable

import yaml

from . import calaccess_adapter, state_db
from .paths import OWNERS_DIR, PROVENANCE_LOG, relpath
from .resolve_entities import CONFIRMED, EXCLUDED, PROBABLE, UNCERTAIN, classify

# A recipient resolver maps a raw receipt row → recipient identity dict:
#   {"filer_id": str|None, "name": str, "type": str|None}
# The fetcher builds this from the portal's filer/cover-page lookup; tests pass a
# simple stub. A resolver that can't identify the filer returns name="" → the row
# still records (recipient unknown is honest), keyed by source_filing_id.
RecipientResolver = Callable[[dict], dict]

# An adapter maps a raw receipt row → the classifier record shape. Defaults to the
# CAL-ACCESS adapter; a future state passes its own.
RecordAdapter = Callable[[dict], dict]


@dataclass
class IngestStateResult:
    slug: str
    jurisdiction: str
    source: str
    records_scanned: int = 0
    confirmed: int = 0
    probable: int = 0
    uncertain: int = 0
    excluded: int = 0
    skipped_no_date: int = 0
    superseded: int = 0
    snapshot_path: str | None = None
    dry_run: bool = False
    rows: list[dict] = field(default_factory=list)  # populated on dry_run for inspection

    @property
    def attributed(self) -> int:
        return self.confirmed + self.probable


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_owner(slug: str) -> dict:
    path = OWNERS_DIR / f"{slug}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"owners/{slug}.yaml not found")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _default_recipient(rcpt: dict) -> dict:
    return {"filer_id": None, "name": "", "type": None}


def ingest_state_entity(
    slug: str,
    *,
    rcpt_rows: Iterable[dict],
    recipient_resolver: RecipientResolver = _default_recipient,
    record_adapter: RecordAdapter = calaccess_adapter.to_classifier_record,
    row_builder=calaccess_adapter.to_state_donation_row,
    jurisdiction: str = "CA",
    source: str = "CAL-ACCESS",
    raw_payload_path: str = "",
    extract_label: str = "",
    discovery_source: str | None = None,
    dry_run: bool = False,
    db_path=state_db.STATE_DB,
) -> IngestStateResult:
    """Classify + persist one owner's state contributions from a portal extract.

    On dry_run, classifies and reports counts but writes nothing (the candidate
    rows are returned on the result for inspection). Raw extract persistence is
    the fetcher's job (GOVERNANCE.md §1.4) and happens regardless of dry_run.
    """
    owner = _load_owner(slug)
    res = IngestStateResult(slug=slug, jurisdiction=jurisdiction, source=source, dry_run=dry_run)
    ingested_at = _utc_now_iso()
    run_id = uuid.uuid4().hex[:8]

    if not dry_run:
        res.snapshot_path = _maybe_snapshot(run_id, db_path)

    # Durable overrides are read once up front.
    overrides: dict[str, str] = {}
    discarded: set[str] = set()
    if not dry_run:
        with state_db.connect(db_path) as conn:
            overrides = state_db.state_manual_attributions_for_slug(conn, slug)
            discarded = state_db.discarded_txns_for_slug(conn, slug)

    superseded_events: list[tuple[str, str, str]] = []

    def _process(conn) -> None:
        for rcpt in rcpt_rows:
            res.records_scanned += 1
            record = record_adapter(rcpt)
            c = classify(record, owner)
            if c is None:
                continue  # name doesn't match — filtered out, never enters DB/queue

            state_txn_id = state_db.compose_state_txn_id(
                jurisdiction=jurisdiction,
                source=source,
                source_filing_id=str(rcpt.get("FILING_ID") or "").strip() or None,
                source_tran_id=str(rcpt.get("TRAN_ID") or "").strip() or None,
            )

            status = c.status
            reason = c.status_reason
            # Apply durable manual override (survives reclassify).
            override = overrides.get(state_txn_id)
            if override == EXCLUDED:
                res.excluded += 1
                continue
            if override in (CONFIRMED, PROBABLE):
                status = override
                reason = f"manual attribution ({override})"

            recipient = recipient_resolver(rcpt)
            row = row_builder(
                rcpt,
                state_txn_id=state_txn_id,
                status=status,
                status_reason=reason,
                signals_matched_json=json.dumps(c.signals_matched),
                entity_slug=c.entity_slug or slug,
                entity_kind=c.entity_kind,
                parent_owner_slug=c.parent_owner_slug,
                recipient_filer_id=recipient.get("filer_id"),
                recipient_name=recipient.get("name") or "",
                recipient_type=recipient.get("type"),
                raw_payload_path=raw_payload_path,
                ingested_at=ingested_at,
                jurisdiction=jurisdiction,
                source=source,
                discovery_source=discovery_source,
            )

            # Provenance gate (GOVERNANCE.md §1.3): a contribution with no parseable
            # date can't be placed on a timeline or safely superseded → route to the
            # review queue rather than inventing a date, never dropped silently.
            if not row.get("date"):
                res.skipped_no_date += 1
                if not dry_run:
                    state_db.insert_state_review_queue(
                        conn,
                        {
                            "state_txn_id": state_txn_id,
                            "entity_slug": slug,
                            "jurisdiction": jurisdiction,
                            "source": source,
                            "reason": "unparseable contribution date — verify against portal filing",
                            "raw_payload_path": raw_payload_path,
                            "queued_at": ingested_at,
                        },
                    )
                continue

            if status == UNCERTAIN:
                res.uncertain += 1
                if dry_run:
                    res.rows.append(row)
                    continue
                if state_txn_id in discarded:
                    continue  # standing DISCARDED verdict suppresses re-queue
                state_db.insert_state_review_queue(
                    conn,
                    {
                        "state_txn_id": state_txn_id,
                        "entity_slug": slug,
                        "jurisdiction": jurisdiction,
                        "source": source,
                        "reason": reason,
                        "raw_payload_path": raw_payload_path,
                        "queued_at": ingested_at,
                    },
                )
                continue

            # CONFIRMED / PROBABLE
            if status == CONFIRMED:
                res.confirmed += 1
            else:
                res.probable += 1
            if dry_run:
                res.rows.append(row)
                continue
            if recipient.get("filer_id"):
                state_db.upsert_state_filer(
                    conn,
                    {
                        "filer_id": recipient["filer_id"],
                        "jurisdiction": jurisdiction,
                        "source": source,
                        "name": recipient.get("name") or "",
                        "filer_type": recipient.get("type"),
                        "party": None,
                        "office": None,
                        "raw_payload_path": raw_payload_path,
                        "fetched_at": ingested_at,
                        "refreshed_at": ingested_at,
                    },
                )
            action, sreason = state_db.insert_state_donation(conn, row)
            if action == "superseded":
                superseded_events.append((state_txn_id, slug, sreason or ""))

    if dry_run:
        # No DB writes: every write path in _process is guarded by `if not dry_run`,
        # so conn is never touched. Counts + candidate rows are still produced.
        _process(None)
    else:
        with state_db.connect(db_path) as conn:
            _process(conn)
        res.superseded = len(superseded_events)
        _append_state_provenance(res, run_id, extract_label)
        _append_supersession_log(superseded_events, run_id)
        with state_db.connect(db_path) as conn:
            state_db.insert_state_ingestion_run(
                conn,
                {
                    "run_id": run_id,
                    "entity_slug": slug,
                    "jurisdiction": jurisdiction,
                    "source": source,
                    "started_at": ingested_at,
                    "completed_at": _utc_now_iso(),
                    "extract_label": extract_label,
                    "name_variants_queried": json.dumps(owner.get("name_variants") or []),
                    "records_scanned": res.records_scanned,
                    "confirmed_count": res.confirmed,
                    "probable_count": res.probable,
                    "uncertain_count": res.uncertain,
                    "snapshot_path": res.snapshot_path,
                    "notes": _result_note(res),
                    "dry_run": 0,
                },
            )
    return res


def reclassify_state_entity(
    slug: str,
    *,
    rcpt_rows: Iterable[dict],
    reason: str = "",
    db_path=state_db.STATE_DB,
    **kwargs,
) -> IngestStateResult:
    """Wipe this owner's state_donations + open review-queue, then re-ingest from
    the supplied extract rows. Durable verdicts (resolutions, manual attributions)
    survive (state_db.delete_donations_for_slug leaves them). Snapshots + logs.
    """
    run_id = uuid.uuid4().hex[:8]
    _maybe_snapshot(run_id, db_path)
    with state_db.connect(db_path) as conn:
        state_db.delete_donations_for_slug(conn, slug)
    return ingest_state_entity(
        slug,
        rcpt_rows=rcpt_rows,
        extract_label=f"reclassify: {reason}" if reason else "reclassify",
        db_path=db_path,
        **kwargs,
    )


def _maybe_snapshot(run_id: str, db_path) -> str | None:
    p = state_db.snapshot(run_id, db_path)
    if not p:
        return None
    try:
        return relpath(p)
    except ValueError:
        return str(p)  # snapshot dir outside the repo (e.g. under test) — record absolute


def _result_note(res: IngestStateResult) -> str:
    bits = [f"scanned={res.records_scanned}"]
    if res.excluded:
        bits.append(f"excluded={res.excluded}")
    if res.skipped_no_date:
        bits.append(f"skipped_no_date={res.skipped_no_date}")
    if res.superseded:
        bits.append(f"superseded={res.superseded}")
    return ", ".join(bits)


def _append_state_provenance(res: IngestStateResult, run_id: str, extract_label: str) -> None:
    PROVENANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    ts = _utc_now_iso()
    block = [f"\n### {ts[:10]} — STATE_INGESTION", ""]
    fields = [
        ("run_id", run_id),
        ("entity_slug", res.slug),
        ("jurisdiction", res.jurisdiction),
        ("source", res.source),
        ("extract_label", extract_label),
        ("records_scanned", res.records_scanned),
        ("confirmed_count", res.confirmed),
        ("probable_count", res.probable),
        ("uncertain_count", res.uncertain),
        ("snapshot_path", res.snapshot_path),
    ]
    for k, v in fields:
        block.append(f"- **{k}**: `{v}`")
    note = _result_note(res)
    if note:
        block.append(f"- **notes**: {note}")
    PROVENANCE_LOG.write_text(existing + "\n".join(block) + "\n", encoding="utf-8")


def _append_supersession_log(events: list[tuple[str, str, str]], run_id: str) -> None:
    if not events:
        return
    PROVENANCE_LOG.parent.mkdir(parents=True, exist_ok=True)
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    ts = _utc_now_iso()
    block = [f"\n### {ts[:10]} — STATE_SUPERSESSION — run {run_id}", ""]
    for txn, slug, reason in events:
        block.append(f"- `{txn}` ({slug}): {reason}")
    block.append("")
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")

"""Command-line interface for the archive."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml
from tabulate import tabulate

from . import db
from .apply_committee_external_links import apply_external_links
from .audit import audit_slug
from .backfill_donation_image_fields import backfill as backfill_donation_image_fields
from .backfill_memo_fields import backfill as backfill_memo_fields
from .backfill_subid import backfill as backfill_subid
from .export import export_aggregate, export_entity, export_household
from .ingest import ingest_entity, reclassify_entity, reclassify_in_place
from .ingest_committee_disbursements import (
    ingest_all_committee_disbursements,
)
from .ingest_committees import ingest_all_committees
from .ingest_filings import ingest_filings as ingest_filings_orchestrator
from .paths import OWNERS_DIR
from .provenance import append_provenance
from .queue_stats import queue_stats_report
from .refresh import refresh_all, select_bucket
from .validate_owners import format_report, validate_all


@click.group()
def cli():
    """MLB Owner FEC Donations Archive."""


@cli.command()
def validate():
    """Validate owner YAMLs (OWNER_SCHEMA.md) and the legislation index YAMLs."""
    from .validate_legislation import format_report as format_leg_report
    from .validate_legislation import validate_all as validate_leg_all

    owner_results = validate_all()
    click.echo(format_report(owner_results))

    leg_results = validate_leg_all()
    click.echo("\n--- legislation ---")
    click.echo(format_leg_report(leg_results))

    # v12: master.db row-identity integrity. The record_uid column is nullable in
    # SCHEMA_SQL for fixture convenience, so the real guarantee is asserted here.
    uid_errors = db.check_record_uid_integrity()
    click.echo("\n--- master.db identity (v12) ---")
    if uid_errors:
        for e in uid_errors:
            click.echo(f"[FAIL] donations (id=record-uid-integrity)\n    error: {e}")
    else:
        click.echo("[OK] donations (id=record-uid-integrity)")

    # Adjudication durability + override blast radius. WARNINGS, not failures:
    # both describe human decisions that need a human to confirm, and neither
    # means the stored data is wrong (mirrors the legislation validators, which
    # warn rather than fail the gate).
    adj_warnings = db.check_adjudication_integrity()
    click.echo("\n--- master.db adjudication ---")
    if adj_warnings:
        for w in adj_warnings:
            click.echo(f"[OK] adjudication (id=adjudication-integrity)\n    warn:  {w}")
    else:
        click.echo("[OK] adjudication (id=adjudication-integrity)")

    ok = (
        all(r.ok for r in owner_results)
        and all(r.ok for r in leg_results)
        and not uid_errors
    )
    sys.exit(0 if ok else 1)


@cli.command()
def init():
    """Create the SQLite schema (idempotent)."""
    db.init()
    db.refresh_entities()
    click.echo(f"Initialized {db.MASTER_DB}")


@cli.command(name="init-legislation")
def init_legislation_cmd():
    """Create the Phase 3 legislation index schema in data/legislation.db (idempotent)."""
    from . import legislation_db
    from .paths import LEGISLATION_DB

    legislation_db.init()
    click.echo(f"Initialized {LEGISLATION_DB} (leg schema v{legislation_db.LEG_SCHEMA_VERSION})")


# ── Phase 4 — state campaign finance (CA pilot) ─────────────────────────────


@cli.command(name="init-state")
def init_state_cmd():
    """Create the Phase 4 state campaign-finance schema in data/state.db (idempotent)."""
    from . import state_db

    state_db.init()
    click.echo(f"Initialized {state_db.STATE_DB} (state schema v{state_db.STATE_SCHEMA_VERSION})")


def _find_tsv(extract_dir: Path, stem: str) -> Path | None:
    """Locate a CAL-ACCESS table file (e.g. RCPT_CD) under extract_dir, case-insensitively."""
    for p in extract_dir.rglob("*"):
        if p.is_file() and p.stem.upper() == stem.upper() and p.suffix.upper() in (".TSV", ".CSV"):
            return p
    return None


@cli.command(name="ingest-state")
@click.argument("slug")
@click.option("--extract-dir", required=True, type=click.Path(exists=True, path_type=Path),
              help="Directory holding the CAL-ACCESS extract (RCPT_CD.TSV + FILERNAME_CD.TSV).")
@click.option("--jurisdiction", default="CA", help="State code (default: CA).")
@click.option("--source", default="CAL-ACCESS", help="Official portal source label (default: CAL-ACCESS).")
@click.option("--dry-run", is_flag=True, help="Classify + report counts but write nothing.")
def ingest_state_cmd(slug, extract_dir, jurisdiction, source, dry_run):
    """Ingest one owner's state contributions from a CAL-ACCESS extract dir.

    Pre-filters the receipts to the owner's surname candidates, runs the SAME
    three-tier classifier as the federal pipeline, and writes CONFIRMED/PROBABLE
    to state.db (UNCERTAIN → state review queue). The official portal extract is
    the primary source (GOVERNANCE.md §3 / CHARTER.md §Phase 4).
    """
    from . import fetch_calaccess, ingest_state
    from .paths import REPO_ROOT, relpath

    rcpt_tsv = _find_tsv(extract_dir, "RCPT_CD")
    if rcpt_tsv is None:
        click.echo("ERROR: RCPT_CD.TSV not found under --extract-dir", err=True)
        sys.exit(1)
    filer_tsv = _find_tsv(extract_dir, "FILERNAME_CD")

    owner = ingest_state._load_owner(slug)
    rows = fetch_calaccess.candidate_rows_for_owner(rcpt_tsv, owner)
    filer_index = fetch_calaccess.build_filer_index(filer_tsv) if filer_tsv else {}
    resolver = fetch_calaccess.make_recipient_resolver(filer_index)

    # Prefer a repo-relative provenance path (the extract normally lives under
    # data/raw/state/ca/); fall back to the absolute path for out-of-repo extracts.
    try:
        raw_path = relpath(rcpt_tsv)
    except ValueError:
        raw_path = str(rcpt_tsv.resolve())

    res = ingest_state.ingest_state_entity(
        slug,
        rcpt_rows=rows,
        recipient_resolver=resolver,
        raw_payload_path=raw_path,
        extract_label=extract_dir.name,
        jurisdiction=jurisdiction,
        source=source,
        dry_run=dry_run,
    )
    tag = "[dry-run] " if dry_run else ""
    click.echo(
        f"{tag}{slug} [{jurisdiction}/{source}]: scanned {res.records_scanned} candidate(s) → "
        f"{res.confirmed} CONFIRMED, {res.probable} PROBABLE, {res.uncertain} UNCERTAIN"
        + (f", {res.excluded} excluded" if res.excluded else "")
        + (f", {res.skipped_no_date} no-date→review" if res.skipped_no_date else "")
        + (f", {res.superseded} superseded" if res.superseded else "")
    )


@cli.command(name="status-state")
def status_state_cmd():
    """Per-owner state-contribution counts from data/state.db."""
    from . import state_db
    from .paths import STATE_DB

    if not STATE_DB.exists():
        click.echo("No data/state.db yet — run `init-state` then `ingest-state`.")
        return
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT entity_slug, jurisdiction,
                   SUM(status='CONFIRMED') AS confirmed,
                   SUM(status='PROBABLE')  AS probable,
                   ROUND(SUM(CASE WHEN status IN ('CONFIRMED','PROBABLE') THEN amount ELSE 0 END), 2) AS total
              FROM state_donations
             WHERE status IN ('CONFIRMED','PROBABLE')
             GROUP BY entity_slug, jurisdiction
             ORDER BY total DESC
            """
        ).fetchall()
        queue = {
            r["entity_slug"]: r["n"]
            for r in conn.execute(
                "SELECT entity_slug, COUNT(*) AS n FROM state_review_queue "
                "WHERE resolution IS NULL GROUP BY entity_slug"
            )
        }
    if not rows:
        click.echo("No state donations yet.")
        return
    table = [
        [r["entity_slug"], r["jurisdiction"], r["confirmed"], r["probable"],
         f"${r['total']:,.0f}", queue.get(r["entity_slug"], 0)]
        for r in rows
    ]
    click.echo(tabulate(table, headers=["owner", "juris", "CONF", "PROB", "total", "review"]))


@cli.command(name="review-state")
@click.option("--slug", default=None, help="Limit to one owner.")
def review_state_cmd(slug):
    """List open state review-queue items (UNCERTAIN awaiting adjudication)."""
    from . import state_db
    from .paths import STATE_DB

    if not STATE_DB.exists():
        click.echo("No data/state.db yet.")
        return
    q = ("SELECT state_txn_id, entity_slug, jurisdiction, reason FROM state_review_queue "
         "WHERE resolution IS NULL")
    params: tuple = ()
    if slug:
        q += " AND entity_slug = ?"
        params = (slug,)
    with state_db.connect() as conn:
        rows = conn.execute(q + " ORDER BY entity_slug", params).fetchall()
    if not rows:
        click.echo("State review queue empty.")
        return
    table = [[r["entity_slug"], r["jurisdiction"], r["state_txn_id"], r["reason"]] for r in rows]
    click.echo(tabulate(table, headers=["owner", "juris", "state_txn_id", "reason"]))


@cli.command(name="state-freshness")
@click.option("--max-run-age-days", type=int, default=40,
              help="Flag a jurisdiction whose last ingestion run is older than this (default 40 — one monthly cron cycle + slack).")
@click.option("--fail-on-stale", is_flag=True,
              help="Exit non-zero if any jurisdiction is flagged stale (for CI/cron gating).")
def state_freshness_cmd(max_run_age_days, fail_on_stale):
    """Read-only per-jurisdiction freshness — last ingestion run + newest donation (§4.2).

    A state that SKIPPED its monthly refresh (e.g. a portal fetch failed under
    continue-on-error) records no new run, so its last-run age climbs — this is
    how a skipped state is told apart from a healthy one. `max_donation_age` is a
    softer signal (a genuinely quiet owner also ages), surfaced for a human eye.
    """
    from datetime import datetime, timezone

    from . import state_db
    from .paths import STATE_DB

    if not STATE_DB.exists():
        click.echo("No data/state.db yet.")
        return
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with state_db.connect() as conn:
        rows = state_db.state_freshness(conn, now_iso)
    stale = [r for r in rows if r["last_run_age_days"] is None or r["last_run_age_days"] > max_run_age_days]
    table = [
        [r["jurisdiction"], r["live_rows"],
         (r["last_run"] or "—")[:10], r["last_run_age_days"],
         (r["max_donation_date"] or "—"), r["max_donation_age_days"],
         "STALE" if r in stale else ""]
        for r in rows
    ]
    click.echo(tabulate(
        table,
        headers=["juris", "rows", "last_run", "run_age_d", "newest_donation", "don_age_d", "flag"],
    ))
    if stale:
        click.echo(f"\n{len(stale)} jurisdiction(s) with last run > {max_run_age_days}d "
                   f"(possible skipped refresh): {', '.join(r['jurisdiction'] for r in stale)}")
    if fail_on_stale and stale:
        sys.exit(1)


@cli.command(name="ingest-state-ca")
@click.option("--zip", "zip_path", required=True, type=click.Path(exists=True, path_type=Path),
              help="Path to the SoS CAL-ACCESS dbwebexport.zip (RCPT_CD streamed, not extracted).")
@click.option("--slugs", default=None,
              help="Comma-separated owner slugs (default: every pilot/active owner).")
@click.option("--dry-run", is_flag=True, help="Classify + report counts but write nothing.")
def ingest_state_ca_cmd(zip_path, slugs, dry_run):
    """Ingest CA state contributions for MANY owners in ONE streaming pass over the zip.

    Streams RCPT_CD straight from dbwebexport.zip (no 3.7 GB extraction), buckets
    candidate rows across all selected owners by surname, then runs the SAME
    three-tier classifier per owner. The zip is the persisted raw source
    (GOVERNANCE.md §1.4); CONFIRMED/PROBABLE → data/state.db, UNCERTAIN → review.
    """
    import yaml as _yaml

    from . import fetch_calaccess, ingest_state
    from .paths import OWNERS_DIR, REPO_ROOT, relpath

    # Resolve owner set.
    if slugs:
        want = [s.strip() for s in slugs.split(",") if s.strip()]
        owner_paths = [OWNERS_DIR / f"{s}.yaml" for s in want]
    else:
        owner_paths = [
            p for p in sorted(OWNERS_DIR.glob("*.yaml")) if not p.name.startswith("_")
        ]
    owners: list[tuple[str, dict]] = []
    for p in owner_paths:
        if not p.exists():
            click.echo(f"WARNING: {p.name} not found — skipping", err=True)
            continue
        data = _yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        if slugs or (data.get("status") in ("pilot", "active")):
            owners.append((data["slug"], data))
    if not owners:
        click.echo("No owners selected.", err=True)
        sys.exit(1)

    try:
        raw_path = relpath(zip_path)
    except ValueError:
        raw_path = str(zip_path.resolve())

    click.echo(f"Building recipient index from {zip_path.name} (CVR cover pages)…")
    recipient_index = fetch_calaccess.build_recipient_index_from_zip(zip_path)
    resolver = fetch_calaccess.make_recipient_resolver_by_filing(recipient_index)
    click.echo(f"  {len(recipient_index):,} filings indexed.")

    click.echo(f"Streaming RCPT_CD and bucketing across {len(owners)} owner(s)… (one pass over 3.7 GB)")
    buckets = fetch_calaccess.bucket_rows_by_owner(
        fetch_calaccess.iter_rcpt_rows_from_zip(zip_path), owners
    )
    total_candidates = sum(len(v) for v in buckets.values())
    click.echo(f"  {total_candidates:,} candidate receipt(s) across all owners (pre-dedupe).")

    results = []
    for slug, _owner in owners:
        rows = fetch_calaccess.dedupe_receipts(buckets.get(slug, []))
        if not rows:
            continue
        res = ingest_state.ingest_state_entity(
            slug,
            rcpt_rows=rows,
            recipient_resolver=resolver,
            raw_payload_path=raw_path,
            extract_label=zip_path.name,
            jurisdiction="CA",
            source="CAL-ACCESS",
            dry_run=dry_run,
        )
        results.append(res)

    tag = "[dry-run] " if dry_run else ""
    table = [
        [r.slug, r.records_scanned, r.confirmed, r.probable, r.uncertain,
         (r.excluded or ""), (r.skipped_no_date or ""), (r.superseded or "")]
        for r in sorted(results, key=lambda r: (-(r.confirmed + r.probable), r.slug))
    ]
    if table:
        click.echo(f"\n{tag}CA ingest results:")
        click.echo(tabulate(
            table,
            headers=["owner", "cand", "CONF", "PROB", "UNCERT", "excl", "no-date", "superseded"],
        ))
    else:
        click.echo(f"{tag}No candidate receipts matched any selected owner.")


@cli.command(name="reclassify-state")
@click.argument("slug")
@click.option("--zip", "zip_path", required=True, type=click.Path(exists=True, path_type=Path),
              help="Path to the CAL-ACCESS dbwebexport.zip.")
@click.option("--reason", default="", help="Reason (recorded in PROVENANCE_LOG).")
def reclassify_state_cmd(slug, zip_path, reason):
    """Wipe one owner's CA state rows and re-classify from the portal extract.

    For applying an owner-YAML signal change (calibration): a status change is
    invisible to the idempotent ingest upsert, so reclassify (delete + re-insert)
    is required. Streams the zip once; touches only this owner's rows + appends one
    provenance entry — the other owners' data is untouched.
    """
    from . import fetch_calaccess, ingest_state
    from .paths import relpath

    owner = ingest_state._load_owner(slug)
    try:
        raw_path = relpath(zip_path)
    except ValueError:
        raw_path = str(zip_path.resolve())

    click.echo(f"Building recipient index from {zip_path.name}…")
    resolver = fetch_calaccess.make_recipient_resolver_by_filing(
        fetch_calaccess.build_recipient_index_from_zip(zip_path)
    )
    click.echo(f"Streaming RCPT_CD for {slug}… (one pass)")
    buckets = fetch_calaccess.bucket_rows_by_owner(
        fetch_calaccess.iter_rcpt_rows_from_zip(zip_path), [(slug, owner)]
    )
    rows = fetch_calaccess.dedupe_receipts(buckets.get(slug, []))
    res = ingest_state.reclassify_state_entity(
        slug,
        rcpt_rows=rows,
        jurisdiction="CA",  # this command re-ingests a CAL-ACCESS extract
        recipient_resolver=resolver,
        reason=reason or "calibration",
        raw_payload_path=raw_path,
    )
    click.echo(
        f"{slug}: {res.confirmed} CONFIRMED, {res.probable} PROBABLE, "
        f"{res.uncertain} UNCERTAIN (scanned {res.records_scanned})"
    )


# ─── State review-queue adjudication (§5.4) ──────────────────────────────────
# The state-side mirror of the federal resolve/bulk-discard/attribute/exclude
# trio, keyed on (state_txn_id, entity_slug). resolve-state / bulk-discard-state
# are queue-only and take effect immediately (they set the resolution on the
# open state_review_queue item, so it drops out of the burndown). attribute-state
# / exclude-state write a durable override to state_manual_attributions; unlike
# the federal side (which reclassifies from stored per-owner raw), state can't
# re-score without the bulk portal extract, so those apply on the next
# `ingest-state` / `reclassify-state`. All are gated (snapshot + PROVENANCE_LOG)
# and reversible with their un* counterpart.


def _state_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@cli.command(name="resolve-state")
@click.argument("state_txn_id")
@click.argument("entity_slug")
@click.option("--reason", default="", help="Why this item is discarded (recorded in state_review_resolutions).")
@click.option("--resolution", default="DISCARDED", show_default=True, help="Verdict to record.")
def resolve_state_cmd(state_txn_id, entity_slug, reason, resolution):
    """Record a standing verdict for one STATE review-queue item (STATE_TXN_ID ENTITY_SLUG).

    A DISCARDED verdict permanently suppresses the transaction from re-entering the
    state review queue on future ingests/reclassifies (GOVERNANCE.md §2.5, §1.11).
    Queue-only and immediate — does NOT affect attribution. Undo with `unresolve-state`.
    """
    from . import state_db
    state_db.init()
    ts = _state_now_iso()
    with state_db.connect() as conn:
        state_db.upsert_state_review_resolution(
            conn, state_txn_id=state_txn_id, entity_slug=entity_slug,
            resolution=resolution, resolution_reason=reason or None, resolved_at=ts,
        )
        conn.execute(
            "UPDATE state_review_queue SET resolution=?, resolution_reason=?, resolution_at=? "
            "WHERE state_txn_id=? AND entity_slug=?",
            (resolution, reason or None, ts, state_txn_id, entity_slug),
        )
    click.echo(f"Recorded {resolution} for {state_txn_id} ({entity_slug}).")


@cli.command(name="unresolve-state")
@click.argument("state_txn_id")
@click.argument("entity_slug")
def unresolve_state_cmd(state_txn_id, entity_slug):
    """Remove a standing state verdict (undo resolve-state). The item re-queues on
    the next ingest/reclassify if it still classifies UNCERTAIN."""
    from . import state_db
    state_db.init()
    with state_db.connect() as conn:
        n = state_db.delete_state_review_resolution(
            conn, state_txn_id=state_txn_id, entity_slug=entity_slug)
        conn.execute(
            "UPDATE state_review_queue SET resolution=NULL, resolution_reason=NULL, resolution_at=NULL "
            "WHERE state_txn_id=? AND entity_slug=?",
            (state_txn_id, entity_slug),
        )
    click.echo(f"Removed {n} standing verdict(s) for {state_txn_id} ({entity_slug}).")


@cli.command(name="bulk-discard-state")
@click.argument("entity_slug")
@click.option("--jurisdiction", default=None, help="Restrict to one jurisdiction (e.g. TX). Default: all.")
@click.option("--reason-like", default=None, help="Only discard items whose reason LIKE this pattern.")
@click.option("--note", default="bulk discard", help="Reason recorded on each resolution.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def bulk_discard_state_cmd(entity_slug, jurisdiction, reason_like, note, yes):
    """Discard every OPEN state review-queue item for an owner (STATE burndown, §5.4).

    GATED — snapshots state.db and logs to PROVENANCE_LOG. Optionally scope to one
    --jurisdiction and/or a --reason-like SQL pattern. Queue-only (never touches
    state_donations); each discarded item gets a DISCARDED resolution so it won't
    re-queue. Reverse individual items with `unresolve-state`.
    """
    from . import state_db
    from .paths import PROVENANCE_LOG
    state_db.init()
    where = "entity_slug = ? AND resolution IS NULL"
    params: list = [entity_slug]
    if jurisdiction:
        where += " AND jurisdiction = ?"; params.append(jurisdiction)
    if reason_like:
        where += " AND reason LIKE ?"; params.append(reason_like)
    with state_db.connect() as conn:
        n = conn.execute(f"SELECT COUNT(*) FROM state_review_queue WHERE {where}", params).fetchone()[0]
    if n == 0:
        click.echo(f"No open state review items match for {entity_slug}.")
        return
    scope = f" jurisdiction={jurisdiction}" if jurisdiction else ""
    scope += f" reason~{reason_like!r}" if reason_like else ""
    click.echo(f"Will DISCARD {n} open state review item(s) for {entity_slug}{scope}. "
               f"state.db is snapshotted first; logged to PROVENANCE_LOG.")
    if not yes and not click.confirm("Continue?", default=False):
        click.echo("Aborted."); return
    ts = _state_now_iso()
    snap = state_db.snapshot("bulk_discard_state")
    with state_db.connect() as conn:
        rows = list(conn.execute(f"SELECT state_txn_id FROM state_review_queue WHERE {where}", params))
        for r in rows:
            state_db.upsert_state_review_resolution(
                conn, state_txn_id=r["state_txn_id"], entity_slug=entity_slug,
                resolution="DISCARDED", resolution_reason=note, resolved_at=ts)
        conn.execute(
            f"UPDATE state_review_queue SET resolution='DISCARDED', resolution_reason=?, resolution_at=? WHERE {where}",
            [note, ts, *params])
    append_provenance(
        f"\n### {ts[:10]} — REVIEW_RESOLUTION — {entity_slug} (state bulk-discard)\n\n"
        f"Discarded **{n}** open state review-queue item(s) for `{entity_slug}`"
        f"{scope} as DISCARDED (§5.4 burndown). Queue-only; state_donations untouched. "
        f"Reason: {note}. Snapshot: `{snap}`.\n",
        PROVENANCE_LOG,
    )
    click.echo(f"Discarded {n} state review item(s) for {entity_slug}.")


@cli.command(name="attribute-state")
@click.argument("state_txn_id")
@click.argument("entity_slug")
@click.option("--reason", required=True, help="Documented justification (recorded in state_manual_attributions).")
@click.option("--source", default="", help="Evidence/source supporting the attribution.")
@click.option("--status", default="CONFIRMED", show_default=True, help="Status to force (CONFIRMED or PROBABLE).")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def attribute_state_cmd(state_txn_id, entity_slug, reason, source, status, yes):
    """Manually attribute one STATE transaction to an owner (STATE_TXN_ID ENTITY_SLUG).

    GATED — records a durable override in state_manual_attributions (snapshot +
    PROVENANCE_LOG). Unlike the federal `attribute` (which reclassifies from stored
    raw), state can't re-score without the bulk portal extract, so the override
    APPLIES ON THE NEXT `ingest-state` / `reclassify-state` for this owner. Survives
    reclassify; undo with `unattribute-state`.
    """
    from . import state_db
    from .paths import PROVENANCE_LOG
    status = status.upper()
    if status not in ("CONFIRMED", "PROBABLE"):
        click.echo("--status must be CONFIRMED or PROBABLE.", err=True); raise SystemExit(1)
    state_db.init()
    click.echo(f"Will attribute {state_txn_id} → {entity_slug} as {status} (applies on next ingest-state).\n"
               f"  reason: {reason}\n  source: {source or '(none)'}")
    if not yes and not click.confirm("Continue?", default=False):
        click.echo("Aborted."); return
    ts = _state_now_iso()
    snap = state_db.snapshot("pre-state-attribute")
    with state_db.connect() as conn:
        state_db.upsert_state_manual_attribution(
            conn, state_txn_id=state_txn_id, entity_slug=entity_slug, status=status,
            reason=reason, source=source or None, attributed_at=ts)
        # A manually-attributed txn must not also carry a DISCARDED verdict.
        state_db.delete_state_review_resolution(conn, state_txn_id=state_txn_id, entity_slug=entity_slug)
    append_provenance(
        f"\n### {ts[:10]} — MANUAL_ATTRIBUTION — {entity_slug} (state)\n\n"
        f"Forced `{state_txn_id}` → **{status}** for `{entity_slug}` (state). Reason: {reason}. "
        f"Source: {source or '(none)'}. Applies on the next state ingest/reclassify. Snapshot: `{snap}`.\n",
        PROVENANCE_LOG,
    )
    click.echo(f"Attributed {state_txn_id} → {entity_slug} as {status} (applies on next ingest-state).")


@cli.command(name="exclude-state")
@click.argument("state_txn_id")
@click.argument("entity_slug")
@click.option("--reason", required=True, help="Why this txn is NOT this owner (e.g. same-named relative).")
@click.option("--source", default="", help="Evidence/source.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def exclude_state_cmd(state_txn_id, entity_slug, reason, source, yes):
    """Exclude one STATE transaction from an owner (STATE_TXN_ID ENTITY_SLUG).

    GATED — records a durable EXCLUDED override in state_manual_attributions so the
    txn is dropped (not even queued) on the next state ingest/reclassify. The state
    analog of `exclude`, for a same-named relative's record a signal edit can't
    cleanly separate. Undo with `unexclude-state`.
    """
    from . import state_db
    from .paths import PROVENANCE_LOG
    state_db.init()
    click.echo(f"Will EXCLUDE {state_txn_id} from {entity_slug} (applies on next ingest-state).\n  reason: {reason}")
    if not yes and not click.confirm("Continue?", default=False):
        click.echo("Aborted."); return
    ts = _state_now_iso()
    snap = state_db.snapshot("pre-state-exclude")
    with state_db.connect() as conn:
        state_db.upsert_state_manual_attribution(
            conn, state_txn_id=state_txn_id, entity_slug=entity_slug, status="EXCLUDED",
            reason=reason, source=source or None, attributed_at=ts)
    append_provenance(
        f"\n### {ts[:10]} — MANUAL_ATTRIBUTION — {entity_slug} (state EXCLUDED)\n\n"
        f"Excluded `{state_txn_id}` from `{entity_slug}` (state). Reason: {reason}. "
        f"Dropped on the next state ingest/reclassify. Snapshot: `{snap}`.\n",
        PROVENANCE_LOG,
    )
    click.echo(f"Excluded {state_txn_id} from {entity_slug} (applies on next ingest-state).")


@cli.command(name="unattribute-state")
@click.argument("state_txn_id")
@click.argument("entity_slug")
def unattribute_state_cmd(state_txn_id, entity_slug):
    """Remove a manual state attribution (undo attribute-state)."""
    from . import state_db
    state_db.init()
    with state_db.connect() as conn:
        n = state_db.delete_state_manual_attribution(conn, state_txn_id=state_txn_id, entity_slug=entity_slug)
    click.echo(f"Removed {n} manual attribution(s) for {state_txn_id} ({entity_slug}).")


@cli.command(name="unexclude-state")
@click.argument("state_txn_id")
@click.argument("entity_slug")
def unexclude_state_cmd(state_txn_id, entity_slug):
    """Remove a manual state exclusion (undo exclude-state)."""
    from . import state_db
    state_db.init()
    with state_db.connect() as conn:
        n = state_db.delete_state_manual_attribution(conn, state_txn_id=state_txn_id, entity_slug=entity_slug)
    click.echo(f"Removed {n} manual exclusion(s) for {state_txn_id} ({entity_slug}).")


@cli.command(name="dedupe-state-crossfilings")
@click.option("--jurisdictions", default="IL,TX,AZ",
              help="Comma-separated jurisdiction codes to dedupe (default: IL,TX,AZ).")
@click.option("--dry-run", is_flag=True, help="Report what would be superseded; write nothing.")
def dedupe_state_crossfilings_cmd(jurisdictions, dry_run):
    """One-shot (GATED): collapse state cross-filing / fan-out duplicates (§1.2).

    Groups CONFIRMED/PROBABLE state_donations by content key (donor+date+amount+
    recipient) and marks all but the latest-filing row SUPERSEDED, so a
    contribution re-filed across filings (IL/TX) or fanned out with different
    tran-ids (AZ) is counted once. Never deletes (§1.10). Snapshots state.db and
    appends a CORRECTION entry to PROVENANCE_LOG. CA/WA are excluded by default
    (their same-filing distinct-tran rows are deliberately preserved — needs sign-off).
    """
    from .dedupe_state_crossfilings import dedupe

    juris = tuple(j.strip().upper() for j in jurisdictions.split(",") if j.strip())
    summary = dedupe(jurisdictions=juris, dry_run=dry_run)
    click.echo(json.dumps(summary, indent=2, default=str))


def _load_state_owners(slugs: str | None):
    """[(slug, owner_dict)] for the given --slugs, or every pilot/active owner."""
    import yaml as _yaml

    from .paths import OWNERS_DIR

    if slugs:
        want = [s.strip() for s in slugs.split(",") if s.strip()]
        paths = [OWNERS_DIR / f"{s}.yaml" for s in want]
    else:
        paths = [p for p in sorted(OWNERS_DIR.glob("*.yaml")) if not p.name.startswith("_")]
    owners = []
    for p in paths:
        if not p.exists():
            click.echo(f"WARNING: {p.name} not found — skipping", err=True)
            continue
        d = _yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and (slugs or d.get("status") in ("pilot", "active")):
            owners.append((d["slug"], d))
    return owners


@cli.command(name="ingest-state-bulk")
@click.argument("state")
@click.option("--input", "input_path", default=None, type=click.Path(exists=True, path_type=Path),
              help="File-based sources only: CA→dbwebexport.zip; PA→export dir of per-year "
                   "zips; TX→TEC_CF_CSV.zip; IL→dir with Receipts.txt+Committees.txt; "
                   "CO→dir of per-year <YEAR>_ContributionData.csv.zip; MN→dir with the "
                   "all-entities contributions CSV. Omit for API sources (NY, WA, AZ, FL).")
@click.option("--slugs", default=None, help="Comma-separated owner slugs (default: every pilot/active owner).")
@click.option("--dry-run", is_flag=True, help="Classify + report counts but write nothing.")
def ingest_state_bulk_cmd(state, input_path, slugs, dry_run):
    """Ingest a registered state's contributions for many owners in one pass.

    Generic over the StateSource registry (CA, PA, NY, …). The official portal is the
    primary source (GOVERNANCE.md §3); CONFIRMED/PROBABLE → data/state.db, UNCERTAIN
    → the state review queue. Same three-tier classifier as the federal pipeline —
    only the per-portal input adapter differs. File sources need --input; API sources
    (NY) fetch live.
    """
    from . import ingest_state
    from .state_sources import get_source

    src = get_source(state)
    if src.requires_input and input_path is None:
        click.echo(f"ERROR: {src.code} is a file-based source — pass --input.", err=True)
        sys.exit(1)
    owners = _load_state_owners(slugs)
    if not owners:
        click.echo("No owners selected.", err=True)
        sys.exit(1)
    origin = input_path.name if input_path else src.raw_ref
    click.echo(f"[{src.code}/{src.source}] ingesting {len(owners)} owner(s) from {origin}…")
    results = ingest_state.ingest_state_bulk(src.code, input_path, owners, dry_run=dry_run)

    tag = "[dry-run] " if dry_run else ""
    table = [
        [r.slug, r.records_scanned, r.confirmed, r.probable, r.uncertain,
         (r.excluded or ""), (r.skipped_no_date or ""), (r.superseded or "")]
        for r in sorted(results, key=lambda r: (-(r.confirmed + r.probable), r.slug))
    ]
    if table:
        click.echo(f"\n{tag}{src.code} ingest results:")
        click.echo(tabulate(table, headers=["owner", "cand", "CONF", "PROB", "UNCERT", "excl", "no-date", "superseded"]))
    else:
        click.echo(f"{tag}No candidate contributions matched any selected owner.")


@cli.command(name="ingest-legislators")
@click.option("--no-historical", is_flag=True, help="Fetch only legislators-current.yaml (skip the larger historical file).")
@click.option("--all-legislators", is_flag=True, help="Keep legislators with no FEC id too (default: only the FEC-joinable universe).")
def ingest_legislators_cmd(no_historical, all_legislators):
    """Fetch the congress-legislators crosswalk and rebuild the FEC→Bioguide map.

    GATED DATA OPERATION — snapshots legislation.db first and appends a
    PROVENANCE_LOG entry. The crosswalk tables are a pure projection of the
    upstream source, so this is an idempotent wipe-and-rebuild.
    """
    from datetime import datetime, timezone

    from . import legislation_db
    from .fetch_legislators import CURRENT_URL, HISTORICAL_URL, SOURCE_LABEL, fetch_and_parse
    from .ingest_legislation import ingest_legislators
    from .paths import PROVENANCE_LOG

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snap = legislation_db.snapshot("pre-ingest-legislators")
    click.echo("Fetching congress-legislators crosswalk…")
    legislators, fec_ids, terms = fetch_and_parse(
        include_historical=not no_historical,
        only_with_fec=not all_legislators,
    )
    counts = ingest_legislators(legislators, fec_ids, terms)

    urls = CURRENT_URL if no_historical else f"{CURRENT_URL} + {HISTORICAL_URL}"
    block = [
        f"\n### {ts[:10]} — INGESTION (legislators crosswalk)",
        "",
        f"- **source**: `{SOURCE_LABEL}` ({urls})",
        f"- **fetched_at**: `{ts}`",
        f"- **legislators**: `{counts['legislators']}`",
        f"- **fec_id_links**: `{counts['fec_ids']}`",
        f"- **terms**: `{counts['terms']}`",
        f"- **only_with_fec**: `{not all_legislators}`",
        f"- **include_historical**: `{not no_historical}`",
        f"- **snapshot_path**: `{snap}`",
        "- **note**: Tier-2 entity identification (SOURCES.md Phase-3 addendum). Crosswalk tables are a pure projection of the upstream source — idempotent wipe-and-rebuild. Raw payloads persisted under data/raw/legislation/.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)

    click.echo(json.dumps(counts, indent=2))


@cli.command(name="ingest-congress-committees")
def ingest_congress_committees_cmd():
    """Fetch current congressional committees + membership (for the committee join).

    GATED DATA OPERATION — snapshots legislation.db first and appends a
    PROVENANCE_LOG entry. Current-congress snapshot only (upstream has no history);
    `committees.congress` records which congress it represents. Run
    `ingest-legislators` first so the current congress can be derived.
    """
    from datetime import datetime, timezone

    from . import legislation_db
    from .fetch_congress_committees import COMMITTEES_URL, MEMBERSHIP_URL, SOURCE_LABEL, fetch_and_parse
    from .ingest_legislation import ingest_committees
    from .paths import PROVENANCE_LOG

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snap = legislation_db.snapshot("pre-ingest-committees")
    click.echo("Fetching current congressional committees + membership…")
    committee_rows, membership_rows, raw_path = fetch_and_parse()
    counts = ingest_committees(committee_rows, membership_rows, raw_payload_path=raw_path)

    block = [
        f"\n### {ts[:10]} — INGESTION (congressional committees)",
        "",
        f"- **source**: `{SOURCE_LABEL}` ({COMMITTEES_URL} + {MEMBERSHIP_URL})",
        f"- **fetched_at**: `{ts}`",
        f"- **committees**: `{counts['committees']}`",
        f"- **memberships**: `{counts['memberships']}`",
        f"- **congress**: `{counts['congress']}`",
        f"- **snapshot_path**: `{snap}`",
        "- **note**: Current-congress committee membership only (upstream has no history). The committee→donation join (policy-join --via-committee) guards on committees.congress so a present-day member is never tied to a historical bill. Idempotent wipe-and-rebuild.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)

    click.echo(json.dumps(counts, indent=2))


@cli.command(name="legislation-coverage")
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON instead of a table.")
def legislation_coverage_cmd(as_json):
    """Read-only: how many donation recipient candidates resolve to a legislator?

    De-risking probe for the phase. Joins master.db donations to the crosswalk and
    reports resolved/unresolved coverage plus the largest unresolved recipients.
    """
    from .ingest_legislation import donation_legislator_coverage

    cov = donation_legislator_coverage()
    if as_json:
        click.echo(json.dumps(cov, indent=2))
        return
    click.echo(
        f"Donation recipient candidates: {cov['n_candidate_ids']} distinct "
        f"({cov['statuses']})\n"
        f"  resolved to a legislator: {cov['n_resolved']} ({cov['pct_resolved']}%)\n"
        f"  unresolved:               {cov['n_unresolved']}"
    )
    if cov["top_unresolved"]:
        rows = [
            [r["cid"], (r["name"] or "")[:40], r["n_donations"], f"${(r['total_amount'] or 0):,.0f}"]
            for r in cov["top_unresolved"]
        ]
        click.echo("\nLargest unresolved recipients:")
        click.echo(tabulate(rows, headers=["fec_cand_id", "name", "n", "total"]))


@cli.command(name="ingest-bills")
def ingest_bills_cmd():
    """Enrich the curated bill set (legislation/bills/*.yaml) from Congress.gov.

    GATED DATA OPERATION — snapshots legislation.db first and appends a
    PROVENANCE_LOG entry. Upserts bills + bill_sponsors keyed by bill_id; the
    curated fields (mlb_issue_area, relevance_basis, …) always come from the YAML,
    never the API.
    """
    from datetime import datetime, timezone

    from . import legislation_db
    from .fetch_congress import CongressClient
    from .ingest_legislation import ingest_bills, load_curated_bills
    from .paths import PROVENANCE_LOG

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    specs = load_curated_bills()
    if not specs:
        click.echo("No curated bills in legislation/bills/. Nothing to do.")
        return
    snap = legislation_db.snapshot("pre-ingest-bills")
    client = CongressClient()
    click.echo(f"Enriching {len(specs)} curated bill(s) from Congress.gov…")
    counts = ingest_bills(specs, client)

    block = [
        f"\n### {ts[:10]} — INGESTION (bills)",
        "",
        f"- **source**: `congress.gov` (api.congress.gov v3)",
        f"- **fetched_at**: `{ts}`",
        f"- **curated_bills_in_set**: `{len(specs)}`",
        f"- **bills_enriched**: `{counts['bills']}`",
        f"- **sponsor_rows**: `{counts['sponsors']}`",
        f"- **errors**: `{counts['errors']}`",
        f"- **snapshot_path**: `{snap}`",
        "- **note**: Curated fields (mlb_issue_area, relevance_basis, carried_by_bill_id) sourced from legislation/bills/*.yaml; identity/sponsors/action from Congress.gov (Tier-1). Raw payloads under data/raw/legislation/.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)

    click.echo(json.dumps(counts, indent=2))


@cli.command(name="ingest-votes")
def ingest_votes_cmd():
    """Fetch the curated roll-call votes (bills' roll_calls blocks) from Clerk/Senate XML.

    GATED DATA OPERATION — snapshots legislation.db first and appends a
    PROVENANCE_LOG entry. House positions key on Bioguide directly; Senate
    positions are mapped LIS→Bioguide via the crosswalk.
    """
    from datetime import datetime, timezone

    from . import fetch_votes, legislation_db
    from .ingest_legislation import ingest_votes, load_curated_roll_calls
    from .paths import PROVENANCE_LOG

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    specs = load_curated_roll_calls()
    if not specs:
        click.echo("No roll_calls declared in legislation/bills/. Nothing to do.")
        return
    snap = legislation_db.snapshot("pre-ingest-votes")
    click.echo(f"Fetching {len(specs)} roll-call vote(s) from Clerk/Senate XML…")
    counts = ingest_votes(specs, fetch_votes)

    block = [
        f"\n### {ts[:10]} — INGESTION (votes)",
        "",
        "- **source**: `clerk.house.gov` (EVS XML) + `senate.gov` (LIS XML) — Tier-1 source of record",
        f"- **fetched_at**: `{ts}`",
        f"- **roll_calls_in_set**: `{len(specs)}`",
        f"- **votes_ingested**: `{counts['votes']}`",
        f"- **vote_positions**: `{counts['positions']}`",
        f"- **senate_unmapped (no FEC-crosswalk lis_id)**: `{counts['senate_unmapped']}`",
        f"- **errors**: `{counts['errors']}`",
        f"- **snapshot_path**: `{snap}`",
        "- **note**: Vote positions are FEC-neutral facts (who voted Yea/Nay). Senate LIS ids mapped to Bioguide via legislators.lis_id. Raw XML under data/raw/legislation/.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)

    click.echo(json.dumps(counts, indent=2))


@cli.command(name="policy-join")
@click.option("--bill", "bills", multiple=True, help="Vote-bearing bill_id(s): join donations→legislators who voted on these.")
@click.option("--sponsors-of", "sponsor_bills", multiple=True, help="Bill_id(s): also join donations→sponsors/cosponsors of these.")
@click.option("--via-committee", "committee_bills", multiple=True, help="Bill_id(s): also join donations→current members of the bill's committee(s) of referral (current-congress bills only).")
@click.option("--out", "basename", required=True, help="Output basename written under reports/data/.")
@click.option(
    "--include-indirect",
    "include_indirect",
    is_flag=True,
    help=(
        "Also join the indirect-authorized tier — money to a legislator's OWN campaign "
        "committee, gated on FEC designation P/A + a single candidate_ids (PR #106). "
        "Default off: the published briefs are direct-tier deep-dives. The #/legislation "
        "dashboard renders WITH this flag, so pass it to reproduce the dashboard's figures."
    ),
)
def policy_join_cmd(bills, sponsor_bills, committee_bills, basename, include_indirect):
    """Read-only: write the neutral owner→donation→legislator→vote join to reports/data/.

    Produces a reproducible CSV + JSON of neutral facts (donation, legislator,
    position, days_before_vote). NO interpretation — that lives in the brief.

    Three join modes (combinable): --bill (voters), --sponsors-of (authors),
    --via-committee (current members of the committee(s) the bill was referred
    to — the widest money-meets-power surface; current-congress bills only).
    """
    from .policy_join import (
        committee_donation_rows,
        sponsor_donation_rows,
        summarize_by_owner,
        vote_donation_rows,
        write_outputs,
    )

    if not bills and not sponsor_bills and not committee_bills:
        click.echo("Pass at least one --bill, --sponsors-of, or --via-committee.")
        return

    # Declared in every artifact's _meta so a regenerated file states which tiers
    # it covers — a direct-tier file and a two-tier file are both correct and are
    # not otherwise distinguishable from their contents.
    tier_meta = {
        "include_indirect": include_indirect,
        "join_tiers": (
            ["direct", "indirect-authorized"] if include_indirect else ["direct"]
        ),
    }

    written = {}
    vote_rows = (
        vote_donation_rows(bill_ids=list(bills), include_indirect=include_indirect)
        if bills
        else []
    )
    if bills:
        written["votes"] = write_outputs(
            vote_rows,
            basename=basename,
            meta={"join": "donations_to_votes", "bill_ids": list(bills), **tier_meta},
        )
    if sponsor_bills:
        sp_rows = sponsor_donation_rows(
            bill_ids=list(sponsor_bills), include_indirect=include_indirect
        )
        written["sponsors"] = write_outputs(
            sp_rows,
            basename=f"{basename}-sponsors",
            meta={"join": "donations_to_sponsors", "bill_ids": list(sponsor_bills), **tier_meta},
        )
    if committee_bills:
        cm_rows = committee_donation_rows(
            bill_ids=list(committee_bills), include_indirect=include_indirect
        )
        written["committees"] = write_outputs(
            cm_rows,
            basename=f"{basename}-committees",
            meta={
                "join": "donations_to_committee_members",
                "bill_ids": list(committee_bills),
                "membership_note": "Current-congress committee membership only; restricted to bills of that congress.",
                **tier_meta,
            },
        )

    if vote_rows:
        summary = summarize_by_owner(vote_rows)
        rows = [
            [s["owner_name"] or s["owner_slug"], s["owner_team"] or "", s["n_donations"],
             f"${s['total_amount']:,.0f}", s["n_legislators"], f"${s['to_yea_amount']:,.0f}"]
            for s in summary
        ]
        click.echo(tabulate(rows, headers=["owner", "team", "n", "total", "legis", "to Yea"]))
    click.echo("\n" + json.dumps(written, indent=2))


@cli.command()
@click.argument("slug")
@click.option("--dry-run", is_flag=True, help="Fetch + classify but do not write to DB.")
@click.option(
    "--min-date",
    default=None,
    help=(
        "Explicit minimum contribution_receipt_date (YYYY-MM-DD). "
        "Default: use owner's audit.last_ingestion if set, else 2000-01-01. "
        "Use --full-refetch to override audit.last_ingestion and pull complete history."
    ),
)
@click.option("--full-refetch", is_flag=True, help="Ignore audit.last_ingestion; fetch from 2000-01-01 forward.")
@click.option("--max-pages", type=int, default=None, help="Per-variant page cap (for testing).")
@click.option("--include-related", is_flag=True, help="Also classify against related_entities (default: principals only).")
@click.option("--no-state-filter", is_flag=True, help="Disable state pre-filter at fetch — search FEC by name only. Use for discovery, not production.")
@click.option("--from-raw", is_flag=True, help="Skip the network fetch; classify against existing raw payloads in data/raw/<slug>/.")
@click.option("--chunk-by-cycle", is_flag=True, help="Always paginate FEC per 2-year election cycle (use for common-name owners like Malone, Sherman, Davis where total page count would otherwise timeout).")
@click.option("--force-resume", is_flag=True, help="Resume from data/raw/<slug>/_fetch_state.json even if older than 7 days.")
def ingest(slug, dry_run, min_date, full_refetch, max_pages, include_related, no_state_filter, from_raw, chunk_by_cycle, force_resume):
    """Run the full ingestion pipeline for one entity."""
    summary = ingest_entity(
        slug,
        dry_run=dry_run,
        min_date=min_date,
        max_pages=max_pages,
        process_related_entities=include_related,
        state_filter=not no_state_filter,
        from_raw=from_raw,
        full_refetch=full_refetch,
        chunk_by_cycle=chunk_by_cycle,
        force_resume=force_resume,
    )
    click.echo("")
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command()
@click.option(
    "--only",
    default=None,
    help="Comma-separated owner slugs to limit the run to (default: every pilot/active owner).",
)
@click.option(
    "--bucket",
    default=None,
    help=(
        "Run only this matrix bucket, formatted N/M (0-indexed). E.g. --bucket 0/4 "
        "runs ~1/4 of active owners, balanced by raw-payload weight. Used by the "
        "GHA refresh matrix to parallelize the weekly run across 4 jobs."
    ),
)
@click.option("--dry-run", is_flag=True, help="Fetch + classify but do not write to DB or regenerate data.json.")
@click.option("--skip-data-json", is_flag=True, help="Do not regenerate mockup/data.json even if records changed.")
@click.option("--full-refetch", is_flag=True, help="Ignore audit.last_ingestion for every owner; refetch from 2000-01-01.")
@click.option("--chunk-by-cycle", is_flag=True, help="Pass --chunk-by-cycle to every owner's ingest.")
def refresh(only, bucket, dry_run, skip_data_json, full_refetch, chunk_by_cycle):
    """Refresh every pilot/active owner from FEC since their last_ingestion.

    Loops the resolved owner set, runs the existing ingest pipeline per owner
    with per-owner failure isolation, and regenerates mockup/data.json once at
    the end if any owner ingested new records (and only if no --bucket scope —
    the matrix consolidate job rebuilds data.json after all buckets land).

    Exit code: 0 if every attempted owner succeeded, 1 if any failed.
    """
    if only and bucket:
        click.echo("--only and --bucket are mutually exclusive.", err=True)
        sys.exit(2)

    only_list: list[str] | None = None
    if only:
        only_list = [s.strip() for s in only.split(",") if s.strip()]
    elif bucket:
        try:
            idx_s, count_s = bucket.split("/", 1)
            idx, count = int(idx_s), int(count_s)
        except ValueError:
            click.echo(f"--bucket must be N/M (e.g. 0/4), got {bucket!r}.", err=True)
            sys.exit(2)
        only_list = select_bucket(idx, count)
        click.echo(f"[refresh] bucket {idx}/{count}: {len(only_list)} owner(s): {only_list}")

    summary = refresh_all(
        only=only_list,
        dry_run=dry_run,
        # When running as one bucket of the matrix, leave data.json untouched
        # — the consolidate job rebuilds it once after merging all buckets.
        skip_data_json=skip_data_json or bool(bucket),
        full_refetch=full_refetch,
        chunk_by_cycle=chunk_by_cycle,
    )
    click.echo("")
    click.echo(json.dumps(summary, indent=2, default=str))
    if summary["owners_failed"] > 0:
        sys.exit(1)


@cli.command(name="ingest-committees")
@click.option(
    "--only",
    default=None,
    help="Comma-separated committee_ids to refresh (default: every distinct recipient on a CONFIRMED/PROBABLE donation).",
)
@click.option(
    "--force-refresh",
    is_flag=True,
    help="Re-fetch even if the committees row was refreshed within the freshness window.",
)
@click.option(
    "--max",
    "max_count",
    type=int,
    default=None,
    help="Cap the number of committees processed (for testing / smoke runs).",
)
@click.option(
    "--max-fetch",
    "max_fetch",
    type=int,
    default=None,
    help="Cap committees actually FETCHED from FEC this run (oldest first; the rest "
         "deferred to the next run). Bounds runtime and de-syncs the cohort (§4.3).",
)
def ingest_committees_cmd(only, force_refresh, max_count, max_fetch):
    """Enrich the committees and committee_totals tables from OpenFEC.

    Fetches /committee/<id>/ (identity) and /committee/<id>/totals/ (per-cycle
    scale) for every committee that has received an attributed donation.
    Idempotent — re-runs within 45 days are no-ops unless --force-refresh.
    """
    only_list: list[str] | None = None
    if only:
        only_list = [s.strip() for s in only.split(",") if s.strip()]
    summary = ingest_all_committees(
        only=only_list,
        force_refresh=force_refresh,
        max_count=max_count,
        max_fetch=max_fetch,
    )
    click.echo("")
    click.echo(json.dumps(summary, indent=2, default=str))
    if summary.get("failed", 0) > 0:
        sys.exit(1)


@cli.command(name="backfill-donation-image-fields")
def backfill_donation_image_fields_cmd():
    """One-shot: populate v3 image_number/pdf_url/etc. columns on existing donation rows.

    Scans data/raw/<slug>/*.json for each owner whose donation rows still have
    NULL image_number, and rehydrates the new columns from whatever payloads
    are present locally. Idempotent. Rows whose raw payload was destroyed (e.g.,
    via a runner-side GHA refresh whose data/raw didn't make it back) stay NULL
    and need a full --full-refetch to recover.
    """
    summary = backfill_donation_image_fields()
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command(name="backfill-memo-fields")
def backfill_memo_fields_cmd():
    """One-shot (GATED): populate v8 memo_code/memo_text/is_individual and recompute `counted`.

    Scans data/raw/<slug>/*.json for each owner whose donation rows still have
    NULL is_individual, backfills the FEC earmark/conduit fields, then recomputes
    the derived `counted` dedup flag over the whole table so double-counted
    conduit passthrough legs (ActBlue/WinRed) drop out of every published SUM
    (§1.1). Snapshots master.db and appends a CORRECTION entry to PROVENANCE_LOG.
    Idempotent; never deletes a row (GOVERNANCE.md §1.10).
    """
    summary = backfill_memo_fields()
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command(name="prune-snapshots")
@click.option("--keep-days", type=int, default=30, help="Keep every snapshot younger than this (default 30).")
@click.option("--apply", "do_apply", is_flag=True, help="Actually delete (default is a dry-run preview).")
def prune_snapshots_cmd(keep_days, do_apply):
    """Prune old data/snapshots/*.db (§2.3): keep all < keep-days + newest per op group.

    Snapshots are gitignored, regenerable rollback aids that accrete ~130MB per
    gated op. Defaults to a DRY-RUN preview; pass --apply to delete. Deletes local
    files only (never anything committed) and logs a PRUNE entry to PROVENANCE_LOG.
    """
    from .prune_snapshots import prune

    summary = prune(keep_days=keep_days, dry_run=not do_apply)
    click.echo(json.dumps(summary, indent=2, default=str))
    if not do_apply and summary["pruned"]:
        click.echo(f"\nDry run — pass --apply to free {summary['mb_freed']} MB.")


@cli.command(name="rotate-provenance")
@click.option(
    "--before-year",
    type=int,
    default=None,
    help="Seal every year strictly before this (default: the current UTC year, so all completed prior years).",
)
@click.option("--apply", "do_apply", is_flag=True, help="Actually rotate (default is a dry-run preview).")
def rotate_provenance_cmd(before_year, do_apply):
    """Rotate completed years out of catalog/PROVENANCE_LOG.md into yearly archives (§2.2).

    The append-only log grows without bound and the dashboard build parses the
    whole file each deploy. This seals completed prior years into
    catalog/provenance/PROVENANCE_LOG-<YYYY>.md so the active log stays bounded.
    Entry text is MOVED verbatim, never deleted (§1.10); the build parses the
    active log + all archives together, so provenance.json stays complete.
    Defaults to a DRY-RUN preview; pass --apply to perform the move.
    """
    from .rotate_provenance import apply_rotation

    plan = apply_rotation(cutoff_year=before_year, apply=do_apply)
    summary = {
        "cutoff_year": plan["cutoff_year"],
        "years_to_seal": plan["years"],
        "n_archived": plan["n_archived"],
        "n_retained": plan["n_retained"],
        "applied": plan["applied"],
        "archive_files": plan.get("archive_files", {}),
    }
    click.echo(json.dumps(summary, indent=2, default=str))
    if plan["n_archived"] == 0:
        click.echo("\nNothing to rotate — no completed prior years in the active log.")
    elif not do_apply:
        click.echo(
            f"\nDry run — pass --apply to seal {plan['n_archived']} entries "
            f"into {len(plan['years'])} yearly archive(s)."
        )


@cli.command(name="backfill-sub-id")
def backfill_subid_cmd():
    """One-shot (GATED): populate the v9 sub_id column on existing donation rows.

    Scans data/raw/<slug>/*.json for each owner whose rows still have NULL sub_id
    and rehydrates FEC's globally-unique record id (the §1.3 collision
    discriminator). Snapshots master.db. Idempotent; changes no published total.
    Rows whose raw is gone stay NULL and safely fall back to the transaction_id
    identity (no live collisions).
    """
    summary = backfill_subid()
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command(name="ingest-filings")
@click.option(
    "--only",
    default=None,
    help="Comma-separated file_numbers to refresh (default: every distinct filing_id on a CONFIRMED/PROBABLE donation).",
)
@click.option(
    "--force-refresh",
    is_flag=True,
    help="Re-fetch even if the filings row was refreshed within the freshness window.",
)
@click.option(
    "--max",
    "max_count",
    type=int,
    default=None,
    help="Cap the number of filings processed (for testing).",
)
def ingest_filings_cmd(only, force_refresh, max_count):
    """Enrich the filings table from OpenFEC's /v1/filings/?file_number=... endpoint.

    Backs the donation card's "Full filing PDF" link. Batches up to 50 file_numbers
    per FEC request. Idempotent — re-runs within 30 days are no-ops unless
    --force-refresh.
    """
    only_list: list[str] | None = None
    if only:
        only_list = [s.strip() for s in only.split(",") if s.strip()]
    summary = ingest_filings_orchestrator(
        only=only_list,
        force_refresh=force_refresh,
        max_count=max_count,
    )
    click.echo("")
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command(name="ingest-committee-beneficiaries")
@click.option(
    "--only",
    default=None,
    help="Comma-separated committee_ids to enrich (default: every committee with totals).",
)
@click.option(
    "--cycles",
    default=None,
    help=(
        "Comma-separated cycle years (e.g. 2022,2024) to restrict the fetch to. "
        "Default: every cycle FEC has totals for on that committee."
    ),
)
@click.option(
    "--force-refresh",
    is_flag=True,
    help="Re-fetch even if the (committee, cycle) is within the freshness window.",
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=None,
    help="Cap of top recipients per (committee, cycle). Default 200.",
)
@click.option(
    "--max",
    "max_count",
    type=int,
    default=None,
    help="Cap the number of committees processed (for testing / smoke runs).",
)
@click.option(
    "--max-fetch",
    "max_fetch",
    type=int,
    default=None,
    help=(
        "Cap how many (committee, cycle) pairs are actually FETCHED this run; "
        "oldest first, the rest deferred to the next run (§4.3). Bounds a "
        "convergence run so it can't outlive the CI job timeout."
    ),
)
def ingest_committee_beneficiaries_cmd(
    only, cycles, force_refresh, top_n, max_count, max_fetch
):
    """Enrich committee_disbursements_by_recipient from OpenFEC Schedule B by_recipient.

    For each Phase-1-enriched committee, fetches the top-N recipients it disbursed
    to per cycle. Names + amounts only — no editorial linkage to legislation or
    policy outcomes (GOVERNANCE.md §6, that's Phase 3).
    Idempotent — re-runs within 30 days per (committee, cycle) are no-ops
    unless --force-refresh.
    """
    only_list: list[str] | None = None
    if only:
        only_list = [s.strip() for s in only.split(",") if s.strip()]
    cycles_list: list[int] | None = None
    if cycles:
        try:
            cycles_list = [int(s.strip()) for s in cycles.split(",") if s.strip()]
        except ValueError:
            click.echo(f"--cycles must be comma-separated integers, got {cycles!r}.", err=True)
            sys.exit(2)
    kwargs: dict = {
        "only": only_list,
        "cycles": cycles_list,
        "force_refresh": force_refresh,
        "max_count": max_count,
        "max_fetch": max_fetch,
    }
    if top_n is not None:
        kwargs["top_n"] = top_n
    summary = ingest_all_committee_disbursements(**kwargs)
    click.echo("")
    click.echo(json.dumps(summary, indent=2, default=str))
    if summary.get("failed", 0) > 0:
        sys.exit(1)


@cli.command(name="apply-committee-external-links")
def apply_committee_external_links_cmd():
    """Apply curated external links from catalog/committee_external_links.yaml.

    Edit the YAML to add Wikipedia/Ballotpedia/etc. pointers per committee, then
    run this to push them onto the committees table. Re-runnable.
    """
    summary = apply_external_links()
    click.echo(json.dumps(summary, indent=2, default=str))
    if summary.get("error"):
        sys.exit(1)


@cli.command(name="ingest-all-pilot")
@click.option("--dry-run", is_flag=True)
@click.option("--min-date", default=None, help="Explicit min_date for ALL owners (overrides per-owner audit.last_ingestion).")
@click.option("--full-refetch", is_flag=True, help="Ignore audit.last_ingestion for every owner.")
@click.option("--include-related", is_flag=True)
def ingest_all_pilot(dry_run, min_date, full_refetch, include_related):
    """Run ingestion for every in-steady-state owner (status pilot OR active).

    Selects the same set as the production `refresh` path (`ACTIVE_STATUSES`),
    NOT just status=pilot — most owners have since been promoted from pilot to
    active (they were all `pilot` when this command was named), and selecting
    pilot-only would silently skip them. `paused`/`queued` owners are excluded.
    """
    pilots = []
    for path in sorted(OWNERS_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") in ("pilot", "active"):
            pilots.append(data["slug"])
    if not pilots:
        click.echo("No owners with status pilot/active found.")
        return
    click.echo(f"Owners (pilot+active): {', '.join(pilots)}")
    for slug in pilots:
        click.echo(f"\n========== {slug} ==========")
        ingest_entity(
            slug,
            dry_run=dry_run,
            min_date=min_date,
            full_refetch=full_refetch,
            process_related_entities=include_related,
        )


@cli.command(name="reclassify-inplace")
@click.argument("slug")
@click.option("--reason", default="", help="Reason (recorded in PROVENANCE_LOG).")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def reclassify_inplace_cmd(slug, reason, yes):
    """Re-score SLUG's attributed rows from their STORED DB fields and update status
    in place — no raw read, no delete-then-rebuild.

    Use to back-apply a signal/flag change (e.g. city_state_alone_insufficient) when a
    from-raw `reclassify` would abort/lose verified rows because FEC no longer returns
    their raw. It only RE-TIERS existing donations rows (CONFIRMED/PROBABLE → updated
    in place, or demoted to the review queue); it does not promote queue rows or
    re-route related entities. Snapshots + logs.
    """
    db.init()
    with db.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM donations WHERE (entity_slug = ? OR parent_owner_slug = ?) "
            "AND superseded_by IS NULL AND status IN ('CONFIRMED','PROBABLE')",
            (slug, slug),
        ).fetchone()[0]
    if n == 0:
        click.echo(f"No attributed rows for {slug}. Nothing to re-score.")
        return
    click.echo(f"Will re-score {n} attributed row(s) for {slug} from stored DB fields (in place).")
    if not yes and not click.confirm("Continue?", default=False):
        click.echo("Aborted.")
        return
    summary = reclassify_in_place(slug, reason=reason)
    click.echo("")
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command()
@click.argument("slug")
@click.option("--reason", default="", help="Reason for reclassification (recorded in PROVENANCE_LOG).")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--include-related", is_flag=True, help="Also classify against related_entities (spouses, children, business entities) declared in the YAML.")
@click.option("--force", is_flag=True, help="Proceed even if some attributed rows have no recoverable raw payload on disk (they will be permanently lost). Default: abort to protect those rows (GOVERNANCE.md §1.4).")
def reclassify(slug, reason, yes, include_related, force):
    """Wipe SLUG's rows and reclassify against existing raw payloads.

    Use after editing the owner YAML (signal additions, negative_signals,
    new related_entities, etc.) — applies the new rules without re-hitting
    FEC. Snapshots before deletion; logs the wipe and the ingestion run in
    PROVENANCE_LOG.md.

    Pass --include-related when the YAML has related_entities (spouses,
    children, etc.) that should be classified into their own slugs.

    This is the right tool for calibration iterations. For a fresh fetch
    from FEC, use `ingest` instead.
    """
    db.init()
    with db.connect() as conn:
        d_before = conn.execute("SELECT COUNT(*) FROM donations WHERE entity_slug = ?", (slug,)).fetchone()[0]
        r_before = conn.execute("SELECT COUNT(*) FROM review_queue WHERE entity_slug = ?", (slug,)).fetchone()[0]
        r_resolved = conn.execute(
            "SELECT COUNT(*) FROM review_queue WHERE entity_slug = ? AND resolution IS NOT NULL",
            (slug,),
        ).fetchone()[0]
    if d_before == 0 and r_before == 0:
        click.echo(f"No existing rows for {slug}. Nothing to wipe — running fresh from-raw classification.")
    else:
        click.echo(f"Will delete {d_before} donations and {r_before} review_queue rows for {slug}.")
        if r_resolved:
            click.echo(f"  WARNING: {r_resolved} of those review_queue rows have resolutions set. Those resolutions will be lost (but logged in PROVENANCE_LOG.md).")
        if not yes and not click.confirm("Continue?", default=False):
            click.echo("Aborted.")
            return
    try:
        summary = reclassify_entity(
            slug, reason=reason, include_related=include_related, force=force
        )
    except RuntimeError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)
    click.echo("")
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command()
@click.argument("slug", required=False)
def export(slug):
    """Refresh CSV exports. If SLUG omitted, exports all entities present in DB plus the aggregate."""
    if slug:
        out = export_entity(slug)
        click.echo(json.dumps(out, indent=2))
        return
    with db.connect() as conn:
        slugs = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT entity_slug FROM donations ORDER BY entity_slug"
            ).fetchall()
        ]
    for s in slugs:
        click.echo(f"Exporting {s}…")
        export_entity(s)
    agg = export_aggregate()
    household = export_household()
    click.echo(json.dumps({**agg, "household": household}, indent=2))


@cli.command()
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON instead of a table.")
def household(slug, as_json):
    """Read-only household view for an owner: own total vs. owner+related total.

    Rolls the owner together with its declared related entities (spouse/family),
    itemized by entity, kind, and tier. PROBABLE is shown as its own line and
    never folded into a single 'donated' figure — the owner total and the
    household total are reported separately so a citation can say "Steven Cohen
    gave $X; the Cohen household gave $Y" without silently merging the two
    (VERIFICATION.md anti-pattern). SLUG must be an OWNER slug.
    """
    with db.connect() as conn:
        is_owner = conn.execute(
            "SELECT 1 FROM entities WHERE slug = ? AND kind = 'owner'", (slug,)
        ).fetchone()
        rows = conn.execute(
            """
            SELECT entity_slug, entity_kind, status,
                   COUNT(*) AS donations, SUM(amount) AS total_amount
            FROM donations
            WHERE (entity_slug = ? OR parent_owner_slug = ?)
              AND status IN ('CONFIRMED', 'PROBABLE')
              AND counted = 1
            GROUP BY entity_slug, entity_kind, status
            ORDER BY (entity_kind = 'owner') DESC, entity_kind, entity_slug, status
            """,
            (slug, slug),
        ).fetchall()

    if not is_owner:
        click.echo(f"'{slug}' is not a known owner slug (run `validate` / check owners/). "
                   f"Household view expects an owner.", err=True)
        sys.exit(1)

    items = [
        {
            "entity_slug": r["entity_slug"],
            "entity_kind": r["entity_kind"],
            "status": r["status"],
            "donations": r["donations"],
            "total_amount": round(r["total_amount"] or 0, 2),
        }
        for r in rows
    ]
    owner_total = sum(i["total_amount"] for i in items if i["entity_kind"] == "owner")
    owner_n = sum(i["donations"] for i in items if i["entity_kind"] == "owner")
    related_total = sum(i["total_amount"] for i in items if i["entity_kind"] != "owner")
    related_n = sum(i["donations"] for i in items if i["entity_kind"] != "owner")
    household_total = owner_total + related_total
    household_n = owner_n + related_n
    summary = {
        "household_slug": slug,
        "owner_total": round(owner_total, 2),
        "owner_donations": owner_n,
        "related_total": round(related_total, 2),
        "related_donations": related_n,
        "household_total": round(household_total, 2),
        "household_donations": household_n,
        "by_entity": items,
    }

    if as_json:
        click.echo(json.dumps(summary, indent=2))
        return

    if not items:
        click.echo(f"No CONFIRMED/PROBABLE donations attributed to household '{slug}'.")
        return

    table = [
        [i["entity_slug"], i["entity_kind"], i["status"], i["donations"], f"${i['total_amount']:,.0f}"]
        for i in items
    ]
    click.echo(f"Household: {slug}")
    click.echo(tabulate(table, headers=["entity", "kind", "tier", "n", "amount"]))
    click.echo("")
    def _line(label, amount, n):
        return f"  {label:<30}${amount:>14,.0f}  ({n} donations)"
    click.echo(_line(f"Owner ({slug}):", owner_total, owner_n))
    if related_total or related_n:
        click.echo(_line("Related (spouse/family):", related_total, related_n))
    click.echo(_line("Household (owner + related):", household_total, household_n))
    if any(i["status"] == "PROBABLE" for i in items):
        click.echo("\n  Note: includes PROBABLE records (one confirming signal) — cite as "
                   "\"probable\", never as confirmed.")


@cli.command(name="raw-coverage")
@click.argument("slug", required=False)
def raw_coverage_cmd(slug):
    """Report live donation rows whose raw payload is missing on disk.

    master.db is the durable source of truth (GOVERNANCE.md §1.4); raw is best-effort
    ground truth. This surfaces the coverage gap (and is the same gap that gates
    `reclassify`). Pass a SLUG to scope to one entity.
    """
    from .ingest import raw_coverage_report

    db.init()
    click.echo(json.dumps(raw_coverage_report(slug), indent=2, default=str))


@cli.command(name="fetch-raw")
@click.argument("transaction_id")
@click.option("--download", is_flag=True, help="Fetch the object from R2 to a temp file (needs aws CLI + RAW_ARCHIVE_* env).")
def fetch_raw_cmd(transaction_id, download):
    """Resolve a donation's raw payload — locally, else in the off-runner archive (§2.1).

    Maps the stored `raw_payload_path` to its Cloudflare R2 key (a prefix swap:
    data/raw/… → s3://<bucket>/raw/…). Prints where the raw lives (on disk and/or in
    the bucket); with --download and RAW_ARCHIVE_* set, pulls it from R2 via the aws
    CLI. Read-only. See docs/DESIGN_raw_archival_2026-07.md.
    """
    import os
    import subprocess
    import tempfile

    db.init()
    with db.connect() as conn:
        row = conn.execute(
            "SELECT raw_payload_path FROM donations WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
    if row is None:
        raise click.ClickException(f"No donation with transaction_id {transaction_id!r}.")
    rel = row["raw_payload_path"]
    if not rel:
        raise click.ClickException(f"{transaction_id} has no raw_payload_path recorded.")

    local_exists = os.path.exists(rel)
    bucket = os.environ.get("RAW_ARCHIVE_S3_BUCKET")
    # data/raw/… → raw/… (mirror of the archive_raw.sh key layout)
    key = rel[len("data/raw/"):] if rel.startswith("data/raw/") else rel
    s3_uri = f"s3://{bucket}/raw/{key}" if bucket else f"s3://<RAW_ARCHIVE_S3_BUCKET>/raw/{key}"

    click.echo(json.dumps({
        "transaction_id": transaction_id,
        "raw_payload_path": rel,
        "local_exists": local_exists,
        "r2_uri": s3_uri,
        "bucket_configured": bool(bucket),
    }, indent=2))

    if download:
        if local_exists:
            click.echo(f"Already on disk: {rel}")
            return
        if not bucket:
            raise click.ClickException("RAW_ARCHIVE_S3_BUCKET not set — cannot fetch from R2.")
        endpoint = os.environ.get("RAW_ARCHIVE_S3_ENDPOINT")
        dest = os.path.join(tempfile.gettempdir(), os.path.basename(rel))
        cmd = ["aws", "s3", "cp", s3_uri, dest, "--endpoint-url", endpoint or ""]
        env = {**os.environ, "AWS_DEFAULT_REGION": os.environ.get("AWS_DEFAULT_REGION", "auto")}
        try:
            subprocess.run(cmd, check=True, env=env)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise click.ClickException(f"aws s3 cp failed ({e}). Is the aws CLI installed and are RAW_ARCHIVE_* set?")
        click.echo(f"Downloaded to {dest}")


@cli.command()
def review():
    """List open review-queue items."""
    db.init()
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT transaction_id, entity_slug, reason, queued_at, raw_payload_path
            FROM review_queue
            WHERE resolution IS NULL
            ORDER BY queued_at DESC, entity_slug
            """
        ).fetchall()
    if not rows:
        click.echo("Review queue empty.")
        return
    table = [[r["transaction_id"], r["entity_slug"], r["reason"][:60], r["queued_at"]] for r in rows]
    click.echo(tabulate(table, headers=["txn", "entity", "reason", "queued_at"]))
    click.echo(f"\n{len(rows)} open item(s).")
    with db.connect() as conn:
        n_res = conn.execute("SELECT COUNT(*) FROM review_resolutions WHERE resolution='DISCARDED'").fetchone()[0]
    if n_res:
        click.echo(f"{n_res} standing DISCARDED verdict(s) (suppressed from the queue).")


@cli.command(name="queue-stats")
@click.option("--top", default=0, type=int,
              help="Limit the per-owner tables to the top N rows (0 = all).")
def queue_stats_cmd(top):
    """Review-queue burndown across all owners (and states).

    The wide, read-only counterpart to `audit <slug>`: open vs resolved
    UNCERTAIN counts, per-owner P/C ratio and last-ingestion age, and
    open-reason histograms — for both master.db and state.db. Surfaces where
    the adjudication work actually is so it can be prioritized.
    """
    db.init()
    click.echo(queue_stats_report(top=top))


@cli.command()
@click.argument("transaction_id")
@click.argument("entity_slug")
@click.option("--reason", default="", help="Why this item is discarded (recorded in review_resolutions).")
@click.option("--resolution", default="DISCARDED", show_default=True, help="Verdict to record.")
def resolve(transaction_id, entity_slug, reason, resolution):
    """Record a standing verdict for one review-queue item (TRANSACTION_ID ENTITY_SLUG).

    A DISCARDED verdict permanently suppresses the transaction from re-entering
    the review queue on future ingests/reclassifies (GOVERNANCE.md §2.5). It does
    NOT affect attribution: if a later signal change makes the donor a real match,
    the record is attributed normally. Undo with `unresolve`.
    """
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.init()
    with db.connect() as conn:
        db.upsert_review_resolution(
            conn,
            transaction_id=transaction_id,
            entity_slug=entity_slug,
            resolution=resolution,
            resolution_reason=reason or None,
            resolved_at=ts,
        )
        conn.execute(
            "UPDATE review_queue SET resolution=?, resolution_reason=?, resolution_at=? "
            "WHERE transaction_id=? AND entity_slug=?",
            (resolution, reason or None, ts, transaction_id, entity_slug),
        )
    click.echo(f"Recorded {resolution} for {transaction_id} ({entity_slug}).")


@cli.command()
@click.argument("transaction_id")
@click.argument("entity_slug")
def unresolve(transaction_id, entity_slug):
    """Remove a standing verdict (undo a resolve). The item will re-queue on the
    next ingest/reclassify if it still classifies UNCERTAIN."""
    db.init()
    with db.connect() as conn:
        n = db.delete_review_resolution(
            conn, transaction_id=transaction_id, entity_slug=entity_slug
        )
        conn.execute(
            "UPDATE review_queue SET resolution=NULL, resolution_reason=NULL, resolution_at=NULL "
            "WHERE transaction_id=? AND entity_slug=?",
            (transaction_id, entity_slug),
        )
    click.echo(f"Removed {n} standing verdict(s) for {transaction_id} ({entity_slug}).")


@cli.command()
@click.argument("transaction_id")
@click.argument("entity_slug")
@click.option("--reason", required=True, help="Documented justification for the override (recorded in manual_attributions).")
@click.option("--source", default="", help="Evidence/source supporting the attribution.")
@click.option("--status", default="CONFIRMED", show_default=True, help="Status to force (CONFIRMED or PROBABLE).")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def attribute(transaction_id, entity_slug, reason, source, status, yes):
    """Manually attribute one transaction to an owner (TRANSACTION_ID ENTITY_SLUG).

    GATED DATA OPERATION — records a manual override in manual_attributions (snapshot
    + PROVENANCE_LOG), then reclassifies the owner so the override takes effect.

    This bypasses the two-signal rule by explicit, documented human decision
    (GOVERNANCE.md §1.1). Use only for records the classifier cannot safely confirm
    via signals/name_variants — e.g. a donation misfiled with the wrong generational
    suffix that no name_variant can capture without also matching a same-named
    relative. Always supply --reason (and --source where possible). Survives
    reclassify; undo with `unattribute`.
    """
    from datetime import datetime, timezone

    from .paths import PROVENANCE_LOG

    status = status.upper()
    if status not in ("CONFIRMED", "PROBABLE"):
        click.echo("--status must be CONFIRMED or PROBABLE.", err=True)
        raise SystemExit(1)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.init()
    click.echo(
        f"Will manually attribute {transaction_id} to {entity_slug} as {status}.\n"
        f"  reason: {reason}\n  source: {source or '(none)'}\n"
        f"master.db is snapshotted first; the change is logged to PROVENANCE_LOG.md, "
        f"then {entity_slug} is reclassified."
    )
    if not yes and not click.confirm("Continue?", default=False):
        click.echo("Aborted.")
        return

    snap = db.snapshot("pre-manual-attribute")
    with db.connect() as conn:
        db.upsert_manual_attribution(
            conn,
            transaction_id=transaction_id,
            entity_slug=entity_slug,
            status=status,
            reason=reason,
            source=source or None,
            attributed_at=ts,
        )
        # A manually-attributed txn must not also carry a standing DISCARDED
        # verdict (it would be contradictory state). Clear any prior discard.
        db.delete_review_resolution(conn, transaction_id=transaction_id, entity_slug=entity_slug)
    block = [
        f"\n### {ts[:10]} — MANUAL_ATTRIBUTION — {entity_slug}",
        "",
        f"- **transaction_id**: `{transaction_id}`",
        f"- **entity_slug**: `{entity_slug}`",
        f"- **forced_status**: `{status}`",
        f"- **reason**: {reason}",
        f"- **source**: {source or '(none)'}",
        f"- **snapshot_path**: `{snap}`",
        f"- **note**: Override recorded in manual_attributions (survives reclassify). Bypasses the two-signal rule by documented human decision (GOVERNANCE.md §1.1). Reversible via `unattribute`. Reclassification follows below.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)

    summary = reclassify_entity(entity_slug, reason=f"apply manual attribution of {transaction_id}")
    click.echo(json.dumps({
        "transaction_id": transaction_id,
        "entity_slug": entity_slug,
        "forced_status": status,
        "manual_overrides_applied": summary.get("manual_overrides_applied"),
        "snapshot_path": str(snap),
    }, indent=2, default=str))


@cli.command()
@click.argument("transaction_id")
@click.argument("entity_slug")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def unattribute(transaction_id, entity_slug, yes):
    """Remove a manual attribution override and reclassify (undo an attribute).

    GATED DATA OPERATION — snapshots master.db, removes the override, logs to
    PROVENANCE_LOG, then reclassifies so the record reverts to its automated verdict.
    """
    from datetime import datetime, timezone

    from .paths import PROVENANCE_LOG

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.init()
    if not yes and not click.confirm(
        f"Remove manual attribution of {transaction_id} from {entity_slug} and reclassify?",
        default=False,
    ):
        click.echo("Aborted.")
        return
    snap = db.snapshot("pre-unattribute")
    with db.connect() as conn:
        n = db.delete_manual_attribution(conn, transaction_id=transaction_id, entity_slug=entity_slug)
    if n == 0:
        click.echo(f"No manual attribution found for {transaction_id} ({entity_slug}). Nothing to do.")
        return
    block = [
        f"\n### {ts[:10]} — MANUAL_ATTRIBUTION_REMOVED — {entity_slug}",
        "",
        f"- **transaction_id**: `{transaction_id}`",
        f"- **entity_slug**: `{entity_slug}`",
        f"- **snapshot_path**: `{snap}`",
        f"- **note**: Override removed; record reverts to its automated classification on the reclassify below.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)
    reclassify_entity(entity_slug, reason=f"remove manual attribution of {transaction_id}")
    click.echo(f"Removed manual attribution for {transaction_id} ({entity_slug}) and reclassified.")


@cli.command()
@click.argument("transaction_id")
@click.argument("entity_slug")
@click.option("--reason", required=True, help="Documented justification for the exclusion (recorded in manual_attributions).")
@click.option("--source", default="", help="Evidence/source supporting the exclusion.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def exclude(transaction_id, entity_slug, reason, source, yes):
    """Manually EXCLUDE one transaction from an owner (TRANSACTION_ID ENTITY_SLUG).

    GATED DATA OPERATION — records an EXCLUDED override in manual_attributions
    (snapshot + PROVENANCE_LOG), then reclassifies so the record is dropped from
    this owner.

    The negative counterpart to `attribute`: use when the automated classifier
    WOULD attribute a record to this owner (CONFIRMED/PROBABLE) but a documented
    human decision is that it is NOT this owner and no signal can separate them —
    e.g. a same-named relative (son/parent) at the same address whose middle
    initial the classifier cannot distinguish (GOVERNANCE.md §1.1, §1.9). The txn
    is dropped from this owner's classification entirely — it is NOT routed to the
    review queue. Survives reclassify; undo with `unexclude`.
    """
    from datetime import datetime, timezone

    from .paths import PROVENANCE_LOG

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.init()
    click.echo(
        f"Will manually EXCLUDE {transaction_id} from {entity_slug} (dropped as not-this-owner).\n"
        f"  reason: {reason}\n  source: {source or '(none)'}\n"
        f"master.db is snapshotted first; the change is logged to PROVENANCE_LOG.md, "
        f"then {entity_slug} is reclassified."
    )
    if not yes and not click.confirm("Continue?", default=False):
        click.echo("Aborted.")
        return

    snap = db.snapshot("pre-manual-exclude")
    with db.connect() as conn:
        db.upsert_manual_attribution(
            conn,
            transaction_id=transaction_id,
            entity_slug=entity_slug,
            status="EXCLUDED",
            reason=reason,
            source=source or None,
            attributed_at=ts,
        )
        # An EXCLUDED txn must not also carry a standing DISCARDED verdict — both
        # keep it out, but the EXCLUDED override is the authoritative record.
        db.delete_review_resolution(conn, transaction_id=transaction_id, entity_slug=entity_slug)
    block = [
        f"\n### {ts[:10]} — MANUAL_EXCLUSION — {entity_slug}",
        "",
        f"- **transaction_id**: `{transaction_id}`",
        f"- **entity_slug**: `{entity_slug}`",
        f"- **forced_status**: `EXCLUDED`",
        f"- **reason**: {reason}",
        f"- **source**: {source or '(none)'}",
        f"- **snapshot_path**: `{snap}`",
        f"- **note**: Documented human decision that this txn is NOT this owner (GOVERNANCE.md §1.1/§1.9). Dropped from classification (not queued). Survives reclassify. Reversible via `unexclude`. Reclassification follows below.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)

    summary = reclassify_entity(entity_slug, reason=f"apply manual exclusion of {transaction_id}")
    click.echo(json.dumps({
        "transaction_id": transaction_id,
        "entity_slug": entity_slug,
        "forced_status": "EXCLUDED",
        "manual_exclusions_applied": summary.get("manual_exclusions_applied"),
        "snapshot_path": str(snap),
    }, indent=2, default=str))


@cli.command()
@click.argument("transaction_id")
@click.argument("entity_slug")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def unexclude(transaction_id, entity_slug, yes):
    """Remove a manual EXCLUDED override and reclassify (undo an exclude).

    GATED DATA OPERATION — snapshots master.db, removes the override, logs to
    PROVENANCE_LOG, then reclassifies so the record reverts to its automated verdict.
    """
    from datetime import datetime, timezone

    from .paths import PROVENANCE_LOG

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.init()
    if not yes and not click.confirm(
        f"Remove manual exclusion of {transaction_id} from {entity_slug} and reclassify?",
        default=False,
    ):
        click.echo("Aborted.")
        return
    snap = db.snapshot("pre-unexclude")
    with db.connect() as conn:
        n = db.delete_manual_attribution(conn, transaction_id=transaction_id, entity_slug=entity_slug)
    if n == 0:
        click.echo(f"No manual override found for {transaction_id} ({entity_slug}). Nothing to do.")
        return
    block = [
        f"\n### {ts[:10]} — MANUAL_EXCLUSION_REMOVED — {entity_slug}",
        "",
        f"- **transaction_id**: `{transaction_id}`",
        f"- **entity_slug**: `{entity_slug}`",
        f"- **snapshot_path**: `{snap}`",
        f"- **note**: EXCLUDED override removed; record reverts to its automated classification on the reclassify below.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)
    reclassify_entity(entity_slug, reason=f"remove manual exclusion of {transaction_id}")
    click.echo(f"Removed manual exclusion for {transaction_id} ({entity_slug}) and reclassified.")


@cli.command(name="bulk-discard")
@click.option("--reason-like", required=True, help="SQL LIKE pattern matched against review_queue.reason (e.g. 'city/state outside%').")
@click.option("--only", default=None, help="Restrict to one entity_slug.")
@click.option("--note", default="", help="Resolution note recorded on each item.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def bulk_discard_cmd(reason_like, only, note, yes):
    """Discard every OPEN review-queue item whose reason matches a LIKE pattern.

    GATED DATA OPERATION — snapshots master.db first and appends a PROVENANCE_LOG
    entry. Records a standing DISCARDED verdict per item (survives reclassify) and
    suppresses each from re-queuing (GOVERNANCE.md §2.5). Attribution is never
    affected — only the UNCERTAIN queue. Reversible per-item via `unresolve`.
    """
    from datetime import datetime, timezone

    from .paths import PROVENANCE_LOG

    db.init()
    where = "resolution IS NULL AND reason LIKE ?"
    params: list = [reason_like]
    if only:
        where += " AND entity_slug = ?"
        params.append(only)
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT transaction_id, entity_slug, reason FROM review_queue WHERE {where}",
            params,
        ).fetchall()
    if not rows:
        click.echo("No open items match. Nothing to do.")
        return
    by_slug: dict[str, int] = {}
    for r in rows:
        by_slug[r["entity_slug"]] = by_slug.get(r["entity_slug"], 0) + 1
    click.echo(f"Will DISCARD {len(rows)} open item(s) matching reason LIKE {reason_like!r}"
               + (f" for {only}" if only else "") + ".")
    click.echo("Per owner: " + ", ".join(f"{k}={v}" for k, v in sorted(by_slug.items(), key=lambda x: -x[1])))
    click.echo("master.db is snapshotted first; the change is logged to PROVENANCE_LOG.md.")
    if not yes and not click.confirm("Continue?", default=False):
        click.echo("Aborted.")
        return

    snap = db.snapshot("pre-bulk-discard")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.connect() as conn:
        for r in rows:
            db.upsert_review_resolution(
                conn,
                transaction_id=r["transaction_id"],
                entity_slug=r["entity_slug"],
                resolution="DISCARDED",
                resolution_reason=note or f"bulk-discard: reason LIKE {reason_like}",
                resolved_at=ts,
            )
        conn.execute(
            f"UPDATE review_queue SET resolution='DISCARDED', resolution_reason=?, resolution_at=? "
            f"WHERE {where}",
            [note or f"bulk-discard: reason LIKE {reason_like}", ts, *params],
        )
        remaining = conn.execute("SELECT COUNT(*) FROM review_queue WHERE resolution IS NULL").fetchone()[0]

    block = [
        f"\n### {ts[:10]} — RESOLUTION — bulk-discard review-queue items",
        "",
        f"- **reason_like**: `{reason_like}`",
        f"- **scope**: `{only or 'all owners'}`",
        f"- **items_discarded**: `{len(rows)}`",
        f"- **per_owner**: {', '.join(f'{k}={v}' for k, v in sorted(by_slug.items(), key=lambda x: -x[1]))}",
        f"- **open_queue_remaining**: `{remaining}`",
        f"- **snapshot_path**: `{snap}`",
        f"- **note**: Standing DISCARDED verdicts recorded in review_resolutions (survive reclassify). Attribution unaffected (GOVERNANCE.md §2.5). Reversible via `unresolve`.",
        "",
    ]
    append_provenance("\n".join(block), PROVENANCE_LOG)
    click.echo(json.dumps({"discarded": len(rows), "open_queue_remaining": remaining, "snapshot_path": str(snap)}, indent=2, default=str))


@cli.command(name="backfill-pre2006-filing-id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def backfill_pre2006_filing_id_cmd(yes):
    """One-shot: stamp FEC-PRE2006-NOID on rows with a blank filing_id (H3).

    GATED DATA OPERATION — mutates master.db (snapshots first, appends a
    PROVENANCE_LOG entry). Run deliberately; it is not part of any automated
    workflow. Idempotent.
    """
    from .backfill_pre2006_filing_id import backfill as _backfill

    db.init()
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM donations WHERE filing_id = ''").fetchone()[0]
    if n == 0:
        click.echo("No rows with a blank filing_id. Nothing to do.")
        return
    click.echo(
        f"Will set filing_id = FEC-PRE2006-NOID on {n} row(s). "
        f"master.db is snapshotted first and the change is logged to PROVENANCE_LOG.md."
    )
    if not yes and not click.confirm("Continue?", default=False):
        click.echo("Aborted.")
        return
    summary = _backfill()
    click.echo(json.dumps(summary, indent=2, default=str))


@cli.command(name="export-review-queue")
def export_review_queue_cmd():
    """Regenerate catalog/REVIEW_QUEUE.md from the review_queue table.

    The .md is a human-readable mirror and is no longer git-tracked (it grew
    unbounded); the review_queue table in master.db is the source of truth. Full
    contributor detail for an item lives in its raw payload (raw_payload_path).
    """
    from datetime import datetime, timezone

    from .paths import REVIEW_QUEUE_MD

    db.init()
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT transaction_id, entity_slug, reason, queued_at, raw_payload_path,
                   resolution, resolution_reason, resolution_at
            FROM review_queue
            ORDER BY entity_slug, queued_at
            """
        ).fetchall()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# Review queue — regenerated {ts} from the review_queue table",
        f"# {len(rows)} item(s). Source of truth: review_queue in master.db.",
        "",
    ]
    for r in rows:
        status = r["resolution"] or "pending"
        lines.append(f"### {r['transaction_id']} — {r['entity_slug']} — {status}")
        lines.append(f"- Reason: {r['reason']}")
        lines.append(f"- Queued at: {r['queued_at']}")
        lines.append(f"- Raw payload: {r['raw_payload_path']}")
        if r["resolution"]:
            lines.append(
                f"- Resolution: {r['resolution']} ({r['resolution_at']}) — {r['resolution_reason'] or ''}"
            )
        lines.append("")
    REVIEW_QUEUE_MD.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_QUEUE_MD.write_text("\n".join(lines), encoding="utf-8")
    click.echo(f"Wrote {REVIEW_QUEUE_MD} ({len(rows)} item(s)).")


@cli.command()
def status():
    """Show per-owner ingestion freshness."""
    db.init()
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT e.slug,
                   e.name,
                   e.team,
                   (SELECT MAX(completed_at) FROM ingestion_runs ir WHERE ir.entity_slug = e.slug) AS last_run,
                   (SELECT COUNT(*) FROM donations d WHERE d.entity_slug = e.slug AND d.status='CONFIRMED') AS confirmed,
                   (SELECT COUNT(*) FROM donations d WHERE d.entity_slug = e.slug AND d.status='PROBABLE') AS probable,
                   (SELECT COUNT(*) FROM review_queue rq WHERE rq.entity_slug = e.slug AND rq.resolution IS NULL) AS uncertain_open
            FROM entities e
            ORDER BY e.slug
            """
        ).fetchall()
    if not rows:
        click.echo("No entities loaded. Run `cli init` (refreshes entities) or add owners.")
        return
    table = [
        [r["slug"], r["team"], r["last_run"] or "—", r["confirmed"], r["probable"], r["uncertain_open"]]
        for r in rows
    ]
    click.echo(tabulate(table, headers=["slug", "team", "last_run", "CONFIRMED", "PROBABLE", "UNCERTAIN open"]))


@cli.command()
@click.argument("slug")
def audit(slug):
    """Read-only signal audit for one owner.

    Surfaces the current signal-block summary, classification counts,
    PROBABLE records grouped by employer + ZIP, REVIEW_QUEUE reasons, and
    a heuristic suggestion checklist for tightening signals.

    Apply changes by editing the owner YAML with a change_log entry
    (GOVERNANCE.md §1.7), then `reclassify <slug>`. See
    docs/CALIBRATION_PLAYBOOK.md for the full calibration loop.
    """
    db.init()
    click.echo(audit_slug(slug))


@cli.command(name="sample")
@click.argument("slug")
@click.option("--status", "status_filter", default=None, type=click.Choice(["CONFIRMED", "PROBABLE", "UNCERTAIN"]))
@click.option("--n", default=5)
def sample(slug, status_filter, n):
    """Print N random sample records for sanity-checking."""
    db.init()
    if status_filter == "UNCERTAIN":
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM review_queue WHERE entity_slug = ? ORDER BY RANDOM() LIMIT ?",
                (slug, n),
            ).fetchall()
    else:
        with db.connect() as conn:
            if status_filter:
                rows = conn.execute(
                    "SELECT * FROM donations WHERE entity_slug = ? AND status = ? ORDER BY RANDOM() LIMIT ?",
                    (slug, status_filter, n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM donations WHERE entity_slug = ? ORDER BY RANDOM() LIMIT ?",
                    (slug, n),
                ).fetchall()
    for r in rows:
        click.echo(json.dumps(dict(r), default=str, indent=2))
        click.echo("---")


if __name__ == "__main__":
    cli()

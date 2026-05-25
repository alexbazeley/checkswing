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
from .export import export_aggregate, export_entity
from .ingest import ingest_entity, reclassify_entity
from .ingest_committees import ingest_all_committees
from .paths import OWNERS_DIR
from .refresh import refresh_all, select_bucket
from .validate_owners import format_report, validate_all


@click.group()
def cli():
    """MLB Owner FEC Donations Archive."""


@cli.command()
def validate():
    """Validate every owner YAML against OWNER_SCHEMA.md rules."""
    results = validate_all()
    click.echo(format_report(results))
    sys.exit(0 if all(r.ok for r in results) else 1)


@cli.command()
def init():
    """Create the SQLite schema (idempotent)."""
    db.init()
    db.refresh_entities()
    click.echo(f"Initialized {db.MASTER_DB}")


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
def ingest_committees_cmd(only, force_refresh, max_count):
    """Enrich the committees and committee_totals tables from OpenFEC.

    Fetches /committee/<id>/ (identity) and /committee/<id>/totals/ (per-cycle
    scale) for every committee that has received an attributed donation.
    Idempotent — re-runs within 30 days are no-ops unless --force-refresh.
    """
    only_list: list[str] | None = None
    if only:
        only_list = [s.strip() for s in only.split(",") if s.strip()]
    summary = ingest_all_committees(
        only=only_list,
        force_refresh=force_refresh,
        max_count=max_count,
    )
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
@click.option("--min-date", default=None, help="Explicit min_date for ALL pilots (overrides per-owner audit.last_ingestion).")
@click.option("--full-refetch", is_flag=True, help="Ignore audit.last_ingestion for every pilot.")
@click.option("--include-related", is_flag=True)
def ingest_all_pilot(dry_run, min_date, full_refetch, include_related):
    """Run ingestion for every entity marked status=pilot in owners/."""
    pilots = []
    for path in sorted(OWNERS_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") == "pilot":
            pilots.append(data["slug"])
    if not pilots:
        click.echo("No owners with status=pilot found.")
        return
    click.echo(f"Pilots: {', '.join(pilots)}")
    for slug in pilots:
        click.echo(f"\n========== {slug} ==========")
        ingest_entity(
            slug,
            dry_run=dry_run,
            min_date=min_date,
            full_refetch=full_refetch,
            process_related_entities=include_related,
        )


@cli.command()
@click.argument("slug")
@click.option("--reason", default="", help="Reason for reclassification (recorded in PROVENANCE_LOG).")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@click.option("--include-related", is_flag=True, help="Also classify against related_entities (spouses, children, business entities) declared in the YAML.")
def reclassify(slug, reason, yes, include_related):
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
    summary = reclassify_entity(slug, reason=reason, include_related=include_related)
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
    click.echo(json.dumps(agg, indent=2))


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
    (CLAUDE.md §1.7), then `reclassify --from-raw <slug>`.
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

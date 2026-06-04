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
from .export import export_aggregate, export_entity
from .ingest import ingest_entity, reclassify_entity
from .ingest_committee_disbursements import (
    ingest_all_committee_disbursements,
)
from .ingest_committees import ingest_all_committees
from .ingest_filings import ingest_filings as ingest_filings_orchestrator
from .paths import OWNERS_DIR
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

    ok = all(r.ok for r in owner_results) and all(r.ok for r in leg_results)
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
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")

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
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")

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
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")

    click.echo(json.dumps(counts, indent=2))


@cli.command(name="policy-join")
@click.option("--bill", "bills", multiple=True, help="Vote-bearing bill_id(s): join donations→legislators who voted on these.")
@click.option("--sponsors-of", "sponsor_bills", multiple=True, help="Bill_id(s): also join donations→sponsors/cosponsors of these.")
@click.option("--out", "basename", required=True, help="Output basename written under reports/data/.")
def policy_join_cmd(bills, sponsor_bills, basename):
    """Read-only: write the neutral owner→donation→legislator→vote join to reports/data/.

    Produces a reproducible CSV + JSON of neutral facts (donation, legislator,
    position, days_before_vote). NO interpretation — that lives in the brief.
    """
    from .policy_join import (
        sponsor_donation_rows,
        summarize_by_owner,
        vote_donation_rows,
        write_outputs,
    )

    if not bills and not sponsor_bills:
        click.echo("Pass at least one --bill or --sponsors-of.")
        return

    written = {}
    vote_rows = vote_donation_rows(bill_ids=list(bills)) if bills else []
    if bills:
        written["votes"] = write_outputs(
            vote_rows,
            basename=basename,
            meta={"join": "donations_to_votes", "bill_ids": list(bills)},
        )
    if sponsor_bills:
        sp_rows = sponsor_donation_rows(bill_ids=list(sponsor_bills))
        written["sponsors"] = write_outputs(
            sp_rows,
            basename=f"{basename}-sponsors",
            meta={"join": "donations_to_sponsors", "bill_ids": list(sponsor_bills)},
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
def ingest_committee_beneficiaries_cmd(only, cycles, force_refresh, top_n, max_count):
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
    click.echo(json.dumps(agg, indent=2))


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
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")

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
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")
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
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")

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
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")
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
    existing = PROVENANCE_LOG.read_text(encoding="utf-8") if PROVENANCE_LOG.exists() else ""
    PROVENANCE_LOG.write_text(existing + "\n".join(block), encoding="utf-8")
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

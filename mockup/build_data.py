#!/usr/bin/env python3
"""
Build mockup/data.json from data/master.db.

This is the dashboard mockup's data pipeline: a one-shot exporter that
denormalizes everything the front end needs into a single JSON file.

Re-run after each ingestion to refresh the mockup. The mockup is a
presentation layer — it never reads master.db directly.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "master.db"
OUT_PATH = REPO_ROOT / "mockup" / "data.json"
PROVENANCE_SRC = REPO_ROOT / "catalog" / "PROVENANCE_LOG.md"
PROVENANCE_OUT = REPO_ROOT / "mockup" / "provenance.json"

# Allow `from scripts.* import …` when this script is run directly
# (Cloudflare invokes it as `python mockup/build_data.py`).
sys.path.insert(0, str(REPO_ROOT))
from scripts.dollars import (  # noqa: E402
    CPI_BASE_YEAR,
    CPI_LATEST_MONTH,
    CPI_TABLE,
    committee_type_label,
    to_real,
)
from scripts.parse_provenance import parse_provenance_file  # noqa: E402


def filing_page_url(filing_id) -> str | None:
    """
    Public FEC filings detail page for this filing. Returns the modern
    fec.gov data-portal URL, which always renders without auth.

    Why not link the raw PDF? The canonical PDF lives at
    docquery.fec.gov/pdf/<shard>/<image_number>/<image_number>.pdf where shard
    is the last 3 digits of the FILING's image_number (not the file_number,
    not a transaction's image_number — the filing record's own image_number,
    which we'd have to fetch from /v1/filings/?file_number=<id>). That's a
    pending data-enrichment job; in the meantime this URL is the closest
    publicly-reachable surface to "the filing" the donation came from. The
    fec.gov page has its own link to the FEC-hosted PDF.
    """
    if not filing_id:
        return None
    fid = str(filing_id).strip()
    if not fid.isdigit():
        return None
    return f"https://www.fec.gov/data/filings/?file_number={fid}"


def load_raw_payload_index(repo_root: Path, slugs: set[str]) -> dict[str, dict]:
    """
    Read every raw FEC payload under data/raw/<slug>/ for each given slug, and
    index transactions by transaction_id. This is the only place we cross from
    the DB into the raw archive.

    We walk the full per-owner dir (not just the donations' stamped
    raw_payload_path) so that transactions whose stamped page was clobbered
    by a same-second filename collision still get recovered from a sibling
    payload (different name variant or cycle that returned the same txn).
    The stamped path stays the provenance pointer; this index is just how the
    UI finds the FEC image URL.
    """
    raw_root = repo_root / "data" / "raw"
    files: list[Path] = []
    for slug in slugs:
        slug_dir = raw_root / slug
        if not slug_dir.is_dir():
            continue
        for p in sorted(slug_dir.glob("*.json")):
            # Skip checkpoint/state files; they live alongside payloads.
            if p.name.startswith("_"):
                continue
            files.append(p)

    index: dict[str, dict] = {}
    for path in files:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        results = (data.get("response") or {}).get("results") or []
        for r in results:
            txn = r.get("transaction_id")
            if not txn:
                continue
            # Recipient committee type can live on the top-level result or
            # nested under `committee`. Prefer top-level (cleaner pull).
            rct = r.get("recipient_committee_type")
            if not rct:
                cmt = r.get("committee") or {}
                rct = cmt.get("committee_type") if isinstance(cmt, dict) else None
            index[txn] = {
                "image_number": r.get("image_number"),
                "pdf_url": r.get("pdf_url"),
                "filing_form": r.get("filing_form"),
                "line_number": r.get("line_number"),
                "receipt_type_full": r.get("receipt_type_full"),
                "committee_type": rct,
            }
    return index


def normalize_party(raw: str | None) -> str:
    if not raw:
        return "OTH"
    p = raw.strip().upper()
    if p in {"DEM", "DEMOCRAT", "DEMOCRATIC"}:
        return "DEM"
    if p in {"REP", "REPUBLICAN", "GOP"}:
        return "REP"
    if p in {"IND", "INDEPENDENT"}:
        return "IND"
    if p in {"LIB", "LIBERTARIAN"}:
        return "LIB"
    if p in {"GRE", "GREEN"}:
        return "GRE"
    return "OTH"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Entities (owner + spouse + etc.)
    cur.execute(
        """
        SELECT slug, kind, parent_slug, name, team,
               tenure_start_date, tenure_end_date, family_tenure_start_date,
               refreshed_at
        FROM entities
        """
    )
    entities = {row["slug"]: dict(row) for row in cur.fetchall()}

    # Ingestion runs — full history. The dashboard's freshness layer surfaces
    # these as pipeline transparency.
    cur.execute(
        """
        SELECT run_id, entity_slug, started_at, completed_at,
               period_start, period_end, name_variants_queried,
               api_calls_made, records_fetched,
               confirmed_count, probable_count, uncertain_count,
               snapshot_path, notes, dry_run
        FROM ingestion_runs
        ORDER BY started_at DESC
        """
    )
    runs = []
    for row in cur.fetchall():
        r = dict(row)
        # Parse the JSON-encoded name_variants_queried for display
        try:
            r["name_variants_queried"] = json.loads(r["name_variants_queried"] or "[]")
        except (TypeError, ValueError):
            r["name_variants_queried"] = []
        runs.append(r)

    # Index runs by entity for quick per-owner lookup
    runs_by_entity = {}
    for r in runs:
        runs_by_entity.setdefault(r["entity_slug"], []).append(r)

    # Donations: CONFIRMED + PROBABLE only (matches export rule)
    cur.execute(
        """
        SELECT transaction_id, entity_slug, entity_kind, parent_owner_slug,
               status, status_reason, signals_matched,
               contributor_name_raw, contributor_employer_raw,
               contributor_occupation_raw, contributor_city,
               contributor_state, contributor_zip,
               recipient_committee_id, recipient_committee_name,
               recipient_candidate_id, recipient_candidate_name,
               recipient_party, recipient_office,
               amount, date, election_cycle, report_type, filing_id,
               raw_payload_path, ingested_at
        FROM donations
        WHERE status IN ('CONFIRMED', 'PROBABLE')
        ORDER BY date DESC
        """
    )
    donations_raw = [dict(row) for row in cur.fetchall()]

    # Index every raw FEC payload once, keyed by transaction_id. This is how
    # we surface per-transaction FEC image links (pdf_url / image_number) and
    # the FEC line item number, none of which live in the DB schema. We walk
    # the full per-owner dir so transactions whose stamped page was clobbered
    # by a same-second filename collision still get found in a sibling payload.
    donation_slugs = {d["entity_slug"] for d in donations_raw if d["entity_slug"]}
    raw_index = load_raw_payload_index(REPO_ROOT, donation_slugs)

    # Recovery accounting: how many donations would have been "image link not
    # available" under the old stamped-path-only index? Surface in stdout so
    # PROVENANCE_LOG entries can cite a real number.
    stamped_index_hits = 0
    wide_index_hits = 0
    for d in donations_raw:
        txn = d["transaction_id"]
        stamped = d["raw_payload_path"]
        wide_hit = txn in raw_index
        stamped_hit = False
        if stamped:
            stamped_path = REPO_ROOT / stamped
            if stamped_path.exists():
                try:
                    sd = json.loads(stamped_path.read_text())
                    stamped_hit = any(
                        r.get("transaction_id") == txn
                        for r in (sd.get("response") or {}).get("results") or []
                    )
                except (OSError, ValueError):
                    stamped_hit = False
        if stamped_hit:
            stamped_index_hits += 1
        if wide_hit:
            wide_index_hits += 1
    recovered = wide_index_hits - stamped_index_hits
    print(
        f"raw_payload_index: {wide_index_hits}/{len(donations_raw)} donations resolved "
        f"(recovered {recovered} from sibling payloads after stamped-path miss)"
    )

    # Normalize, trim, and project to display shape.
    donations = []
    for d in donations_raw:
        signals = []
        if d["signals_matched"]:
            try:
                signals = json.loads(d["signals_matched"])
            except (TypeError, ValueError):
                signals = []
        extra = raw_index.get(d["transaction_id"], {})
        donations.append(
            {
                "id": d["transaction_id"],
                "entity": d["entity_slug"],
                "entity_kind": d["entity_kind"],
                "parent": d["parent_owner_slug"],
                "status": d["status"],
                "status_reason": d["status_reason"],
                "signals": signals,
                "donor_name": d["contributor_name_raw"],
                "employer": d["contributor_employer_raw"],
                "occupation": d["contributor_occupation_raw"],
                "city": d["contributor_city"],
                "state": d["contributor_state"],
                "zip": d["contributor_zip"],
                "committee_id": d["recipient_committee_id"],
                "committee": d["recipient_committee_name"],
                "candidate_id": d["recipient_candidate_id"],
                "candidate": d["recipient_candidate_name"],
                "party": normalize_party(d["recipient_party"]),
                "party_raw": d["recipient_party"],
                "office": d["recipient_office"],
                "amount": d["amount"],
                # CPI-adjusted to CPI_BASE_YEAR. Baked at build time so the
                # frontend can flip the inflation toggle without re-aggregating.
                "amount_2026": to_real(d["amount"], d["election_cycle"]),
                "date": d["date"],
                "cycle": d["election_cycle"],
                "report_type": d["report_type"],
                "filing_id": d["filing_id"],
                "raw_payload": d["raw_payload_path"],
                "ingested_at": d["ingested_at"],
                # FEC deep-link fields (sourced from raw payloads, not DB)
                "image_number": extra.get("image_number"),
                "pdf_url": extra.get("pdf_url"),
                "filing_form": extra.get("filing_form"),
                "line_number": extra.get("line_number"),
                "receipt_type": extra.get("receipt_type_full"),
                "committee_type": extra.get("committee_type"),
                "recipient_type": committee_type_label(extra.get("committee_type")),
                "filing_page_url": filing_page_url(d["filing_id"]),
            }
        )

    # ── Per-owner aggregates ────────────────────────────────────────────────
    owners_summary = {}

    # Roll spouse/family donations up under their parent owner for aggregates,
    # but keep the original entity_kind so the UI can flag them.
    owner_donations = defaultdict(list)
    for d in donations:
        roll_to = d["parent"] if d["parent"] else d["entity"]
        owner_donations[roll_to].append(d)

    for slug, ent in entities.items():
        if ent["kind"] != "owner":
            continue

        my_donations = owner_donations.get(slug, [])
        total_amount = sum(d["amount"] for d in my_donations)
        total_amount_2026 = sum(d["amount_2026"] for d in my_donations)
        n_total = len(my_donations)
        n_confirmed = sum(1 for d in my_donations if d["status"] == "CONFIRMED")
        n_probable = sum(1 for d in my_donations if d["status"] == "PROBABLE")

        # Party split by dollars (both currencies)
        party_dollars = defaultdict(float)
        party_dollars_2026 = defaultdict(float)
        for d in my_donations:
            party_dollars[d["party"]] += d["amount"]
            party_dollars_2026[d["party"]] += d["amount_2026"]

        # Sparkline: dollars per cycle 2000..2026 (even years) — both currencies
        cycle_dollars = defaultdict(float)
        cycle_dollars_2026 = defaultdict(float)
        # Per-cycle, per-party breakdown for B.2 heatmap.
        cycle_party_dollars: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        cycle_party_dollars_2026: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        cycle_count: dict[int, int] = defaultdict(int)
        for d in my_donations:
            if d["cycle"]:
                c = int(d["cycle"])
                cycle_dollars[c] += d["amount"]
                cycle_dollars_2026[c] += d["amount_2026"]
                cycle_party_dollars[c][d["party"]] += d["amount"]
                cycle_party_dollars_2026[c][d["party"]] += d["amount_2026"]
                cycle_count[c] += 1

        # Top 5 recipient committees by dollars (both currencies)
        committee_dollars = defaultdict(float)
        committee_dollars_2026 = defaultdict(float)
        committee_count = defaultdict(int)
        committee_name = {}
        committee_party = {}
        for d in my_donations:
            cid = d["committee_id"] or "_unknown"
            committee_dollars[cid] += d["amount"]
            committee_dollars_2026[cid] += d["amount_2026"]
            committee_count[cid] += 1
            committee_name[cid] = d["committee"]
            committee_party[cid] = d["party"]
        top_recipients = [
            {
                "committee_id": cid,
                "committee": committee_name[cid],
                "party": committee_party[cid],
                "amount": committee_dollars[cid],
                "amount_2026": committee_dollars_2026[cid],
                "count": committee_count[cid],
            }
            for cid in sorted(committee_dollars, key=committee_dollars.get, reverse=True)[:8]
        ]

        # Distinct recipients
        distinct_recipients = len(committee_dollars)

        # Earliest and latest donation
        if my_donations:
            dates = sorted(d["date"] for d in my_donations if d["date"])
            earliest = dates[0] if dates else None
            latest = dates[-1] if dates else None
        else:
            earliest = latest = None

        # Most recent ingestion run for this owner (for freshness UI)
        my_runs = runs_by_entity.get(slug, [])
        last_run = my_runs[0] if my_runs else None
        last_run_summary = None
        if last_run:
            last_run_summary = {
                "run_id": last_run["run_id"],
                "started_at": last_run["started_at"],
                "completed_at": last_run["completed_at"],
                "records_fetched": last_run["records_fetched"],
                "confirmed_count": last_run["confirmed_count"],
                "probable_count": last_run["probable_count"],
                "uncertain_count": last_run["uncertain_count"],
                "api_calls_made": last_run["api_calls_made"],
            }

        owners_summary[slug] = {
            "slug": slug,
            "name": ent["name"],
            "team": ent["team"],
            "tenure_start": ent["tenure_start_date"],
            "tenure_end": ent["tenure_end_date"],
            "family_tenure_start": ent["family_tenure_start_date"],
            "total_amount": total_amount,
            "total_amount_2026": total_amount_2026,
            "n_total": n_total,
            "n_confirmed": n_confirmed,
            "n_probable": n_probable,
            "party_dollars": dict(party_dollars),
            "party_dollars_2026": dict(party_dollars_2026),
            "cycle_dollars": dict(cycle_dollars),
            "cycle_dollars_2026": dict(cycle_dollars_2026),
            "cycle_party_dollars": {str(c): dict(v) for c, v in cycle_party_dollars.items()},
            "cycle_party_dollars_2026": {str(c): dict(v) for c, v in cycle_party_dollars_2026.items()},
            "cycle_count": {str(c): n for c, n in cycle_count.items()},
            "top_recipients": top_recipients,
            "distinct_recipients": distinct_recipients,
            "earliest_date": earliest,
            "latest_date": latest,
            # Freshness audit
            "last_refreshed": ent["refreshed_at"],   # from entities table — when YAML last refreshed into DB
            "last_run": last_run_summary,            # the most recent ingestion run for this owner
            "n_runs": len(my_runs),                  # total ingestion runs ever for this owner
        }

    # ── League-wide aggregates ───────────────────────────────────────────────
    league_cycle_party = defaultdict(lambda: defaultdict(float))
    league_cycle_party_2026 = defaultdict(lambda: defaultdict(float))
    league_cycle_count = defaultdict(int)
    league_total = 0.0
    league_total_2026 = 0.0
    for d in donations:
        if d["cycle"]:
            c = int(d["cycle"])
            league_cycle_party[c][d["party"]] += d["amount"]
            league_cycle_party_2026[c][d["party"]] += d["amount_2026"]
            league_cycle_count[c] += 1
        league_total += d["amount"]
        league_total_2026 += d["amount_2026"]

    league = {
        "total_amount": league_total,
        "total_amount_2026": league_total_2026,
        "n_donations": len(donations),
        "n_confirmed": sum(1 for d in donations if d["status"] == "CONFIRMED"),
        "n_probable": sum(1 for d in donations if d["status"] == "PROBABLE"),
        "n_owners": len(owners_summary),
        "n_cycles": len(league_cycle_party),
        "earliest_date": min((d["date"] for d in donations if d["date"]), default=None),
        "latest_date": max((d["date"] for d in donations if d["date"]), default=None),
        "by_cycle": {
            str(c): {
                "dollars_by_party": dict(league_cycle_party[c]),
                "dollars_by_party_2026": dict(league_cycle_party_2026[c]),
                "count": league_cycle_count[c],
                "total": sum(league_cycle_party[c].values()),
                "total_2026": sum(league_cycle_party_2026[c].values()),
            }
            for c in sorted(league_cycle_party)
        },
    }

    # Top league-wide recipients
    league_committee = defaultdict(
        lambda: {"amount": 0.0, "amount_2026": 0.0, "count": 0, "name": None, "party": None}
    )
    for d in donations:
        cid = d["committee_id"] or "_unknown"
        league_committee[cid]["amount"] += d["amount"]
        league_committee[cid]["amount_2026"] += d["amount_2026"]
        league_committee[cid]["count"] += 1
        league_committee[cid]["name"] = d["committee"]
        league_committee[cid]["party"] = d["party"]
    league["top_recipients"] = sorted(
        [
            {
                "committee_id": cid,
                "committee": v["name"],
                "party": v["party"],
                "amount": v["amount"],
                "amount_2026": v["amount_2026"],
                "count": v["count"],
            }
            for cid, v in league_committee.items()
        ],
        key=lambda r: r["amount"],
        reverse=True,
    )[:25]

    # ── Recipients (full distinct-committee rollup for the /#/recipients page) ─
    # Each entry has both currencies, owner_count, cycles_active, and a
    # recipient_type bucket derived from the FEC committee_type code via
    # scripts.dollars.committee_type_label. Skipping "_unknown" because the
    # frontend can't link through to a missing committee_id.
    rec_data: dict[str, dict] = {}
    for d in donations:
        cid = d["committee_id"]
        if not cid:
            continue
        if cid not in rec_data:
            rec_data[cid] = {
                "committee_id": cid,
                "committee": d["committee"],
                "party": d["party"],
                "recipient_type": d.get("recipient_type") or "Other",
                "total_amount": 0.0,
                "total_amount_2026": 0.0,
                "n_donations": 0,
                "_owner_slugs": set(),
                "_cycles": set(),
                "earliest_date": d["date"],
                "latest_date": d["date"],
            }
        r = rec_data[cid]
        r["total_amount"] += d["amount"]
        r["total_amount_2026"] += d["amount_2026"]
        r["n_donations"] += 1
        r["_owner_slugs"].add(d["parent"] or d["entity"])
        if d["cycle"]:
            r["_cycles"].add(int(d["cycle"]))
        if d["date"] and (not r["earliest_date"] or d["date"] < r["earliest_date"]):
            r["earliest_date"] = d["date"]
        if d["date"] and (not r["latest_date"] or d["date"] > r["latest_date"]):
            r["latest_date"] = d["date"]
        # Prefer the most specific recipient_type seen — if any donation has a
        # known bucket, keep it; if all are "Other", that's what we end up with.
        if r["recipient_type"] == "Other" and (d.get("recipient_type") or "Other") != "Other":
            r["recipient_type"] = d["recipient_type"]

    # Pull committee-enrichment rows from the v2 schema. join in identity
    # fields and bundle per-cycle totals into a sibling map. Tolerant of a
    # pre-v2 DB (no committees table yet): treat as no enrichment data,
    # recipients[] still renders the legacy shape.
    enrichment_by_cid: dict[str, dict] = {}
    scale_by_cid: dict[str, list[dict]] = {}
    relevant_cids = tuple(rec_data.keys())
    if relevant_cids:
        placeholders = ",".join(["?"] * len(relevant_cids))
        try:
            cur.execute(
                f"""
                SELECT committee_id, name, designation, designation_label,
                       committee_type, committee_type_label, party, party_full,
                       organization_type, affiliated_committee_name,
                       treasurer_name, custodian_name, city, state, zip,
                       filing_frequency, first_file_date, last_file_date,
                       last_f1_date, is_terminated,
                       external_link, external_link_label, external_link_source,
                       refreshed_at
                  FROM committees
                 WHERE committee_id IN ({placeholders})
                """,
                relevant_cids,
            )
            for row in cur.fetchall():
                enrichment_by_cid[row["committee_id"]] = dict(row)

            cur.execute(
                f"""
                SELECT committee_id, cycle, receipts, disbursements,
                       cash_on_hand_end_period, individual_contributions,
                       other_political_committee_contributions, independent_expenditures,
                       coverage_start_date, coverage_end_date
                  FROM committee_totals
                 WHERE committee_id IN ({placeholders})
                 ORDER BY committee_id, cycle
                """,
                relevant_cids,
            )
            for row in cur.fetchall():
                scale_by_cid.setdefault(row["committee_id"], []).append(dict(row))
        except sqlite3.OperationalError as e:
            # Schema v1 DB — committees tables don't exist yet. That's fine for
            # the legacy render path. Run `python -m scripts.cli init` to bump.
            if "no such table" in str(e):
                print(
                    f"note: committee enrichment tables not present yet ({e}). "
                    f"Run `python -m scripts.cli init` to migrate.",
                    file=sys.stderr,
                )
            else:
                raise

    recipients = []
    for r in rec_data.values():
        entry = {
            "committee_id": r["committee_id"],
            "committee": r["committee"],
            "party": r["party"],
            "recipient_type": r["recipient_type"],
            "total_amount": r["total_amount"],
            "total_amount_2026": r["total_amount_2026"],
            "n_donations": r["n_donations"],
            "owner_count": len(r["_owner_slugs"]),
            "cycles_active": sorted(r["_cycles"]),
            "earliest_date": r["earliest_date"],
            "latest_date": r["latest_date"],
        }
        enr = enrichment_by_cid.get(r["committee_id"])
        if enr:
            # Prefer FEC's name over the most-recently-seen donation name —
            # the donation field is whatever the filer typed on Schedule A.
            entry["committee"] = enr.get("name") or entry["committee"]
            entry["designation"] = enr.get("designation")
            entry["designation_label"] = enr.get("designation_label")
            entry["committee_type_code"] = enr.get("committee_type")
            entry["committee_type_label"] = enr.get("committee_type_label")
            entry["organization_type"] = enr.get("organization_type")
            entry["affiliated_committee_name"] = enr.get("affiliated_committee_name")
            entry["treasurer_name"] = enr.get("treasurer_name")
            entry["city"] = enr.get("city")
            entry["state_short"] = enr.get("state")
            entry["filing_frequency"] = enr.get("filing_frequency")
            entry["first_file_date"] = enr.get("first_file_date")
            entry["last_file_date"] = enr.get("last_file_date")
            entry["is_terminated"] = bool(enr.get("is_terminated"))
            if enr.get("external_link"):
                entry["external_link"] = enr["external_link"]
                entry["external_link_label"] = enr.get("external_link_label") or "Read more"
            entry["enriched_at"] = enr.get("refreshed_at")
        recipients.append(entry)
    recipients.sort(key=lambda x: x["total_amount"], reverse=True)

    # Per-committee scale (lifetime per-cycle totals). Keyed by committee_id;
    # frontend looks up only on the committee detail page so the lookup cost
    # is per-render, not per-row.
    committee_scale = {cid: cycles for cid, cycles in scale_by_cid.items() if cycles}

    # Pipeline summary for the runs page
    completed_runs = [r for r in runs if r["completed_at"]]
    pipeline = {
        "n_runs": len(runs),
        "earliest_run": min((r["started_at"] for r in runs), default=None),
        "latest_run": max((r["started_at"] for r in runs), default=None),
        "total_records_fetched": sum(r["records_fetched"] or 0 for r in runs),
        "total_api_calls": sum(r["api_calls_made"] or 0 for r in runs),
        "n_dry_runs": sum(1 for r in runs if r["dry_run"]),
    }

    out = {
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "cpi": {
            "table": {str(y): v for y, v in CPI_TABLE.items()},
            "base_year": CPI_BASE_YEAR,
            "latest_month": CPI_LATEST_MONTH,
        },
        "league": league,
        "owners": owners_summary,
        "donations": donations,
        "recipients": recipients,
        "committee_scale": committee_scale,
        "runs": runs,
        "pipeline": pipeline,
    }

    OUT_PATH.write_text(json.dumps(out, indent=None, separators=(",", ":")))
    size_mb = OUT_PATH.stat().st_size / 1024 / 1024
    n_enriched = sum(1 for r in recipients if r.get("designation_label"))
    n_with_scale = len(committee_scale)
    print(f"wrote {OUT_PATH} ({size_mb:.2f} MB, {len(donations)} donations, "
          f"{len(owners_summary)} owners, {len(runs)} ingestion runs, "
          f"{n_enriched}/{len(recipients)} recipients enriched, "
          f"{n_with_scale} committee scale blocks)")

    write_provenance()


def write_provenance() -> None:
    """
    Parse catalog/PROVENANCE_LOG.md into mockup/provenance.json so the
    /#/changelog page can render the audit trail. Separate file (not baked
    into data.json) to keep the main payload lean — the changelog page
    lazy-fetches this only when visited.
    """
    if not PROVENANCE_SRC.exists():
        print(f"warn: {PROVENANCE_SRC} not found; skipping provenance.json", file=sys.stderr)
        return
    entries = parse_provenance_file(PROVENANCE_SRC)
    out = {
        "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "source": "catalog/PROVENANCE_LOG.md",
        "n_entries": len(entries),
        "entries": entries,
    }
    PROVENANCE_OUT.write_text(json.dumps(out, indent=None, separators=(",", ":")))
    size_kb = PROVENANCE_OUT.stat().st_size / 1024
    print(f"wrote {PROVENANCE_OUT} ({size_kb:.1f} KB, {len(entries)} entries)")


if __name__ == "__main__":
    main()

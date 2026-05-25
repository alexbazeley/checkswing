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
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "master.db"
OUT_PATH = REPO_ROOT / "mockup" / "data.json"


def filing_pdf_url(filing_id) -> str | None:
    """
    The FEC publishes each filing's full PDF at a deterministic URL keyed by
    the last 3 digits of the filing ID as a sharding prefix. e.g. filing
    1917827 lives at /pdf/827/1917827/1917827.pdf.
    """
    if not filing_id:
        return None
    fid = str(filing_id).strip()
    if not fid.isdigit() or len(fid) < 1:
        return None
    shard = fid[-3:].zfill(3)
    return f"https://docquery.fec.gov/pdf/{shard}/{fid}/{fid}.pdf"


def load_raw_payload_index(repo_root: Path, payload_paths: set[str]) -> dict[str, dict]:
    """
    Read each raw FEC payload once, index transactions by transaction_id, and
    return the fields the UI needs to deep-link into FEC. This is the only
    place we cross from the DB into the raw archive.
    """
    index: dict[str, dict] = {}
    for rel in payload_paths:
        if not rel:
            continue
        path = repo_root / rel
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        results = (data.get("response") or {}).get("results") or []
        for r in results:
            txn = r.get("transaction_id")
            if not txn:
                continue
            index[txn] = {
                "image_number": r.get("image_number"),
                "pdf_url": r.get("pdf_url"),
                "filing_form": r.get("filing_form"),
                "line_number": r.get("line_number"),
                "receipt_type_full": r.get("receipt_type_full"),
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
               tenure_start_date, tenure_end_date,
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
    # the FEC line item number, none of which live in the DB schema.
    payload_paths = {d["raw_payload_path"] for d in donations_raw if d["raw_payload_path"]}
    raw_index = load_raw_payload_index(REPO_ROOT, payload_paths)

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
                "filing_pdf_url": filing_pdf_url(d["filing_id"]),
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
        n_total = len(my_donations)
        n_confirmed = sum(1 for d in my_donations if d["status"] == "CONFIRMED")
        n_probable = sum(1 for d in my_donations if d["status"] == "PROBABLE")

        # Party split by dollars
        party_dollars = defaultdict(float)
        for d in my_donations:
            party_dollars[d["party"]] += d["amount"]

        # Sparkline: dollars per cycle 2000..2026 (even years)
        cycle_dollars = defaultdict(float)
        for d in my_donations:
            if d["cycle"]:
                cycle_dollars[int(d["cycle"])] += d["amount"]

        # Top 5 recipient committees by dollars
        committee_dollars = defaultdict(float)
        committee_count = defaultdict(int)
        committee_name = {}
        committee_party = {}
        for d in my_donations:
            cid = d["committee_id"] or "_unknown"
            committee_dollars[cid] += d["amount"]
            committee_count[cid] += 1
            committee_name[cid] = d["committee"]
            committee_party[cid] = d["party"]
        top_recipients = [
            {
                "committee_id": cid,
                "committee": committee_name[cid],
                "party": committee_party[cid],
                "amount": committee_dollars[cid],
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
            "total_amount": total_amount,
            "n_total": n_total,
            "n_confirmed": n_confirmed,
            "n_probable": n_probable,
            "party_dollars": dict(party_dollars),
            "cycle_dollars": dict(cycle_dollars),
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
    league_cycle_count = defaultdict(int)
    league_total = 0.0
    for d in donations:
        if d["cycle"]:
            league_cycle_party[int(d["cycle"])][d["party"]] += d["amount"]
            league_cycle_count[int(d["cycle"])] += 1
        league_total += d["amount"]

    league = {
        "total_amount": league_total,
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
                "count": league_cycle_count[c],
                "total": sum(league_cycle_party[c].values()),
            }
            for c in sorted(league_cycle_party)
        },
    }

    # Top league-wide recipients
    league_committee = defaultdict(lambda: {"amount": 0.0, "count": 0, "name": None, "party": None})
    for d in donations:
        cid = d["committee_id"] or "_unknown"
        league_committee[cid]["amount"] += d["amount"]
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
                "count": v["count"],
            }
            for cid, v in league_committee.items()
        ],
        key=lambda r: r["amount"],
        reverse=True,
    )[:25]

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
        "league": league,
        "owners": owners_summary,
        "donations": donations,
        "runs": runs,
        "pipeline": pipeline,
    }

    OUT_PATH.write_text(json.dumps(out, indent=None, separators=(",", ":")))
    size_mb = OUT_PATH.stat().st_size / 1024 / 1024
    print(f"wrote {OUT_PATH} ({size_mb:.2f} MB, {len(donations)} donations, "
          f"{len(owners_summary)} owners, {len(runs)} ingestion runs)")


if __name__ == "__main__":
    main()

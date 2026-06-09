"""Build mockup/state_data.json — the Phase-4 state campaign-finance dashboard payload.

Sibling to build_data.py (federal). Reads the SEPARATE data/state.db and emits a
small JSON the SPA lazy-loads only when the dedicated #/states section is opened, so
the federal data.json budget is untouched. The whole CONFIRMED+PROBABLE set is tiny
(~300 rows), so unlike the federal beneficiaries there is no chunking — it all fits
in one file.

Amounts are baked both nominal and CPI-adjusted to the federal base year (via
scripts.dollars.to_real) so the site's existing real-dollars toggle works here too.

Cloudflare runs this right after build_data.py:
  python mockup/build_data.py && python mockup/build_state_data.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from scripts.dollars import CPI_BASE_YEAR, CPI_LATEST_MONTH, CPI_TABLE, to_real  # noqa: E402
from scripts.paths import OWNERS_DIR, STATE_DB  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent / "state_data.json"
TOP_RECIPIENTS = 8
TOP_DONORS = 5


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_signals(raw) -> list[str]:
    """state_donations.signals_matched is a JSON array string; be forgiving."""
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(s) for s in v] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


# NY dataset is the Socrata "Campaign Finance … Contributions: Beginning 1999"
# resource (see scripts/fetch_ny.SODA_URL). The human-facing dataset page:
_NY_DATASET_URL = "https://data.ny.gov/d/4j2b-6a2j"
# ISBE bulk campaign-disclosure data files (the source of Receipts.txt / Committees.txt).
# The per-document PDF viewer keys on an encrypted token, not the raw FiledDocID, so we
# cite the official contribution-search page rather than fabricate a per-record link.
_IL_DATASET_URL = "https://www.elections.il.gov/CampaignDisclosure/ContributionSearchByAllContributions.aspx"
# PA-DOS "Full Campaign Finance Export" page — the source of the per-year bulk zips.
_PA_DATASET_URL = (
    "https://www.pa.gov/agencies/dos/resources/voting-and-elections-resources/campaign-finance-data"
)
# WA PDC Socrata dataset page (kv7h-kjye). source_filing_id is the report_number, which
# deep-links the actual filed report image at my.pdc.wa.gov.
_WA_DATASET_URL = "https://data.wa.gov/d/kv7h-kjye"
# CO TRACER "Campaign Finance Data" bulk-download page — the source of the per-year
# <YEAR>_ContributionData.csv.zip files. TRACER's per-record viewer keys on internal
# session ids (no stable per-row deep link), so we cite the bulk-data page.
_CO_DATASET_URL = "https://tracer.sos.colorado.gov/PublicSite/DataDownload.aspx"
# AZ "See The Money" — the public portal the JSON API backs. Per-record deep links
# require a primed session (no stable shareable URL), so we cite the portal home.
_AZ_DATASET_URL = "https://seethemoney.az.gov/"
# MN CFB "Campaign finance data downloads" page — the source of the all-entities
# contributions CSV. The export carries no per-contribution id (we key on a content
# hash), so there is no per-row deep link; cite the bulk-download page.
_MN_DATASET_URL = "https://cfb.mn.gov/reports-and-data/self-help/data-downloads/campaign-finance/"
# FL Division of Elections contributions query — the source of the tab-delimited
# export. No per-contribution id (content-hash keyed), so cite the query page.
_FL_DATASET_URL = "https://dos.elections.myflorida.com/campaign-finance/contributions/"


def _source_links(source: str, filing_id: str | None, tran_id: str | None) -> tuple[str | None, str | None]:
    """Best-effort (filing_url, dataset_url) to the OFFICIAL record, per portal.

    Returns (None, None) when no reliable public link can be built — we never
    fabricate a citation. Verified patterns only:
      * CAL-ACCESS — the actual filed PDF image, keyed on the filing id.
      * NYSBOE     — NY has no per-filing PDF; source_filing_id is the recipient
                     filer id, so the citable per-record handle is the Socrata
                     transaction. Link the exact dataset record + the dataset page.
      * ISBE       — the per-doc PDF viewer keys on an encrypted token (not the raw
                     FiledDocID); cite the official contribution-search page.
      * WA-PDC     — source_filing_id is the report_number → the filed report image
                     at my.pdc.wa.gov; also link the dataset page.
      * PA-DOS     — no verified per-record deep link in the bulk export; cite the
                     official pa.gov Full Campaign Finance Export page (the source
                     of the ingested file) as the dataset URL.
    """
    src = (source or "").upper()
    if src == "CAL-ACCESS" and filing_id:
        return (f"https://cal-access.sos.ca.gov/PDFGen/pdfgen.prg?filingid={filing_id}&amendid=0", None)
    if src == "NYSBOE" and tran_id:
        return (f"https://data.ny.gov/resource/4j2b-6a2j.json?trans_number={tran_id}", _NY_DATASET_URL)
    if src == "ISBE":
        return (None, _IL_DATASET_URL)
    if src == "WA-PDC" and filing_id:
        return (f"https://my.pdc.wa.gov/public/document?repno={filing_id}", _WA_DATASET_URL)
    if src == "PA-DOS":
        return (None, _PA_DATASET_URL)
    if src == "CO-TRACER":
        return (None, _CO_DATASET_URL)
    if src == "AZ-SOS":
        return (None, _AZ_DATASET_URL)
    if src == "MN-CFB":
        return (None, _MN_DATASET_URL)
    if src == "FL-DOE":
        return (None, _FL_DATASET_URL)
    return (None, None)


def _owner_meta() -> dict[str, dict]:
    """slug → {name, team} from owners/*.yaml (state.db has no entities table)."""
    meta: dict[str, dict] = {}
    for p in sorted(OWNERS_DIR.glob("*.yaml")):
        if p.name.startswith("_"):
            continue
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("slug"):
            meta[d["slug"]] = {"name": d.get("name") or d["slug"], "team": d.get("team") or ""}
    return meta


def main(db_path: Path = STATE_DB, out_path: Path = OUT_PATH) -> Path:
    meta = _owner_meta()
    if not db_path.exists():
        out_path.write_text(json.dumps({"generated_at": _utc_now_iso(), "empty": True}))
        print(f"No {db_path}; wrote empty {out_path}")
        return out_path

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM state_donations WHERE status IN ('CONFIRMED','PROBABLE') ORDER BY date DESC"
    ).fetchall()
    # Recipient identity (canonical name/type/party/office) from the filer lookup.
    # Keyed by (jurisdiction, source, filer_id), matching the state_filers PK.
    filers: dict[tuple, sqlite3.Row] = {}
    try:
        for fr in con.execute("SELECT * FROM state_filers").fetchall():
            filers[(fr["jurisdiction"], fr["source"], fr["filer_id"])] = fr
    except sqlite3.OperationalError:
        pass  # very old DB without the filers table — fall back to as-filed names
    con.close()

    donations = []
    owners: dict[str, dict] = {}
    owner_recip: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    owner_recip_ids: dict[str, set] = defaultdict(set)
    owner_cycles: dict[str, set] = defaultdict(set)
    juris: dict[str, dict] = {}
    juris_recip: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    # Recipient rollup, keyed by a stable recipient key (filer id when present,
    # else the as-filed name) within a jurisdiction+source.
    recips: dict[str, dict] = {}

    for r in rows:
        slug = r["entity_slug"]
        m = meta.get(slug, {"name": slug, "team": ""})
        try:
            cycle = int(r["election_cycle"]) if r["election_cycle"] else int((r["date"] or "2000")[:4])
        except (TypeError, ValueError):
            cycle = 2000
        amt = float(r["amount"] or 0)
        amt2026 = to_real(amt, cycle)
        jx = r["jurisdiction"] or "CA"
        source = r["source"]
        filer_id = r["recipient_filer_id"]
        filer = filers.get((jx, source, filer_id))
        # Prefer the canonical filer name; fall back to the as-filed recipient name.
        recip = (filer["name"] if filer else None) or r["recipient_name"] or "(recipient unidentified)"
        rtype = (filer["filer_type"] if filer else None) or r["recipient_type"]
        filing_url, dataset_url = _source_links(source, r["source_filing_id"], r["source_tran_id"])
        # Stable recipient routing key (filer id when present, else slugged name).
        rkey = f"{jx}:{source}:{filer_id}" if filer_id else f"{jx}:{source}:name:{recip}"

        donations.append({
            "id": r["state_txn_id"],
            "entity": slug,
            "owner_name": m["name"],
            "team": m["team"],
            "entity_kind": r["entity_kind"],
            "parent": r["parent_owner_slug"],
            "status": r["status"],
            "status_reason": r["status_reason"],
            "signals": _parse_signals(r["signals_matched"]),
            "donor_name": r["contributor_name_raw"],
            "employer": r["contributor_employer_raw"],
            "occupation": r["contributor_occupation_raw"],
            "city": r["contributor_city"],
            "state": r["contributor_state"],
            "zip": r["contributor_zip"],
            "amount": amt,
            "amount_2026": round(amt2026, 2),
            "date": r["date"],
            "cycle": cycle,
            "recipient": recip,
            "recipient_type": rtype,
            "recipient_filer_id": filer_id,
            "recipient_key": rkey,
            "jurisdiction": jx,
            "source": source,
            "source_filing_id": r["source_filing_id"],
            "source_tran_id": r["source_tran_id"],
            "discovery_source": r["discovery_source"],
            "report_type": r["report_type"],
            "raw_payload": r["raw_payload_path"],
            "ingested_at": r["ingested_at"],
            "filing_url": filing_url,
            "dataset_url": dataset_url,
        })

        o = owners.setdefault(slug, {
            "slug": slug, "name": m["name"], "team": m["team"],
            "n_confirmed": 0, "n_probable": 0,
            "total_amount": 0.0, "total_amount_2026": 0.0,
            "jurisdictions": set(),
        })
        o["n_confirmed" if r["status"] == "CONFIRMED" else "n_probable"] += 1
        o["total_amount"] += amt
        o["total_amount_2026"] += amt2026
        o["jurisdictions"].add(jx)
        owner_recip[slug][recip] += amt
        owner_recip_ids[slug].add(rkey)
        owner_cycles[slug].add(cycle)

        j = juris.setdefault(jx, {
            "code": jx, "source": source,
            "n_confirmed": 0, "n_probable": 0,
            "total_amount": 0.0, "total_amount_2026": 0.0, "owners": set(),
        })
        j["n_confirmed" if r["status"] == "CONFIRMED" else "n_probable"] += 1
        j["total_amount"] += amt
        j["total_amount_2026"] += amt2026
        j["owners"].add(slug)
        juris_recip[jx][recip] += amt

        rc = recips.setdefault(rkey, {
            "key": rkey, "filer_id": filer_id, "jurisdiction": jx, "source": source,
            "name": recip, "recipient_type": rtype,
            "party": (filer["party"] if filer else None) or r["recipient_party"],
            "office": (filer["office"] if filer else None) or r["recipient_office"],
            "n_confirmed": 0, "n_probable": 0,
            "total_amount": 0.0, "total_amount_2026": 0.0,
            "owners": set(), "_donor_amt": defaultdict(float),
            "first_date": r["date"], "last_date": r["date"],
        })
        rc["n_confirmed" if r["status"] == "CONFIRMED" else "n_probable"] += 1
        rc["total_amount"] += amt
        rc["total_amount_2026"] += amt2026
        rc["owners"].add(slug)
        rc["_donor_amt"][slug] += amt
        if r["date"]:
            if not rc["first_date"] or r["date"] < rc["first_date"]:
                rc["first_date"] = r["date"]
            if not rc["last_date"] or r["date"] > rc["last_date"]:
                rc["last_date"] = r["date"]

    def _top(d: dict[str, float]) -> list[dict]:
        return [
            {"recipient": k, "amount": round(v, 2)}
            for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:TOP_RECIPIENTS]
        ]

    owners_out = {}
    for slug, o in owners.items():
        o["jurisdictions"] = sorted(o["jurisdictions"])
        o["total_amount"] = round(o["total_amount"], 2)
        o["total_amount_2026"] = round(o["total_amount_2026"], 2)
        o["top_recipients"] = _top(owner_recip[slug])
        o["n_recipients"] = len(owner_recip_ids[slug])
        o["cycles"] = sorted(owner_cycles[slug])
        owners_out[slug] = o

    juris_out = []
    for jx, j in sorted(juris.items()):
        juris_out.append({
            "code": j["code"], "source": j["source"],
            "n_confirmed": j["n_confirmed"], "n_probable": j["n_probable"],
            "n_owners": len(j["owners"]),
            "total_amount": round(j["total_amount"], 2),
            "total_amount_2026": round(j["total_amount_2026"], 2),
            "top_recipients": _top(juris_recip[jx]),
        })

    recips_out = []
    for rc in sorted(recips.values(), key=lambda x: -x["total_amount"]):
        top_donors = [
            {"slug": s, "name": meta.get(s, {"name": s})["name"], "amount": round(a, 2)}
            for s, a in sorted(rc["_donor_amt"].items(), key=lambda kv: -kv[1])[:TOP_DONORS]
        ]
        recips_out.append({
            "key": rc["key"], "filer_id": rc["filer_id"],
            "jurisdiction": rc["jurisdiction"], "source": rc["source"],
            "name": rc["name"], "recipient_type": rc["recipient_type"],
            "party": rc["party"], "office": rc["office"],
            "n_confirmed": rc["n_confirmed"], "n_probable": rc["n_probable"],
            "n_donations": rc["n_confirmed"] + rc["n_probable"],
            "n_owners": len(rc["owners"]),
            "total_amount": round(rc["total_amount"], 2),
            "total_amount_2026": round(rc["total_amount_2026"], 2),
            "top_donors": top_donors,
            "first_date": rc["first_date"], "last_date": rc["last_date"],
        })

    out = {
        "generated_at": _utc_now_iso(),
        "cpi": {"table": {str(y): v for y, v in CPI_TABLE.items()},
                "base_year": CPI_BASE_YEAR, "latest_month": CPI_LATEST_MONTH},
        "jurisdictions": juris_out,
        "owners": owners_out,
        "recipients": recips_out,
        "donations": donations,
        "n_donations": len(donations),
    }
    out_path.write_text(json.dumps(out, separators=(",", ":")))
    kb = out_path.stat().st_size // 1024
    print(f"Wrote {out_path} — {len(donations)} donations, {len(owners_out)} owners, "
          f"{len(recips_out)} recipients, {len(juris_out)} jurisdiction(s), {kb} KB")
    return out_path


if __name__ == "__main__":
    main()

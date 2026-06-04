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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    con.close()

    donations = []
    owners: dict[str, dict] = {}
    owner_recip: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    juris: dict[str, dict] = {}
    juris_recip: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

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
        recip = r["recipient_name"] or "(recipient unidentified)"

        donations.append({
            "entity": slug,
            "owner_name": m["name"],
            "team": m["team"],
            "status": r["status"],
            "donor_name": r["contributor_name_raw"],
            "employer": r["contributor_employer_raw"],
            "occupation": r["contributor_occupation_raw"],
            "city": r["contributor_city"],
            "state": r["contributor_state"],
            "amount": amt,
            "amount_2026": round(amt2026, 2),
            "date": r["date"],
            "cycle": cycle,
            "recipient": recip,
            "recipient_type": r["recipient_type"],
            "jurisdiction": jx,
            "source": r["source"],
            "source_filing_id": r["source_filing_id"],
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

        j = juris.setdefault(jx, {
            "code": jx, "source": r["source"],
            "n_confirmed": 0, "n_probable": 0,
            "total_amount": 0.0, "total_amount_2026": 0.0, "owners": set(),
        })
        j["n_confirmed" if r["status"] == "CONFIRMED" else "n_probable"] += 1
        j["total_amount"] += amt
        j["total_amount_2026"] += amt2026
        j["owners"].add(slug)
        juris_recip[jx][recip] += amt

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

    out = {
        "generated_at": _utc_now_iso(),
        "cpi": {"table": {str(y): v for y, v in CPI_TABLE.items()},
                "base_year": CPI_BASE_YEAR, "latest_month": CPI_LATEST_MONTH},
        "jurisdictions": juris_out,
        "owners": owners_out,
        "donations": donations,
        "n_donations": len(donations),
    }
    out_path.write_text(json.dumps(out, separators=(",", ":")))
    kb = out_path.stat().st_size // 1024
    print(f"Wrote {out_path} — {len(donations)} donations, {len(owners_out)} owners, "
          f"{len(juris_out)} jurisdiction(s), {kb} KB")
    return out_path


if __name__ == "__main__":
    main()

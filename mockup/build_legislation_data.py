"""Build mockup/legislation.json — the Phase-3 legislation-index dashboard payload.

Sibling to build_data.py (federal) and build_state_data.py (state). Reads the
SEPARATE data/legislation.db plus the curated legislation/issues.yaml taxonomy and
the reproducible join outputs in reports/data/*.json, and emits a small JSON the SPA
lazy-loads only when the dedicated #/legislation section is opened — so the federal
data.json budget is untouched. The whole set is tiny (a couple dozen bills), so there
is no chunking.

Neutrality (GOVERNANCE.md §6 / Phase 3): this payload carries only neutral, sourced
facts — a bill's identity, sponsor, latest action, enacted flag, roll-call results,
and a per-owner *arithmetic* rollup of donations that reach the bill's sponsors /
committee-of-referral members. It carries NO editorial framing, motive, or any claim
that a donation influenced a vote. Interpretation lives in reports/*.md, which the
dashboard links out to (clearly labelled), never inlines. The pass-through
denominator caveat (§3.11) is baked in so the join is never read as complete.

Cloudflare runs this right after build_data.py / build_state_data.py:
  python mockup/build_data.py  (which invokes the state + legislation builders)
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
from scripts.paths import (  # noqa: E402
    LEGISLATION_DB,
    LEGISLATION_DIR,
    REPORTS_DATA_DIR,
    REPORTS_DIR,
)

OUT_PATH = Path(__file__).resolve().parent / "legislation.json"

# The single sentence that keeps the donations × legislation join honest: it can
# only trace direct-to-candidate giving, a small slice of the archive's dollars
# (IMPROVEMENT_PLAN_2026-07 §3.11). Party committees and leadership PACs — the bulk
# of the money — are excluded by construction until the §5.6 pass-through join lands.
DENOMINATOR_NOTE = (
    "This join covers direct-to-candidate and to-sponsor/committee-member giving only. "
    "Party committees and leadership PACs — the bulk of the money — are excluded by "
    "construction, so these rollups are a floor, not a total."
)

# Which reports/data/*.json is the headline join for each published brief, and the
# brief's title + source path. The rollup dedupes by transaction so a single donation
# joined to several legislators is counted once (never multiplied by match count).
BRIEFS = [
    {
        "slug": "save-americas-pastime-act",
        "title": "The “Save America’s Pastime Act” — minor-league pay",
        "issue_area": "minor_league_pay",
        "primary_join": "save-americas-pastime-act.json",
        "report_md": "2026-05-31_save-americas-pastime-act.md",
    },
    {
        "slug": "no-tax-subsidies-for-stadiums",
        "title": "The stadium subsidy that won’t die — committees of referral",
        "issue_area": "stadium_financing",
        "primary_join": "no-tax-subsidies-for-stadiums-committees.json",
        "report_md": "2026-06-08_no-tax-subsidies-for-stadiums.md",
    },
]

GITHUB_REPORTS_URL = "https://github.com/alexbazeley/checkswing/blob/main/reports"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_issues() -> list[dict]:
    """The curated issue taxonomy, in the file's declared order."""
    doc = yaml.safe_load((LEGISLATION_DIR / "issues.yaml").read_text()) or {}
    issues = doc.get("issues", {}) or {}
    out = []
    for key, meta in issues.items():
        out.append(
            {
                "key": key,
                "label": (meta or {}).get("label", key),
                "description": " ".join(((meta or {}).get("description", "") or "").split()),
            }
        )
    return out


def _load_bills(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    bills = []
    for b in cur.execute(
        """
        SELECT bill_id, congress, bill_type, number, title, mlb_issue_area,
               introduced_date, latest_action, latest_action_date, enacted,
               relevance_basis, congress_dot_gov_url, relevance_source_url
        FROM bills
        """
    ).fetchall():
        bid = b["bill_id"]
        sponsor = cur.execute(
            """
            SELECT l.full_name, l.current_party, l.current_state
            FROM bill_sponsors s JOIN legislators l ON l.bioguide_id = s.bioguide_id
            WHERE s.bill_id = ? AND s.role = 'sponsor' LIMIT 1
            """,
            (bid,),
        ).fetchone()
        n_cosponsors = cur.execute(
            "SELECT COUNT(*) FROM bill_sponsors WHERE bill_id = ? AND role = 'cosponsor'",
            (bid,),
        ).fetchone()[0]
        votes = [
            {
                "chamber": v["chamber"],
                "vote_date": v["vote_date"],
                "question": v["question"],
                "result": v["result"],
                "source_url": v["source_url"],
            }
            for v in cur.execute(
                "SELECT chamber, vote_date, question, result, source_url "
                "FROM votes WHERE bill_id = ? ORDER BY vote_date",
                (bid,),
            ).fetchall()
        ]
        bills.append(
            {
                "bill_id": bid,
                "congress": b["congress"],
                "bill_type": b["bill_type"],
                "number": b["number"],
                "title": b["title"],
                "issue_area": b["mlb_issue_area"],
                "sponsor_name": sponsor["full_name"] if sponsor else None,
                "sponsor_party": sponsor["current_party"] if sponsor else None,
                "sponsor_state": sponsor["current_state"] if sponsor else None,
                "n_cosponsors": n_cosponsors,
                "introduced_date": b["introduced_date"],
                "latest_action": b["latest_action"],
                "latest_action_date": b["latest_action_date"],
                "enacted": bool(b["enacted"]),
                "url": b["congress_dot_gov_url"] or b["relevance_source_url"],
                "votes": votes,
            }
        )
    # Stable order: issue area (taxonomy order handled client-side), then congress
    # descending (newest fight first), then chamber/number.
    bills.sort(key=lambda x: (-(x["congress"] or 0), x["bill_id"]))
    return bills


def _brief_rollup(primary_join: str) -> dict | None:
    """Per-owner arithmetic rollup of a brief's headline join, deduped by transaction.

    A single donation joined to several sponsors / committee members is counted once
    (its amount is not multiplied by the number of matched legislators).
    """
    path = REPORTS_DATA_DIR / primary_join
    if not path.exists():
        return None
    doc = json.loads(path.read_text())
    rows = doc.get("rows", []) or []
    meta = doc.get("_meta", {}) or {}
    per_owner: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()  # (owner_slug, transaction_id)
    for r in rows:
        slug = r.get("owner_slug")
        txn = r.get("transaction_id")
        if not slug or not txn:
            continue
        o = per_owner.setdefault(
            slug,
            {
                "owner_slug": slug,
                "owner_name": r.get("owner_name"),
                "owner_team": r.get("owner_team"),
                "total_amount": 0.0,
                "n_donations": 0,
            },
        )
        if (slug, txn) in seen:
            continue
        seen.add((slug, txn))
        o["total_amount"] += float(r.get("amount") or 0)
        o["n_donations"] += 1
    owners = sorted(per_owner.values(), key=lambda x: x["total_amount"], reverse=True)
    return {
        "join": meta.get("join"),
        "bill_ids": meta.get("bill_ids", []),
        "generated_at": meta.get("generated_at"),
        "n_join_rows": len(rows),
        "owners": owners,
    }


def _load_briefs() -> list[dict]:
    out = []
    for spec in BRIEFS:
        rollup = _brief_rollup(spec["primary_join"])
        out.append(
            {
                "slug": spec["slug"],
                "title": spec["title"],
                "issue_area": spec["issue_area"],
                "report_url": f"{GITHUB_REPORTS_URL}/{spec['report_md']}",
                "join": (rollup or {}).get("join"),
                "bill_ids": (rollup or {}).get("bill_ids", []),
                "generated_at": (rollup or {}).get("generated_at"),
                "n_join_rows": (rollup or {}).get("n_join_rows", 0),
                "owners": (rollup or {}).get("owners", []),
            }
        )
    return out


def main(db_path: Path = LEGISLATION_DB, out_path: Path = OUT_PATH) -> Path:
    if not db_path.exists():
        out_path.write_text(json.dumps({"generated_at": _utc_now_iso(), "empty": True}))
        return out_path
    conn = sqlite3.connect(db_path)
    try:
        bills = _load_bills(conn)
    finally:
        conn.close()
    out = {
        "generated_at": _utc_now_iso(),
        "denominator_note": DENOMINATOR_NOTE,
        "neutrality_note": (
            "Neutral sourced facts only (GOVERNANCE.md §6). Donation rollups are "
            "arithmetic — who gave what to a bill’s sponsors / committee members, "
            "deduped by transaction — never a causal claim. Interpretation lives in the "
            "linked briefs."
        ),
        "issues": _load_issues(),
        "bills": bills,
        "briefs": _load_briefs(),
    }
    out_path.write_text(json.dumps(out, separators=(",", ":")))
    return out_path


if __name__ == "__main__":
    p = main()
    print(f"Wrote {p}")

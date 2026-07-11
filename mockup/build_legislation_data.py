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
from scripts import policy_join  # noqa: E402
from scripts.paths import (  # noqa: E402
    LEGISLATION_DB,
    LEGISLATION_DIR,
    MASTER_DB,
    REPORTS_DATA_DIR,
    REPORTS_DIR,
)

OUT_PATH = Path(__file__).resolve().parent / "legislation.json"

# The sentence that keeps the donations × legislation join honest (§3.11, §5.6).
# The rollup now spans two tiers: `direct` (the donation's own recipient_candidate_id)
# and `indirect-authorized` (money to a legislator's OWN campaign committee, resolved
# through committees.candidate_ids and gated on FEC designation P/A + a single
# candidate — see DESIGN_passthrough_join_2026-07.md). Everything else — leadership
# PACs, joint-fundraising committees, party and super PACs — is excluded from candidate
# attribution BY CONSTRUCTION, because a donation to a PAC is not a donation to a
# candidate's campaign.
DENOMINATOR_NOTE = (
    "This join covers two tiers: direct-to-candidate giving, and giving to a "
    "legislator’s own campaign committee (indirect-authorized). Money to leadership "
    "PACs, joint-fundraising committees, and party or super PACs is excluded from "
    "candidate attribution by construction — so these rollups remain a floor, not a total."
)

# Each published brief's headline join, computed LIVE from policy_join with the
# indirect-authorized tier included (§5.6). The rollup dedupes by transaction so a
# single donation joined to several legislators is counted once, and splits the total
# into the direct vs indirect tiers.
BRIEFS = [
    {
        "slug": "save-americas-pastime-act",
        "title": "The “Save America’s Pastime Act” — minor-league pay",
        "issue_area": "minor_league_pay",
        "join_kind": "votes",
        "bill_ids": ["115-hr-1625"],
        "report_md": "2026-05-31_save-americas-pastime-act.md",
    },
    {
        "slug": "no-tax-subsidies-for-stadiums",
        "title": "The stadium subsidy that won’t die — committees of referral",
        "issue_area": "stadium_financing",
        "join_kind": "committee",
        "bill_ids": ["119-s-1192", "119-hr-2434"],
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


def _brief_rollup(spec: dict) -> dict | None:
    """Per-owner rollup of a brief's headline join, computed LIVE from policy_join
    with the indirect-authorized tier included (§5.6), deduped by transaction.

    A single donation joined to several sponsors / committee members / votes is
    counted once per owner (its amount is not multiplied by the match count), and the
    total is split into the `direct` vs `indirect-authorized` tiers. Returns None if
    master.db is absent (e.g. a legislation-only build) — the brief is then omitted.
    """
    if not MASTER_DB.exists():
        return None
    join_kind = spec["join_kind"]
    fn = (
        policy_join.vote_donation_rows if join_kind == "votes"
        else policy_join.committee_donation_rows if join_kind == "committee"
        else policy_join.sponsor_donation_rows
    )
    try:
        rows = fn(bill_ids=spec["bill_ids"], include_indirect=True)
    except Exception:  # noqa: BLE001 — never let a join failure break the whole build
        return None
    per_owner: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()  # (owner_slug, transaction_id) → count each gift once
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
                "direct_amount": 0.0,
                "indirect_amount": 0.0,
                "n_donations": 0,
            },
        )
        if (slug, txn) in seen:
            continue
        seen.add((slug, txn))
        amt = float(r.get("amount") or 0)
        o["total_amount"] += amt
        if r.get("join_tier") == "indirect-authorized":
            o["indirect_amount"] += amt
        else:
            o["direct_amount"] += amt
        o["n_donations"] += 1
    owners = sorted(per_owner.values(), key=lambda x: x["total_amount"], reverse=True)
    return {
        "join": f"donations_to_{join_kind}",
        "bill_ids": spec["bill_ids"],
        "n_join_rows": len(rows),
        "owners": owners,
    }


def _load_briefs() -> list[dict]:
    out = []
    for spec in BRIEFS:
        rollup = _brief_rollup(spec)
        out.append(
            {
                "slug": spec["slug"],
                "title": spec["title"],
                "issue_area": spec["issue_area"],
                "report_url": f"{GITHUB_REPORTS_URL}/{spec['report_md']}",
                "join": (rollup or {}).get("join"),
                "bill_ids": (rollup or {}).get("bill_ids", []),
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

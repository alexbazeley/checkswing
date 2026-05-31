"""Fetch + parse roll-call votes (Phase 3, Tier-1 source of record).

House votes come from the Clerk's Electronic Voting System XML
(clerk.house.gov/evs/<year>/roll<NNN>.xml); members are keyed by Bioguide id
directly. Senate votes come from the Senate LIS XML
(senate.gov/.../vote<C><S>/vote_<C>_<S>_<NNNNN>.xml); members are keyed by LIS id,
which is mapped to Bioguide via the crosswalk (legislators.lis_id) at ingest time.

Parsing is separated from fetching (pure functions over XML text) so it is
unit-testable without a network call. Raw payloads are persisted before parsing
(GOVERNANCE.md §1.4). Uses stdlib xml.etree — no new dependency.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

from .paths import legislation_raw_dir, relpath


HOUSE_URL = "https://clerk.house.gov/evs/{year}/roll{roll:03d}.xml"
SENATE_URL = (
    "https://www.senate.gov/legislative/LIS/roll_call_votes/"
    "vote{congress}{session}/vote_{congress}_{session}_{roll:05d}.xml"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")


def _persist_raw(label: str, url: str, text: str) -> Path:
    raw_path = legislation_raw_dir() / f"{_utc_now_filename()}__{label}.xml"
    raw_path.write_text(text, encoding="utf-8")
    raw_path.with_suffix(".meta.json").write_text(
        f'{{"url": "{url}", "fetched_at": "{_utc_now_iso()}"}}', encoding="utf-8"
    )
    return raw_path


def _text(el, tag) -> str | None:
    found = el.find(tag)
    return found.text.strip() if found is not None and found.text else None


# ── House (Clerk EVS) ───────────────────────────────────────────────────────

def parse_house_vote(xml_text: str) -> tuple[dict, list[dict]]:
    """Parse a Clerk EVS roll-call XML. Returns (meta, positions).

    positions: [{bioguide_id, position}]. meta: chamber/congress/session/roll/
    vote_date/question/result/legis_num.
    """
    root = ET.fromstring(xml_text)
    md = root.find("vote-metadata")
    md = md if md is not None else root

    action_date = _text(md, "action-date")  # e.g. "22-Mar-2018"
    vote_date = None
    if action_date:
        try:
            vote_date = datetime.strptime(action_date, "%d-%b-%Y").strftime("%Y-%m-%d")
        except ValueError:
            vote_date = action_date

    session_raw = _text(md, "session")  # e.g. "2nd"
    session = None
    if session_raw:
        digits = "".join(c for c in session_raw if c.isdigit())
        session = int(digits) if digits else None

    roll_raw = _text(md, "rollcall-num")
    meta = {
        "chamber": "house",
        "congress": int(_text(md, "congress")) if _text(md, "congress") else None,
        "session": session,
        "roll_number": int(roll_raw) if roll_raw and roll_raw.isdigit() else None,
        "vote_date": vote_date,
        "question": _text(md, "vote-question"),
        "description": _text(md, "vote-desc"),
        "result": _text(md, "vote-result"),
        "legis_num": _text(md, "legis-num"),
    }

    positions: list[dict] = []
    seen: set[str] = set()
    data = root.find("vote-data")
    for rv in (data.findall("recorded-vote") if data is not None else []):
        leg = rv.find("legislator")
        bioguide = leg.get("name-id") if leg is not None else None
        pos = _text(rv, "vote")
        if bioguide and pos and bioguide not in seen:
            seen.add(bioguide)
            positions.append({"bioguide_id": bioguide, "position": pos})
    return meta, positions


def fetch_house_vote(year: int, roll: int, session: requests.Session | None = None) -> tuple[str, Path]:
    sess = session or requests.Session()
    url = HOUSE_URL.format(year=year, roll=roll)
    resp = sess.get(url, timeout=60)
    resp.raise_for_status()
    text = resp.content.decode("utf-8")
    raw_path = _persist_raw(f"house-vote-{year}-{roll:03d}", url, text)
    return text, raw_path


# ── Senate (LIS) ────────────────────────────────────────────────────────────

def parse_senate_vote(xml_text: str) -> tuple[dict, list[dict]]:
    """Parse a Senate LIS roll-call XML. Returns (meta, positions).

    positions: [{lis_member_id, position}] — LIS ids, mapped to bioguide at
    ingest time via legislators.lis_id.
    """
    root = ET.fromstring(xml_text)

    raw_date = _text(root, "vote_date")  # e.g. "March 23, 2018, 12:34 AM"
    vote_date = None
    if raw_date:
        # The date is the first two comma-fields; drop any trailing time.
        parts = [p.strip() for p in raw_date.split(",")]
        candidate = ", ".join(parts[:2]) if len(parts) >= 2 else raw_date
        try:
            vote_date = datetime.strptime(candidate, "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            vote_date = raw_date

    num_raw = _text(root, "vote_number")
    meta = {
        "chamber": "senate",
        "congress": int(_text(root, "congress")) if _text(root, "congress") else None,
        "session": int(_text(root, "session")) if _text(root, "session") else None,
        "roll_number": int(num_raw) if num_raw and num_raw.isdigit() else None,
        "vote_date": vote_date,
        "question": _text(root, "question"),
        "description": _text(root, "vote_title"),
        "result": _text(root, "vote_result"),
        "legis_num": None,
    }
    doc = root.find("document")
    if doc is not None:
        meta["legis_num"] = _text(doc, "document_name")

    positions: list[dict] = []
    seen: set[str] = set()
    members = root.find("members")
    for m in (members.findall("member") if members is not None else []):
        lis = _text(m, "lis_member_id")
        pos = _text(m, "vote_cast")
        if lis and pos and lis not in seen:
            seen.add(lis)
            positions.append({"lis_member_id": lis, "position": pos})
    return meta, positions


def fetch_senate_vote(
    congress: int, session_no: int, roll: int, session: requests.Session | None = None
) -> tuple[str, Path]:
    sess = session or requests.Session()
    url = SENATE_URL.format(congress=congress, session=session_no, roll=roll)
    resp = sess.get(url, timeout=60)
    resp.raise_for_status()
    text = resp.content.decode("utf-8")
    raw_path = _persist_raw(f"senate-vote-{congress}-{session_no}-{roll:05d}", url, text)
    return text, raw_path

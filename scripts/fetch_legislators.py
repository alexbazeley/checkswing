"""Fetch + parse the unitedstates/congress-legislators crosswalk.

This is the spine of Phase 3: it maps an FEC candidate_id (how donations name a
recipient legislator) to a Bioguide id (how roll-call votes name a legislator).
Sourced from the public-domain `unitedstates/congress-legislators` project —
Tier-2 entity identification (SOURCES.md Phase-3 addendum): it tells us *who* a
candidate id is, never *what* they voted or *whether* a donation occurred.

Network and parsing are deliberately separated: `parse_legislators()` is a pure
function over YAML text so it is unit-testable without a network call. Raw
upstream payloads are persisted before parsing (GOVERNANCE.md §1.4).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from .paths import legislation_raw_dir, relpath


# theunitedstates.io serves the canonical published copies; the raw.githubusercontent
# mirror is the same content. We pin to the project's stable published URLs.
CURRENT_URL = "https://unitedstates.github.io/congress-legislators/legislators-current.yaml"
HISTORICAL_URL = "https://unitedstates.github.io/congress-legislators/legislators-historical.yaml"

SOURCE_LABEL = "unitedstates/congress-legislators"

# Map the crosswalk's term `type` to our chamber vocabulary. Presidents/VPs
# (prez/viceprez, historical only) have no congressional chamber and are dropped
# from legislator_terms — they never receive FEC *candidate* donations joinable
# to a congressional roll call.
_CHAMBER = {"rep": "house", "sen": "senate"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")


def fetch_raw(url: str, label: str, session: requests.Session | None = None) -> str:
    """GET a crosswalk YAML and persist it raw before parsing (§1.4).

    Returns the response text. `label` (e.g. "current"/"historical") tags the
    persisted filename.
    """
    sess = session or requests.Session()
    resp = sess.get(url, timeout=120)
    resp.raise_for_status()
    # The crosswalk is UTF-8 (accented legislator names). The server sends no
    # charset, so requests would default to ISO-8859-1 and split multibyte
    # characters — decode the bytes as UTF-8 explicitly.
    text = resp.content.decode("utf-8")
    raw_path = legislation_raw_dir() / f"{_utc_now_filename()}__legislators-{label}.yaml"
    raw_path.write_text(text, encoding="utf-8")
    # A tiny sidecar envelope records provenance for the payload.
    meta_path = raw_path.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps(
            {"url": url, "label": label, "source": SOURCE_LABEL, "fetched_at": _utc_now_iso()},
            indent=2,
        ),
        encoding="utf-8",
    )
    return text


def _congress_from_start(start: str | None) -> int | None:
    """Derive the Congress number from a term's ISO start date.

    The Nth Congress convenes in January of the odd year 1789 + 2*(N-1). A term
    starting in an even year (a mid-term appointment) belongs to the Congress
    that convened the prior odd year.
    """
    if not start or len(str(start)) < 4:
        return None
    try:
        year = int(str(start)[:4])
    except ValueError:
        return None
    base_odd = year if year % 2 == 1 else year - 1
    return (base_odd - 1789) // 2 + 1


def parse_legislators(
    yaml_text: str,
    *,
    source: str = SOURCE_LABEL,
    raw_payload_path: str | None = None,
    only_with_fec: bool = True,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Parse a congress-legislators YAML document into row dicts.

    Returns (legislators, fec_ids, terms).

    `only_with_fec=True` keeps only legislators carrying at least one FEC
    candidate id — the joinable universe for this archive (a legislator with no
    FEC id can never match a donation's recipient_candidate_id). This keeps
    legislation.db lean and the table scoped to its purpose.
    """
    docs = yaml.safe_load(yaml_text) or []
    now = _utc_now_iso()

    legislators: list[dict] = []
    fec_ids: list[dict] = []
    terms: list[dict] = []
    seen_fec: set[tuple[str, str]] = set()

    for entry in docs:
        if not isinstance(entry, dict):
            continue
        ids = entry.get("id") or {}
        bioguide = ids.get("bioguide")
        if not bioguide:
            continue
        fec_list = [str(f) for f in (ids.get("fec") or []) if f]
        if only_with_fec and not fec_list:
            continue

        name = entry.get("name") or {}
        first = name.get("first")
        last = name.get("last")
        full = name.get("official_full") or " ".join(p for p in (first, last) if p)

        entry_terms = [t for t in (entry.get("terms") or []) if isinstance(t, dict)]
        # Most recent term anchors current party/state.
        last_term = entry_terms[-1] if entry_terms else {}

        legislators.append(
            {
                "bioguide_id": bioguide,
                "icpsr_id": str(ids["icpsr"]) if ids.get("icpsr") is not None else None,
                "govtrack_id": str(ids["govtrack"]) if ids.get("govtrack") is not None else None,
                "opensecrets_id": ids.get("opensecrets"),
                "full_name": full or bioguide,
                "first_name": first,
                "last_name": last,
                "current_party": last_term.get("party"),
                "current_state": last_term.get("state"),
                "source": source,
                "raw_payload_path": raw_payload_path,
                "fetched_at": now,
                "refreshed_at": now,
            }
        )

        for fec_id in fec_list:
            key = (fec_id, bioguide)
            if key in seen_fec:
                continue
            seen_fec.add(key)
            fec_ids.append({"fec_candidate_id": fec_id, "bioguide_id": bioguide})

        for t in entry_terms:
            chamber = _CHAMBER.get(t.get("type"))
            if chamber is None:
                continue  # prez/viceprez — no congressional chamber
            start = t.get("start")
            terms.append(
                {
                    "bioguide_id": bioguide,
                    "congress": _congress_from_start(start),
                    "chamber": chamber,
                    "state": t.get("state"),
                    "district": str(t["district"]) if t.get("district") is not None else None,
                    "party": t.get("party"),
                    "start_date": str(start) if start else None,
                    "end_date": str(t["end"]) if t.get("end") else None,
                }
            )

    return legislators, fec_ids, terms


def fetch_and_parse(
    *,
    include_historical: bool = True,
    only_with_fec: bool = True,
    session: requests.Session | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Fetch current (+ historical) crosswalks and parse into merged row lists.

    Later sources do not overwrite earlier ones at the legislator level — a
    bioguide appearing in both `current` and `historical` keeps its `current`
    identity (current is fetched first). FEC ids and terms are unioned.
    """
    sources = [("current", CURRENT_URL)]
    if include_historical:
        sources.append(("historical", HISTORICAL_URL))

    all_legis: dict[str, dict] = {}
    all_fec: dict[tuple[str, str], dict] = {}
    all_terms: list[dict] = []
    seen_terms: set[tuple] = set()

    for label, url in sources:
        text = fetch_raw(url, label, session=session)
        raw_rel = None  # the most recent persisted file for this label
        # Find the file we just wrote (newest matching this label).
        candidates = sorted(legislation_raw_dir().glob(f"*__legislators-{label}.yaml"))
        if candidates:
            raw_rel = relpath(candidates[-1])
        legis, fec, terms = parse_legislators(
            text, raw_payload_path=raw_rel, only_with_fec=only_with_fec
        )
        for row in legis:
            all_legis.setdefault(row["bioguide_id"], row)
        for row in fec:
            all_fec.setdefault((row["fec_candidate_id"], row["bioguide_id"]), row)
        for row in terms:
            tkey = (row["bioguide_id"], row["chamber"], row["start_date"])
            if tkey not in seen_terms:
                seen_terms.add(tkey)
                all_terms.append(row)

    return list(all_legis.values()), list(all_fec.values()), all_terms

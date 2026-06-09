"""Fetch + parse current CONGRESSIONAL committees and their membership.

(Distinct from scripts/fetch_committees.py, which fetches FEC *recipient*
committees. This module is about House/Senate committees and who sits on them.)

Sourced from the public-domain `unitedstates/congress-legislators` project — the
same Tier-2 entity source as the legislator crosswalk (SOURCES.md Phase-3
addendum). It tells us *who currently sits on which committee*, never what they
voted or whether a donation occurred.

IMPORTANT — current snapshot only. The upstream files carry no membership
history, so this is the present (current-congress) roster. The committee→donation
join in policy_join.py guards on `committees.congress` so a present-day member is
never asserted to have handled a historical bill.

Network and parsing are separated: the `parse_*` functions are pure over YAML
text (unit-testable without a network call); raw payloads are persisted before
parsing (GOVERNANCE.md §1.4).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import requests
import yaml

from .paths import legislation_raw_dir, relpath


COMMITTEES_URL = "https://unitedstates.github.io/congress-legislators/committees-current.yaml"
MEMBERSHIP_URL = "https://unitedstates.github.io/congress-legislators/committee-membership-current.yaml"

SOURCE_LABEL = "unitedstates/congress-legislators"

_CHAMBER = {"house": "house", "senate": "senate", "joint": "joint"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")


def fetch_raw(url: str, label: str, session: requests.Session | None = None) -> tuple[str, str | None]:
    """GET a committees YAML and persist it raw before parsing (§1.4).

    Returns (text, relative_raw_path). `label` tags the persisted filename.
    """
    sess = session or requests.Session()
    resp = sess.get(url, timeout=120)
    resp.raise_for_status()
    # No charset header upstream; decode bytes as UTF-8 (accented names) rather
    # than letting requests default to ISO-8859-1 and split multibyte chars.
    text = resp.content.decode("utf-8")
    raw_path = legislation_raw_dir() / f"{_utc_now_filename()}__congress-committees-{label}.yaml"
    raw_path.write_text(text, encoding="utf-8")
    meta_path = raw_path.with_suffix(".meta.json")
    meta_path.write_text(
        json.dumps(
            {"url": url, "label": label, "source": SOURCE_LABEL, "fetched_at": _utc_now_iso()},
            indent=2,
        ),
        encoding="utf-8",
    )
    return text, relpath(raw_path)


def parse_committees(yaml_text: str) -> dict[str, dict]:
    """Parse committees-current.yaml → {thomas_id: {chamber, name}}.

    Only full (parent) committees carry a top-level `thomas_id`; their
    subcommittees are nested and are not indexed here, because bills are referred
    to full committees.
    """
    docs = yaml.safe_load(yaml_text) or []
    out: dict[str, dict] = {}
    for c in docs:
        if not isinstance(c, dict):
            continue
        tid = c.get("thomas_id")
        if not tid:
            continue
        out[tid] = {
            "chamber": _CHAMBER.get(c.get("type")),
            "name": c.get("name"),
        }
    return out


def parse_memberships(yaml_text: str, valid_thomas_ids: set[str]) -> list[dict]:
    """Parse committee-membership-current.yaml → membership rows.

    Keyed upstream by thomas_id; subcommittee keys (e.g. SSJU01) are skipped —
    only full-committee keys present in `valid_thomas_ids` are kept, matching the
    committees table and the full-committee referral targets.
    """
    docs = yaml.safe_load(yaml_text) or {}
    rows: list[dict] = []
    for tid, members in docs.items():
        if tid not in valid_thomas_ids or not isinstance(members, list):
            continue
        for m in members:
            if not isinstance(m, dict):
                continue
            bioguide = m.get("bioguide")
            if not bioguide:
                continue
            rank = m.get("rank")
            rows.append(
                {
                    "thomas_id": tid,
                    "bioguide_id": bioguide,
                    "rank": rank if isinstance(rank, int) else (int(rank) if isinstance(rank, str) and rank.isdigit() else None),
                    "title": m.get("title"),
                    "party": m.get("party"),
                }
            )
    return rows


def fetch_and_parse(
    *, session: requests.Session | None = None
) -> tuple[list[dict], list[dict], str | None]:
    """Fetch both files and parse into (committee rows, membership rows, raw_path).

    Committee rows: {thomas_id, chamber, name}. Membership rows:
    {thomas_id, bioguide_id, rank, title, party}. `congress` is stamped by the
    ingest layer (the current congress), not here.
    """
    com_text, com_raw = fetch_raw(COMMITTEES_URL, "current", session=session)
    mem_text, _ = fetch_raw(MEMBERSHIP_URL, "membership", session=session)

    committees = parse_committees(com_text)
    memberships = parse_memberships(mem_text, set(committees.keys()))

    committee_rows = [
        {"thomas_id": tid, "chamber": meta["chamber"], "name": meta["name"]}
        for tid, meta in committees.items()
    ]
    return committee_rows, memberships, com_raw

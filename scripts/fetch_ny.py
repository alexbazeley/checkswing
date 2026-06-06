"""Fetch + parse New York State BOE contributions via the data.ny.gov SODA API.

Dataset `4j2b-6a2j` ("Campaign Finance Disclosure Reports Contributions: Beginning
1999"). Unlike the file-based CA/PA sources, NY is queried directly over the public
Socrata API — we filter server-side by each owner's first+last name so we pull only
plausible candidates (not every same-surnamed contributor in NY since 1999). The
classifier then makes the precise call. The recipient (cand_comm_name) is inline.

Network calls (query) are the only untested surface; the parsing/SoQL builders are
unit-tested. An optional `NY_APP_TOKEN` env raises the Socrata rate limit but is not
required for these modest, filtered queries.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Iterable, Iterator
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .ny_adapter import _clean

SODA_URL = "https://data.ny.gov/resource/4j2b-6a2j.json"
PAGE = 50000


def _name_parts(owner: dict) -> tuple[set[str], set[str]]:
    """(last names, first-name first-tokens) lowercased, from the owner's variants."""
    lasts: set[str] = set()
    firsts: set[str] = set()
    for v in owner.get("name_variants") or []:
        v = (v or "").strip()
        if not v:
            continue
        if "," in v:                       # "Last, First [Middle]"
            last, _, rest = v.partition(",")
            lasts.add(last.strip().lower())
            toks = rest.split()
            if toks:
                firsts.add(toks[0].strip().lower())
        else:                              # "First [Middle] Last"
            toks = v.split()
            if len(toks) >= 2:
                firsts.add(toks[0].lower())
                lasts.add(toks[-1].lower())
    return {s for s in lasts if s}, {s for s in firsts if s}


def _inlist(values: set[str]) -> str:
    return ",".join("'" + v.replace("'", "''").upper() + "'" for v in sorted(values))


def build_where(lasts: set[str], firsts: set[str]) -> str:
    """SoQL WHERE filtering to an owner's name (case-insensitive)."""
    where = f"upper(flng_ent_last_name) in ({_inlist(lasts)})"
    if firsts:
        where += f" AND upper(flng_ent_first_name) in ({_inlist(firsts)})"
    return where


def query(where: str, app_token: str | None = None, soda_url: str = SODA_URL) -> Iterator[dict]:  # pragma: no cover - network
    """Paginated SODA query. Ordered by trans_number for stable paging."""
    offset = 0
    while True:
        params = urlencode({"$where": where, "$limit": PAGE, "$offset": offset, "$order": "trans_number"})
        req = Request(f"{soda_url}?{params}")
        req.add_header("Accept", "application/json")
        if app_token:
            req.add_header("X-App-Token", app_token)
        with urlopen(req, timeout=180) as resp:  # noqa: S310 (trusted gov data portal)
            batch = json.load(resp)
        if not batch:
            return
        yield from batch
        if len(batch) < PAGE:
            return
        offset += PAGE


def candidate_rows_by_owner(_input, owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    """Query NY per owner (server-side name filter). `_input` is unused (API source)."""
    app_token = os.environ.get("NY_APP_TOKEN") or None
    buckets: dict[str, list[dict]] = {}
    for slug, owner in owners:
        lasts, firsts = _name_parts(owner)
        buckets[slug] = list(query(build_where(lasts, firsts), app_token)) if lasts else []
    return buckets


def make_recipient_resolver(_input=None) -> Callable[[dict], dict]:
    """Recipient is inline on each NY row (cand_comm_name + filer_id)."""

    def _resolve(row: dict) -> dict:
        return {
            "filer_id": _clean(row.get("filer_id")) or None,
            "name": _clean(row.get("cand_comm_name")),
            "type": None,
        }

    return _resolve


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """Dedup on trans_number (NY's unique per-contribution id)."""
    seen: dict[str, dict] = {}
    for row in rows:
        seen.setdefault(_clean(row.get("trans_number")), row)
    return list(seen.values())

"""Fetch + parse Washington PDC contributions via the data.wa.gov SODA API.

Dataset `kv7h-kjye` ("Contributions to Candidates and Political Committees", WA Public
Disclosure Commission). Like NY, WA is queried directly over the public Socrata API —
no bulk download. WA stores the contributor as a single `contributor_name` field in
"LAST FIRST [MIDDLE]" order, so we filter server-side by each owner's last+first name
prefix (`contributor_name like 'STANTON JOHN%'`) to pull only plausible candidates; the
classifier then makes the precise call. The recipient (filer_name + office + party) and
the per-record document number (report_number) are inline.

Network calls (query) are the only untested surface; the WHERE/parse builders are
unit-tested. An optional `WA_APP_TOKEN` env raises the Socrata rate limit but isn't
required for these modest, filtered queries.
"""
from __future__ import annotations

import json
import os
from typing import Callable, Iterable, Iterator
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .wa_adapter import _clean

SODA_URL = "https://data.wa.gov/resource/kv7h-kjye.json"
PAGE = 50000


def _name_pairs(owner: dict) -> set[tuple[str, str]]:
    """{(last, first)} lowercased from the owner's name_variants (both name orders)."""
    pairs: set[tuple[str, str]] = set()
    for v in owner.get("name_variants") or []:
        v = (v or "").strip()
        if not v:
            continue
        if "," in v:                       # "Last, First [Middle]"
            last, _, rest = v.partition(",")
            toks = rest.split()
            if last.strip() and toks:
                pairs.add((last.strip().lower(), toks[0].strip().lower()))
        else:                              # "First [Middle] Last"
            toks = v.split()
            if len(toks) >= 2:
                pairs.add((toks[-1].lower(), toks[0].lower()))
    return {(la, fi) for la, fi in pairs if la and fi}


def _esc(s: str) -> str:
    return s.replace("'", "''").upper()


def build_where(pairs: set[tuple[str, str]]) -> str:
    """SoQL WHERE: OR of 'LAST FIRST%' prefix matches over contributor_name."""
    clauses = [f"upper(contributor_name) like '{_esc(la)} {_esc(fi)}%'" for la, fi in sorted(pairs)]
    return " OR ".join(clauses)


def query(where: str, app_token: str | None = None, soda_url: str = SODA_URL) -> Iterator[dict]:  # pragma: no cover - network
    """Paginated SODA query. Ordered by id for stable paging."""
    offset = 0
    while True:
        params = urlencode({"$where": where, "$limit": PAGE, "$offset": offset, "$order": "id"})
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
    """Query WA per owner (server-side name filter). `_input` is unused (API source)."""
    app_token = os.environ.get("WA_APP_TOKEN") or None
    buckets: dict[str, list[dict]] = {}
    for slug, owner in owners:
        pairs = _name_pairs(owner)
        buckets[slug] = list(query(build_where(pairs), app_token)) if pairs else []
    return buckets


def make_recipient_resolver(_input=None) -> Callable[[dict], dict]:
    """Recipient is inline on each WA row (filer_name + filer_id + type)."""

    def _resolve(row: dict) -> dict:
        from .wa_adapter import _recipient_type
        return {
            "filer_id": _clean(row.get("filer_id")) or None,
            "name": _clean(row.get("filer_name")),
            "type": _recipient_type(row),
        }

    return _resolve


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """Dedup on the native transaction id."""
    seen: dict[str, dict] = {}
    for row in rows:
        seen.setdefault(_clean(row.get("id")), row)
    return list(seen.values())

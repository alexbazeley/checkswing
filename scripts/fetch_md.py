"""Fetch Maryland MDCRIS contributions via the public campaign-finance JSON API.

Endpoint (verified live 2026-07-19):

    POST https://api-campaignfinance.maryland.gov/api/PublicGrid/GetContributionList
    Content-Type: application/json
    {"contributorName": "<surname>", "pageNumber": <n>, "pageSize": <n>}
    → {"data": {"items": [...], "totalItems": <int>}, "succeeded": true}

Like WA and NY, MD is queried directly over an API — no bulk download is needed for
our purposes, though the portal also offers per-year bulk CSV
(`ExportPublicData/GetExportPublicDownloadData`, ~195 MB for 2024) if a full-corpus
pass is ever wanted. We filter server-side by each owner's SURNAME and let the
classifier make the precise call, exactly as the WA fetcher does.

**A REQUEST-SHAPE GOTCHA THAT LOOKS LIKE SUCCESS.** Two of them, both verified:

1. A bare `curl` (no User-Agent) gets **403**. Adding an ordinary browser UA gets
   **200**. This is a UA check, NOT a wall — and it is exactly the shape that got
   other states written off as "walled" on one-session recon. Hence the explicit
   User-Agent header below.
2. `contributorName` filters server-side, but `search` / `searchText` / `filter` are
   **silently ignored** — an empty body returns 200 with the entire ~3.96M-row
   corpus. A wrong parameter name therefore looks like it worked and quietly
   scans everything, so the payload is built in one place here rather than
   assembled by callers.

Network calls are the only untested surface; the payload/paging/bucketing builders
are unit-tested.
"""
from __future__ import annotations

import json
from typing import Callable, Iterable, Iterator
from urllib.request import Request, urlopen

from .md_adapter import _clean, surname_of

API_BASE = "https://api-campaignfinance.maryland.gov/api"
CONTRIBUTION_URL = f"{API_BASE}/PublicGrid/GetContributionList"
PAGE = 100          # 100 is accepted; 2000 returns HTTP 400.

# The portal 403s a request without a browser User-Agent (see module docstring).
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def surnames_of(owner: dict) -> set[str]:
    """Distinct lowercased surnames from an owner's name_variants.

    MD's filter is a substring match on the whole contributorName, so querying the
    surname alone is both sufficient and the widest safe net — the classifier does
    the discriminating work afterwards.
    """
    out: set[str] = set()
    for v in owner.get("name_variants") or []:
        v = _clean(v)
        if not v:
            continue
        if "," in v:                       # "Last, First [Middle]"
            last = v.partition(",")[0]
        else:                              # "First [Middle] Last"
            toks = v.split()
            last = toks[-1] if toks else ""
        last = last.strip().lower()
        if last:
            out.add(last)
    return out


def build_payload(surname: str, page_number: int = 1, page_size: int = PAGE) -> dict:
    """The one place a request body is constructed — see gotcha (2) in the docstring."""
    return {
        "contributorName": surname,
        "pageNumber": page_number,
        "pageSize": page_size,
    }


def _post(url: str, payload: dict, timeout: int = 180):  # pragma: no cover - network
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _UA,
        },
    )
    from .state_http import retry_call

    with retry_call(lambda: urlopen(req, timeout=timeout)) as resp:  # noqa: S310 (trusted gov portal)
        return json.load(resp)


def parse_response(doc: dict) -> tuple[list[dict], int]:
    """(items, totalItems) from the MDCRIS envelope, tolerating a failed response."""
    if not isinstance(doc, dict):
        return [], 0
    if doc.get("succeeded") is False:
        raise RuntimeError(f"MDCRIS returned an error: {doc.get('error')!r}")
    data = doc.get("data") or {}
    items = data.get("items") or []
    total = data.get("totalItems") or 0
    return list(items), int(total)


def query_surname(surname: str, url: str = CONTRIBUTION_URL) -> Iterator[dict]:  # pragma: no cover - network
    """Page through every contribution whose contributorName matches `surname`."""
    page = 1
    seen = 0
    while True:
        items, total = parse_response(_post(url, build_payload(surname, page)))
        if not items:
            return
        yield from items
        seen += len(items)
        if seen >= total or len(items) < PAGE:
            return
        page += 1


def candidate_rows_by_owner(_input, owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    """Query MD per owner (server-side surname filter). `_input` unused (API source)."""
    buckets: dict[str, list[dict]] = {}
    for slug, owner in owners:
        rows: list[dict] = []
        for surname in sorted(surnames_of(owner)):
            rows.extend(query_surname(surname))
        buckets[slug] = dedupe(rows)
    return buckets


def make_recipient_resolver(_input=None) -> Callable[[dict], dict]:
    """Recipient is inline on every MD row (committeeName + type + filingEntityId)."""

    def _resolve(row: dict) -> dict:
        from .md_adapter import _recipient_type

        return {
            "filer_id": _clean(row.get("filingEntityId")) or None,
            "name": _clean(row.get("committeeName")),
            "type": _recipient_type(row),
        }

    return _resolve


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """Dedup on transactionGuid — stable across report versions, unlike transactionId.

    Necessary because an owner with several name_variants sharing one surname would
    otherwise fetch the same rows once per variant.
    """
    seen: dict[str, dict] = {}
    for row in rows:
        key = _clean(row.get("transactionGuid")) or _clean(row.get("transactionId"))
        if key:
            seen.setdefault(key, row)
    return list(seen.values())


def bucket_rows_by_owner(rows: Iterable[dict], owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    """Local surname bucketing — used when rows come from the bulk CSV path."""
    wanted = {slug: surnames_of(owner) for slug, owner in owners}
    buckets: dict[str, list[dict]] = {slug: [] for slug, _ in owners}
    for row in rows:
        sn = surname_of(row)
        if not sn:
            continue
        for slug, names in wanted.items():
            if sn in names:
                buckets[slug].append(row)
    return buckets

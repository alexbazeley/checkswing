"""Fetch Arizona "See The Money" contributions via its public JSON API.

seethemoney.az.gov (AZ SOS + Citizens Clean Elections Commission) is a vendor
ASP.NET app, not a bulk download. It exposes a public, un-authenticated JSON API
organised around the "Individuals" contributor view. Pulling a person's outgoing
contributions is a three-step dance per matched filer entity:

  1. SEARCH   POST /Reporting/GetNEWTableData (Page=7, ChartName=7) with the surname
              in the DataTables `search[value]` → contributor entities + EntityIDs.
              The match is a broad substring, and `EntityLastName` is a messy
              "Last, First [Middle]" string, so we filter to entities whose
              (surname, first) matches the owner's name_variants.
  2. PRIME    GET  /Reporting/GetEntityName?NameId=<id> — the server keeps the
              selected entity in session; the detail call returns [] without this.
  3. DETAIL   POST /Reporting/GetNEWDetailedTableData (Page=80, ChartName=80,
              Name=7~<id>, entityId=<id>) with the FULL DataTables column array →
              every itemised contribution that entity made.

One person maps to MANY entities (a record per name spelling / cycle), so we union
the detail rows across all matched entities; the classifier then makes the precise
per-row call. Always pull the full year range (AZ defaults to the current cycle).

Only the network calls are untested; the DataTables body builder, the entity-name
matcher, and the JSON response parsers are pure and unit-tested.
"""
from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from typing import Callable, Iterable, Iterator

from .az_adapter import _clean

BASE = "https://seethemoney.az.gov"
RAW_REF = f"{BASE}/Reporting/GetNEWDetailedTableData (Page=80, Individuals)"
START_YEAR = 2002


def _end_year() -> int:
    # AZ posts in-cycle filings ahead of the calendar year; pad by one.
    return datetime.now(tz=timezone.utc).year + 1


# ── Pure helpers (unit-tested) ───────────────────────────────────────────────

def _name_pairs(owner: dict) -> set[tuple[str, str]]:
    """{(last, first)} lowercased from name_variants (both 'Last, First' and 'First Last')."""
    pairs: set[tuple[str, str]] = set()
    for v in owner.get("name_variants") or []:
        v = (v or "").strip()
        if not v:
            continue
        if "," in v:
            last, _, rest = v.partition(",")
            toks = rest.split()
            if last.strip() and toks:
                pairs.add((last.strip().lower(), toks[0].strip().lower()))
        else:
            toks = v.split()
            if len(toks) >= 2:
                pairs.add((toks[-1].lower(), toks[0].lower()))
    return {(la, fi) for la, fi in pairs if la and fi}


def _entity_name_parts(entity_last_name: str) -> tuple[str, str]:
    """AZ EntityLastName is 'Last, First [Middle]' → (last, first) lowercased."""
    s = _clean(entity_last_name)
    if "," in s:
        last, _, rest = s.partition(",")
        toks = rest.split()
        return last.strip().lower(), (toks[0].strip().lower() if toks else "")
    toks = s.split()
    if len(toks) >= 2:
        return toks[-1].lower(), toks[0].lower()
    return s.lower(), ""


def entity_matches(entity_last_name: str, pairs: set[tuple[str, str]]) -> bool:
    """Keep an entity only if surname matches AND first names are prefix-compatible
    (so 'Ken' ↔ 'Kenneth', 'Art' ↔ 'Arturo', but not 'Ken' ↔ 'Monica')."""
    e_last, e_first = _entity_name_parts(entity_last_name)
    if not e_last:
        return False
    for p_last, p_first in pairs:
        if e_last != p_last:
            continue
        if not e_first or not p_first:
            return True
        if e_first.startswith(p_first) or p_first.startswith(e_first):
            return True
    return False


def _dt_body(search: str = "", length: int = 500) -> bytes:
    """A minimal-but-complete DataTables POST body (the API rejects a stripped one)."""
    b: list[tuple[str, str]] = [("draw", "1"), ("start", "0"), ("length", str(length))]
    for i in range(12):
        b += [
            (f"columns[{i}][data]", str(i)), (f"columns[{i}][name]", ""),
            (f"columns[{i}][searchable]", "true"), (f"columns[{i}][orderable]", "true"),
            (f"columns[{i}][search][value]", ""), (f"columns[{i}][search][regex]", "false"),
        ]
    b += [("order[0][column]", "0"), ("order[0][dir]", "asc"),
          ("search[value]", search), ("search[regex]", "false")]
    return urllib.parse.urlencode(b).encode()


def parse_search_response(payload: dict) -> list[dict]:
    """Search JSON → [{EntityID, EntityLastName}] (the contributor entities)."""
    return [
        {"EntityID": r.get("EntityID"), "EntityLastName": _clean(r.get("EntityLastName"))}
        for r in (payload.get("data") or [])
        if r.get("EntityID") is not None
    ]


def parse_detail_response(payload: dict) -> list[dict]:
    """Detail JSON → the raw transaction rows (already in adapter-ready shape)."""
    return list(payload.get("data") or [])


# ── Network (pragma: no cover) ───────────────────────────────────────────────

def _opener():  # pragma: no cover - network
    import http.cookiejar
    from urllib.request import HTTPCookieProcessor, build_opener
    op = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (compatible; tipping-pitches-archive/1.0)"),
                     ("X-Requested-With", "XMLHttpRequest")]
    return op


def _get_json(opener, url: str, body: bytes | None = None) -> dict:  # pragma: no cover - network
    raw = opener.open(url, body, timeout=120).read()
    return json.loads(raw.decode("utf-8", "replace"))


def search_entities(opener, surname: str) -> list[dict]:  # pragma: no cover - network
    q = urllib.parse.urlencode({
        "Page": 7, "startYear": START_YEAR, "endYear": _end_year(), "JurisdictionId": 0,
        "TablePage": 1, "TableLength": 500, "IsLessActive": "false",
        "ShowOfficeHolder": "false", "ChartName": 7,
    })
    payload = _get_json(opener, f"{BASE}/Reporting/GetNEWTableData/?{q}", _dt_body(surname, 500))
    return parse_search_response(payload)


def entity_detail(opener, entity_id) -> list[dict]:  # pragma: no cover - network
    opener.open(f"{BASE}/Reporting/GetEntityName/?NameId={entity_id}", timeout=120).read()  # prime
    q = urllib.parse.urlencode({
        "Page": 80, "startYear": START_YEAR, "endYear": _end_year(), "JurisdictionId": 0,
        "TablePage": 1, "TableLength": 5000, "Name": f"7~{entity_id}", "entityId": entity_id,
        "ChartName": 80, "IsLessActive": "false", "ShowOfficeHolder": "false",
    })
    payload = _get_json(opener, f"{BASE}/Reporting/GetNEWDetailedTableData/?{q}", _dt_body("", 5000))
    return parse_detail_response(payload)


def candidate_rows_by_owner(_input, owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:  # pragma: no cover - network
    """Per owner: search each surname, keep name-matched entities, union their detail."""
    opener = _opener()
    buckets: dict[str, list[dict]] = {}
    for slug, owner in owners:
        pairs = _name_pairs(owner)
        rows: list[dict] = []
        seen_entities: set = set()
        for surname in sorted({la for la, _ in pairs}):
            for ent in search_entities(opener, surname):
                eid = ent["EntityID"]
                if eid in seen_entities:
                    continue
                if not entity_matches(ent["EntityLastName"], pairs):
                    continue
                seen_entities.add(eid)
                rows.extend(entity_detail(opener, eid))
        buckets[slug] = rows
    return buckets


# ── Recipient resolver / dedupe ──────────────────────────────────────────────

def make_recipient_resolver(_input=None) -> Callable[[dict], dict]:
    """Recipient (committee) is inline on every AZ detail row."""

    def _resolve(row: dict) -> dict:
        from .az_adapter import recipient_type_of
        return {
            "filer_id": _clean(row.get("CommitteeUniqueId")) or _clean(row.get("CommitteeId")) or None,
            "name": _clean(row.get("CommitteeName")),
            "type": recipient_type_of(row),
        }

    return _resolve


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """Dedup on the stable PublicTransactionId (fall back to TransactionId)."""
    seen: dict = {}
    for row in rows:
        key = _clean(row.get("PublicTransactionId")) or _clean(row.get("TransactionId")) or id(row)
        seen.setdefault(key, row)
    return list(seen.values())

"""Fetch Florida Division of Elections contributions via its query CGI.

dos.elections.myflorida.com publishes no bulk file, but the contributions query
form POSTs to `/cgi-bin/contrib.exe` and — in "Search for a list of contributions"
mode (`search_on=2`) with `queryformat=2` — returns a **tab-delimited export** of
every itemised contribution matching the criteria. We query per owner surname
(like the AZ/NY/WA per-owner pattern), filter to the owner's surname, and let the
classifier make the precise per-row call.

IMPORTANT (cost the recon several probes): a bare `GET /cgi-bin/contrib.exe` returns
HTTP 502 (the CGI requires a POST) — this is NOT a Cloudflare wall, just the origin
erroring; a well-formed POST returns 200 `text/tab-separated-values`. And
`search_on=1` is "contributor list only" (header but zero contribution rows); the
contribution export is `search_on=2`. The CGI also needs the FULL form param set
present or it empties/SQL-errors, so the body below is complete by design.

Only the network POST is untested; the TSV parser, the recipient/name helpers, the
surname funnel, and dedup are pure and unit-tested.
"""
from __future__ import annotations

import csv
import hashlib
import io
import urllib.parse
from typing import Callable, Iterable, Iterator

from .fl_adapter import _clean, parse_recipient, recipient_type_of, surname_of

CONTRIB_URL = "https://dos.elections.myflorida.com/cgi-bin/contrib.exe"
RAW_REF = "https://dos.elections.myflorida.com/campaign-finance/contributions/"

# Exact TSV header the CGI returns (used to validate the response shape).
TSV_HEADER = [
    "Candidate/Committee", "Date", "Amount", "Typ", "Contributor Name",
    "Address", "City State Zip", "Occupation", "Inkind Desc",
]


# ── Form body ────────────────────────────────────────────────────────────────

def build_form_body(surname: str, *, namesearch: int = 1, rowlimit: int = 5000) -> bytes:
    """A complete contrib.exe POST body for one contributor surname.

    `search_on=2` (list of contributions) + `queryformat=2` (tab-delimited file);
    `namesearch=1` = last-name Containing. The CGI requires every field present, so
    all the empty filters are sent explicitly.
    """
    fields = [
        ("search_on", "2"), ("namesearch", str(namesearch)),
        ("clname", surname), ("cfname", ""),
        ("CanFName", ""), ("CanLName", ""), ("CanNameSrch", "2"),
        ("ComName", ""), ("ComNameSrch", "2"),
        ("election", "All"), ("office", "All"), ("party", "All"),
        ("cgroup", "All"), ("cdistrict", "All"),
        ("queryformat", "2"), ("rowlimit", str(rowlimit)),
        ("csort1", "NAM"), ("csort2", "DAT"),
        ("cdatefrom", ""), ("cdateto", ""),
        ("ccity", ""), ("cstate", ""), ("czipcode", ""), ("coccupation", ""),
        ("cdollar_minimum", ""), ("cdollar_maximum", ""),
        ("Submit", "Submit"),
    ]
    return urllib.parse.urlencode(fields).encode()


# ── Pure parsers ─────────────────────────────────────────────────────────────

def _content_tran(row: dict) -> str:
    """Stable per-contribution hash (FL has no native id) — idempotent across pulls."""
    parts = "|".join(
        _clean(row.get(k))
        for k in ("Candidate/Committee", "Date", "Amount", "Contributor Name",
                  "Address", "City State Zip", "Occupation")
    )
    return hashlib.sha1(parts.encode("utf-8")).hexdigest()[:16]


def parse_tsv(text: str) -> list[dict]:
    """Parse the contrib.exe tab-delimited export → recipient-joined, hash-stamped rows.

    Tolerates the occasional embedded error/HTML line the CGI can emit (a row whose
    first cell starts with '<' or that lacks the expected column count is skipped).
    """
    if not text:
        return []
    reader = csv.reader(io.StringIO(text), delimiter="\t")
    rows = list(reader)
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    # Only proceed if this looks like the expected export (first/key columns present).
    if "Contributor Name" not in header:
        return []
    out: list[dict] = []
    for raw in rows[1:]:
        if not raw or (raw[0].lstrip().startswith("<")):
            continue  # stray HTML/error line
        if len(raw) < len(header):
            continue
        row = {header[i]: raw[i] for i in range(len(header))}
        rname, rparty, roffice = parse_recipient(row.get("Candidate/Committee"))
        row["_recipient_name"] = rname
        row["_recipient_party"] = rparty
        row["_recipient_office"] = roffice
        row["_recipient_type"] = recipient_type_of(row)
        row["_tran"] = _content_tran(row)
        out.append(row)
    return out


# ── Recipient resolver / bucketing / dedup ───────────────────────────────────

def make_recipient_resolver(_input=None) -> Callable[[dict], dict]:
    """Recipient is parsed inline by parse_tsv (no filer id in the FL export)."""

    def _resolve(row: dict) -> dict:
        return {
            "filer_id": None,
            "name": _clean(row.get("_recipient_name")),
            "type": row.get("_recipient_type"),
        }

    return _resolve


def _surname_set(owner: dict) -> set[str]:
    surnames: set[str] = set()
    for v in owner.get("name_variants") or []:
        v = (v or "").strip()
        if not v:
            continue
        surnames.add((v.split(",")[0] if "," in v else v.split()[-1]).strip().lower())
    return {s for s in surnames if s}


def bucket_rows_by_owner(rows: Iterable[dict], owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:
    """Funnel rows to owners by surname (a 'Containing' query can return adjacent
    surnames; the surname funnel + classifier keep attribution correct)."""
    surname_map = [(slug, _surname_set(o)) for slug, o in owners]
    buckets: dict[str, list[dict]] = {slug: [] for slug, _ in owners}
    for row in rows:
        sn = surname_of(row)
        if not sn:
            continue
        for slug, sns in surname_map:
            if sns and any(s in sn for s in sns):
                buckets[slug].append(row)
    return buckets


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """Dedup on the content-hash tran id (idempotent across re-pulls)."""
    seen: dict[str, dict] = {}
    for row in rows:
        key = _clean(row.get("_tran")) or id(row)
        seen.setdefault(key, row)
    return list(seen.values())


# ── Network (pragma: no cover) ───────────────────────────────────────────────

def _opener():  # pragma: no cover - network
    from urllib.request import build_opener
    op = build_opener()
    op.addheaders = [
        ("User-Agent", "Mozilla/5.0 (compatible; tipping-pitches-archive/1.0)"),
        ("Referer", RAW_REF),
        ("Content-Type", "application/x-www-form-urlencoded"),
    ]
    return op


def query_surname(opener, surname: str, *, namesearch: int = 1) -> list[dict]:  # pragma: no cover - network
    raw = opener.open(CONTRIB_URL, build_form_body(surname, namesearch=namesearch), timeout=120).read()
    return parse_tsv(raw.decode("utf-8", "replace"))


def candidate_rows_by_owner(_input, owners: list[tuple[str, dict]]) -> dict[str, list[dict]]:  # pragma: no cover - network
    """Per owner: POST each surname, parse the TSV, funnel by surname into its bucket."""
    opener = _opener()
    buckets: dict[str, list[dict]] = {slug: [] for slug, _ in owners}
    # Query each distinct surname once, then funnel its rows to every matching owner.
    surname_to_owners: dict[str, list[tuple[str, dict]]] = {}
    for slug, owner in owners:
        for sn in _surname_set(owner):
            surname_to_owners.setdefault(sn, []).append((slug, owner))
    for surname, subset in surname_to_owners.items():
        rows = query_surname(opener, surname)
        for slug, sub in bucket_rows_by_owner(rows, subset).items():
            buckets[slug].extend(sub)
    return buckets

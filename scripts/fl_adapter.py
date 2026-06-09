"""Adapter: Florida Division of Elections contribution rows → pipeline shapes.

Florida's DoE campaign-finance database (dos.elections.myflorida.com) has no bulk
file, but its query CGI (`/cgi-bin/contrib.exe`) returns a **tab-delimited export**
to a POST (the "Search for a list of contributions" mode, `search_on=2`,
`queryformat=2`). One TSV row = one itemised contribution; columns:

    Candidate/Committee, Date, Amount, Typ, Contributor Name, Address,
    City State Zip, Occupation, Inkind Desc

Notes that shape this adapter:
  * `Contributor Name` is a single **"LAST FIRST [MIDDLE]"** space string (last name
    FIRST, NO comma — e.g. "STEINBRENNER HAROLD Z.", "ZALUPSKI PATRICK"). The
    classifier's normalize_name expects either "First Last" or "Last, First" (comma),
    so we rebuild it into the comma form ("STEINBRENNER, HAROLD Z.") — surname = the
    first whitespace token — and the classifier swaps it correctly. The surname
    prefilter takes that same first token.
  * FL discloses **Occupation + city/state/zip but NO employer** — so the two
    confirming paths are occupation + city_state (two signals → CONFIRMED) or a
    documented strong ZIP; richer than NY/MN (which lack occupation), weaker than the
    employer states. `City State Zip` is ONE combined field ("TAMPA, FL 33623") that
    we split.
  * The recipient is INLINE as `Lastname, First (PARTY)(OFFICE)` (e.g.
    "DeSantis, Ron  (REP)(GOV)"), so — unlike CO/MN — we recover recipient party AND
    office. There is no recipient filer id in the export.
  * There is **no per-contribution id**; the fetcher stamps a content-hash `_tran`
    for idempotent keying + dedup (like PA/MN).
  * `Date` is `MM/DD/YYYY`. Returned/negative-amount rows kept as-is (honest archive).
"""
from __future__ import annotations

import re


def _clean(s) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    return "" if s.upper() == "NULL" else s


def parse_fl_date(raw) -> str | None:
    """FL dates are 'MM/DD/YYYY' → ISO 'YYYY-MM-DD'; accept ISO defensively."""
    s = _clean(raw)
    if not s:
        return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def parse_amount(raw) -> float | None:
    s = _clean(raw).replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def election_cycle_from_date(iso_date: str | None) -> int | None:
    if not iso_date or len(iso_date) < 4:
        return None
    try:
        return int(iso_date[:4])
    except ValueError:
        return None


def contributor_comma_name(raw) -> str:
    """FL 'LAST FIRST [MIDDLE]' (last-name-first, no comma) → 'LAST, FIRST [MIDDLE]'
    so the classifier's normalize_name swaps it to first-last correctly. The surname
    is the first whitespace token (any trailing comma/period stripped)."""
    s = _clean(raw)
    if not s:
        return ""
    toks = s.split()
    surname = toks[0].rstrip(",.")
    rest = " ".join(toks[1:]).strip()
    return f"{surname}, {rest}" if rest else surname


def surname_of(row: dict) -> str:
    """First token of the Contributor Name, lowercased — the prefilter funnel."""
    s = _clean(row.get("Contributor Name"))
    return s.split()[0].rstrip(",.").lower() if s else ""


def split_city_state_zip(raw) -> tuple[str, str, str]:
    """'CITY, ST ZIP[-####]' → (city, state, zip5). Best-effort; empties on no match."""
    s = _clean(raw)
    if not s:
        return ("", "", "")
    m = re.match(r"^(.*?),\s*([A-Za-z]{2})\s+(\d{5})(?:-\d+)?\s*$", s)
    if m:
        return (m.group(1).strip(), m.group(2).upper(), m.group(3))
    # Fallback: trailing 2-letter state + 5-digit zip without a comma.
    m = re.match(r"^(.*?)\s+([A-Za-z]{2})\s+(\d{5})(?:-\d+)?\s*$", s)
    if m:
        return (m.group(1).strip(), m.group(2).upper(), m.group(3))
    return (s, "", "")


_RECIP_CODES = re.compile(r"\(([^)]*)\)\s*\(([^)]*)\)\s*$")


def parse_recipient(candcomm) -> tuple[str, str | None, str | None]:
    """'DeSantis, Ron  (REP)(GOV)' → (name, party, office). Committees with no trailing
    (party)(office) codes return (name, None, None)."""
    s = _clean(candcomm)
    if not s:
        return ("", None, None)
    m = _RECIP_CODES.search(s)
    if m:
        name = s[: m.start()].strip()
        party = _clean(m.group(1)) or None
        office = _clean(m.group(2)) or None
        return (name, party, office)
    return (s, None, None)


def to_classifier_record(row: dict) -> dict:
    city, state, zipc = split_city_state_zip(row.get("City State Zip"))
    return {
        "contributor_name": contributor_comma_name(row.get("Contributor Name")),
        "contributor_first_name": "",
        "contributor_middle_name": "",
        "contributor_last_name": "",
        "contributor_suffix": "",
        "contributor_employer": "",  # FL discloses no employer
        "contributor_occupation": _clean(row.get("Occupation")),
        "contributor_city": city,
        "contributor_state": state,
        "contributor_zip": zipc,
    }


def filing_id_of(row: dict) -> str | None:
    """FL's export carries no filing id; the content-hash tran is the unique key."""
    return None


def tran_id_of(row: dict) -> str | None:
    """The fetcher stamps a stable content-hash on `_tran` (no native per-row id)."""
    return _clean(row.get("_tran")) or None


def recipient_type_of(row: dict) -> str | None:
    """A trailing office code (GOV/STR/STS/…) marks a candidate; otherwise a committee."""
    _name, _party, office = parse_recipient(row.get("Candidate/Committee"))
    return "candidate" if office else "committee"


def to_state_donation_row(
    row: dict,
    *,
    state_txn_id: str,
    status: str,
    status_reason: str,
    signals_matched_json: str,
    entity_slug: str,
    entity_kind: str,
    parent_owner_slug: str | None,
    recipient_filer_id: str | None,
    recipient_name: str,
    recipient_type: str | None,
    raw_payload_path: str,
    ingested_at: str,
    jurisdiction: str = "FL",
    source: str = "FL-DOE",
    discovery_source: str | None = None,
) -> dict:
    iso_date = parse_fl_date(row.get("Date"))
    amount = parse_amount(row.get("Amount"))
    rname, rparty, roffice = parse_recipient(row.get("Candidate/Committee"))
    city, state, zipc = split_city_state_zip(row.get("City State Zip"))
    return {
        "state_txn_id": state_txn_id,
        "jurisdiction": jurisdiction,
        "source": source,
        "source_tran_id": tran_id_of(row),
        "source_filing_id": filing_id_of(row),
        "discovery_source": discovery_source,
        "entity_slug": entity_slug,
        "entity_kind": entity_kind,
        "parent_owner_slug": parent_owner_slug,
        "status": status,
        "status_reason": status_reason,
        "signals_matched": signals_matched_json,
        "contributor_name_raw": _clean(row.get("Contributor Name")),
        "contributor_employer_raw": None,
        "contributor_occupation_raw": _clean(row.get("Occupation")) or None,
        "contributor_city": city or None,
        "contributor_state": state or None,
        "contributor_zip": zipc or None,
        "recipient_filer_id": recipient_filer_id,
        "recipient_name": recipient_name or rname,
        "recipient_type": recipient_type or recipient_type_of(row),
        "recipient_party": rparty,
        "recipient_office": roffice,
        "amount": amount,
        "date": iso_date,
        "election_cycle": election_cycle_from_date(iso_date),
        "report_type": _clean(row.get("Typ")) or None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

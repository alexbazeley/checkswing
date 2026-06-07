"""Adapter: Illinois State Board of Elections (ISBE) bulk receipt rows → pipeline shapes.

The ISBE publishes the whole campaign-disclosure database as public tab-delimited
bulk files (no login/key). `Receipts.txt` holds every itemized receipt; `Committees.txt`
is the recipient lookup. The receipt columns this adapter reads:

    ID, CommitteeID, FiledDocID, ETransID, LastOnlyName, FirstName, RcvDate, Amount,
    AggregateAmount, LoanAmount, Occupation, Employer, Address1, Address2, City, State,
    Zip, D2Part, Description, ...

Notes that shape this adapter:
  * Names are PRE-SPLIT (`LastOnlyName` / `FirstName`); `FirstName` bundles the middle
    ("H James"), so we split the first token off as the first name and keep the rest as
    the middle — preserving the middle-initial discriminator the federal classifier uses.
    Organization donors put the org name in `LastOnlyName` with an empty `FirstName`;
    those won't match a personal owner's name_variants (correct — corporate giving isn't
    auto-attributed).
  * `Occupation` + `Employer` are present → the two-signal CONFIRMED bar is reachable
    (ISBE is gold-grade, itemizing occupation/employer for individuals giving > $500).
  * `ID` is a NATIVE per-receipt id and `FiledDocID` the filed document → keying needs
    no content-hash (unlike PA).
  * `D2Part` is the schedule code (1A = individual contribution, 2A = transfer in,
    3A = loan, …). We ingest all receipts and let the classifier filter by name; only
    an individual contribution carries a personal owner's name. The code is recorded as
    `report_type` for provenance.
  * The recipient is `CommitteeID` → `Committees.txt` (Name + TypeOfCommittee +
    PartyAffiliation) — ISBE, like PA, carries recipient party.
"""
from __future__ import annotations

import re


def _clean(s) -> str:
    if s is None:
        return ""
    return str(s).strip()


def parse_il_date(raw) -> str | None:
    """ISBE receipt dates are 'YYYY-MM-DD hh:mm:ss' → ISO 'YYYY-MM-DD'."""
    s = _clean(raw)
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", s)
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


def _split_first_middle(first_raw: str) -> tuple[str, str]:
    """'H James' → ('H', 'James'); 'Jerry' → ('Jerry', '')."""
    toks = _clean(first_raw).split()
    if not toks:
        return "", ""
    return toks[0], " ".join(toks[1:])


def _composed_name(row: dict) -> str:
    """A 'First [Middle] Last' string for the classifier's name match.

    Falls back to the LastOnlyName alone for organization donors (empty FirstName) —
    which won't match a personal owner's name_variants (correct)."""
    first = _clean(row.get("FirstName"))
    last = _clean(row.get("LastOnlyName"))
    parts = [p for p in (first, last) if p]
    return " ".join(parts) if parts else last


def to_classifier_record(row: dict) -> dict:
    """Map an ISBE receipt row → the classifier record dict."""
    first, middle = _split_first_middle(row.get("FirstName"))
    return {
        "contributor_name": _composed_name(row),
        "contributor_first_name": first,
        "contributor_middle_name": middle,
        "contributor_last_name": _clean(row.get("LastOnlyName")),
        "contributor_suffix": "",
        "contributor_employer": _clean(row.get("Employer")),
        "contributor_occupation": _clean(row.get("Occupation")),
        "contributor_city": _clean(row.get("City")),
        "contributor_state": _clean(row.get("State")),
        "contributor_zip": _clean(row.get("Zip")),
    }


def surname_of(row: dict) -> str:
    """LastOnlyName lowercased — the prefilter funnel (matches CA/PA/TX shape)."""
    return _clean(row.get("LastOnlyName")).lower()


def filing_id_of(row: dict) -> str | None:
    return _clean(row.get("FiledDocID")) or None


def tran_id_of(row: dict) -> str | None:
    return _clean(row.get("ID")) or None


def recipient_type_of(type_of_committee: str) -> str | None:
    """Map an ISBE TypeOfCommittee → recipient_type vocabulary."""
    t = _clean(type_of_committee).lower()
    if not t:
        return None
    if "candidate" in t:
        return "candidate"
    if "ballot" in t:
        return "ballot_measure"
    if any(k in t for k in ("political action", "political party", "independent", "activity")):
        return "committee"
    return "committee"


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
    jurisdiction: str = "IL",
    source: str = "ISBE",
    discovery_source: str | None = None,
) -> dict:
    iso_date = parse_il_date(row.get("RcvDate"))
    amount = parse_amount(row.get("Amount"))
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
        "contributor_name_raw": _composed_name(row),
        "contributor_employer_raw": _clean(row.get("Employer")) or None,
        "contributor_occupation_raw": _clean(row.get("Occupation")) or None,
        "contributor_city": _clean(row.get("City")) or None,
        "contributor_state": _clean(row.get("State")) or None,
        "contributor_zip": _clean(row.get("Zip")) or None,
        "recipient_filer_id": recipient_filer_id,
        "recipient_name": recipient_name,
        "recipient_type": recipient_type,
        "recipient_party": _clean(row.get("_recipient_party")) or None,
        "recipient_office": None,    # ISBE committee records carry no office-sought field
        "amount": amount,
        "date": iso_date,
        "election_cycle": election_cycle_from_date(iso_date),
        "report_type": _clean(row.get("D2Part")) or None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

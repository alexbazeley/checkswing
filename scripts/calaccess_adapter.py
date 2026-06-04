"""Adapter: CAL-ACCESS RCPT_CD rows → the shapes the rest of the pipeline expects.

The classifier (scripts/resolve_entities.py) and the state DB (scripts/state_db.py)
are source-agnostic; only the *input shape* differs per state portal. This module
is the California (CAL-ACCESS) adapter. Adding another state means writing a sibling
adapter that produces the same two shapes — nothing downstream changes.

CAL-ACCESS RCPT_CD is the itemized-receipts table. The fields we read (documented at
https://calaccess.californiacivicdata.org/documentation/raw-files/rcpt-cd/):

    ENTITY_CD   contributor entity type: 'IND' individual, 'COM'/'RCP' committee,
                'OTH' other, 'PTY' party, 'SCC' small-contributor committee.
    CTRIB_NAML  contributor last name OR business name
    CTRIB_NAMF  contributor first name
    CTRIB_NAMT  contributor prefix/title (Mr., Dr., …) — honorific, classifier strips it
    CTRIB_NAMS  contributor suffix (Jr., III, …)
    CTRIB_EMP   employer
    CTRIB_OCC   occupation
    CTRIB_CITY / CTRIB_ST / CTRIB_ZIP4   contributor address
    AMOUNT      amount received
    RCPT_DATE   date received
    TRAN_ID     permanent id unique to this item (within a filing)
    FILING_ID   the filing this item belongs to → resolves the recipient filer
    CMTE_ID     committee id, when present

Recipient identity (who received the money) is NOT in RCPT_CD itself — it is the
*filer* of FILING_ID, resolved from the cover-page/filer lookup. The fetcher builds
that map; this adapter takes the resolved recipient as an explicit argument so it
stays pure and unit-testable.
"""
from __future__ import annotations

import re

# CAL-ACCESS individual-entity codes. Only individuals can match an owner-as-person
# under the two-signal rule; business/committee contributors won't match a personal
# name_variant anyway, but we keep the code so callers can scope a scan if desired.
INDIVIDUAL_ENTITY_CODES = {"IND"}


def _clean(s) -> str:
    if s is None:
        return ""
    return str(s).strip()


def parse_calaccess_date(raw) -> str | None:
    """Normalize a CAL-ACCESS date to ISO 'YYYY-MM-DD'.

    CCDC's cleaned export gives ISO already; the raw .TSV gives forms like
    '6/1/2018 12:00:00 AM'. Returns None if unparseable (caller routes the row
    to the review queue rather than inventing a date — GOVERNANCE.md §1.6).
    """
    s = _clean(raw)
    if not s:
        return None
    # ISO 'YYYY-MM-DD' (optionally with a time component).
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # US 'M/D/YYYY' (optionally with time).
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
        return f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
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
    """State election cycles vary by office and state, so we store the contribution's
    calendar year as the cycle (documented in STATE_DONATION_SCHEMA.md). This keeps
    the field honest rather than forcing CA data into FEC's even-year two-year cycle.
    """
    if not iso_date or len(iso_date) < 4:
        return None
    try:
        return int(iso_date[:4])
    except ValueError:
        return None


def contributor_full_name(rcpt: dict) -> str:
    """Best-effort display name from the split CAL-ACCESS fields.

    For an individual that is 'Last, First' (comma form, which the classifier's
    normalize_name swaps); for a business it is just CTRIB_NAML.
    """
    naml = _clean(rcpt.get("CTRIB_NAML"))
    namf = _clean(rcpt.get("CTRIB_NAMF"))
    if namf:
        return f"{naml}, {namf}"
    return naml


def to_classifier_record(rcpt: dict) -> dict:
    """Map a CAL-ACCESS RCPT_CD row → the record dict resolve_entities.classify reads.

    Produces both `contributor_name` (the 'Last, First' comma form the classifier
    can swap) and the structured first/last/suffix fields so the classifier's
    synthetic-name fallback (names_match_with_fallback) has a clean shot too.
    """
    return {
        "contributor_name": contributor_full_name(rcpt),
        "contributor_first_name": _clean(rcpt.get("CTRIB_NAMF")),
        "contributor_middle_name": "",  # CAL-ACCESS has no separate middle field
        "contributor_last_name": _clean(rcpt.get("CTRIB_NAML")),
        "contributor_suffix": _clean(rcpt.get("CTRIB_NAMS")),
        "contributor_employer": _clean(rcpt.get("CTRIB_EMP")),
        "contributor_occupation": _clean(rcpt.get("CTRIB_OCC")),
        "contributor_city": _clean(rcpt.get("CTRIB_CITY")),
        "contributor_state": _clean(rcpt.get("CTRIB_ST")),
        "contributor_zip": _clean(rcpt.get("CTRIB_ZIP4")),
    }


def to_state_donation_row(
    rcpt: dict,
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
    jurisdiction: str = "CA",
    source: str = "CAL-ACCESS",
    discovery_source: str | None = None,
) -> dict:
    """Build a state_donations row (state_db schema) from a RCPT row + classifier verdict."""
    iso_date = parse_calaccess_date(rcpt.get("RCPT_DATE"))
    amount = parse_amount(rcpt.get("AMOUNT"))
    return {
        "state_txn_id": state_txn_id,
        "jurisdiction": jurisdiction,
        "source": source,
        "source_tran_id": _clean(rcpt.get("TRAN_ID")) or None,
        "source_filing_id": _clean(rcpt.get("FILING_ID")) or None,
        "discovery_source": discovery_source,
        "entity_slug": entity_slug,
        "entity_kind": entity_kind,
        "parent_owner_slug": parent_owner_slug,
        "status": status,
        "status_reason": status_reason,
        "signals_matched": signals_matched_json,
        "contributor_name_raw": contributor_full_name(rcpt),
        "contributor_employer_raw": _clean(rcpt.get("CTRIB_EMP")) or None,
        "contributor_occupation_raw": _clean(rcpt.get("CTRIB_OCC")) or None,
        "contributor_city": _clean(rcpt.get("CTRIB_CITY")) or None,
        "contributor_state": _clean(rcpt.get("CTRIB_ST")) or None,
        "contributor_zip": _clean(rcpt.get("CTRIB_ZIP4")) or None,
        "recipient_filer_id": recipient_filer_id,
        "recipient_name": recipient_name,
        "recipient_type": recipient_type,
        "recipient_party": None,  # CAL-ACCESS receipts don't carry recipient party
        "recipient_office": None,
        "amount": amount,
        "date": iso_date,
        "election_cycle": election_cycle_from_date(iso_date),
        "report_type": None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

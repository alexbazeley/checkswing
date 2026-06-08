"""Adapter: Colorado TRACER contribution rows → pipeline shapes.

Colorado's Secretary of State publishes campaign-finance data through TRACER as
one **per-year zip** (`<YEAR>_ContributionData.csv.zip`) of a single comma-
delimited, header-bearing CSV (`<YEAR>_ContributionData.csv`) at
tracer.sos.colorado.gov. Columns (one row = one itemized contribution):

    CO_ID, ContributionAmount, ContributionDate, LastName, FirstName, MI, Suffix,
    Address1, Address2, City, State, Zip, Explanation, RecordID, FiledDate,
    ContributionType, ReceiptType, ContributorType, Electioneering, CommitteeType,
    CommitteeName, CandidateName, Employer, Occupation, Amended, Amendment,
    AmendedRecordID, Jurisdiction, OccupationComments

Notes that shape this adapter:
  * The contributor name is SPLIT across LastName / FirstName / MI / Suffix
    (CA-style), so we produce both the 'Last, First' comma form the classifier can
    swap AND the structured fields its synthetic-name fallback reads.
  * `Employer` and `Occupation` are both present — the two-signal CONFIRMED bar is
    reachable (like CA/PA/TX/WA), which matters because "Monfort" is a large
    Colorado family name (the Greeley meatpacking dynasty) and the Rockies owner's
    own brother Charlie is a same-employer co-owner: only employer + first-name
    discrimination separates Dick Monfort cleanly.
  * `RecordID` is a native per-contribution id → the dedup key + source_tran_id
    (no content-hash needed, unlike PA).
  * The recipient is INLINE on each row (CommitteeName + CommitteeType, CO_ID is the
    committee/filer id), so the resolver is a stateless reader (no filer-index file,
    unlike PA). Colorado does not disclose recipient party/office in this export.
  * One (ContributionDate, ContributionAmount) pair per row (no explosion). Returned
    contributions appear as their own negative-amount rows — kept as-is (honest
    archive), netting naturally against the original.
"""
from __future__ import annotations

import re


def _clean(s) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    return "" if s.upper() == "NULL" else s


def parse_co_date(raw) -> str | None:
    """CO dates are 'YYYY-MM-DD HH:MM:SS' (e.g. '2018-03-14 00:00:00') → 'YYYY-MM-DD'."""
    s = _clean(raw)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


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


def contributor_full_name(row: dict) -> str:
    """Best-effort display name from the split CO fields.

    For an individual that is 'Last, First [MI]' (comma form, which the classifier's
    normalize_name swaps); for an org with no first name it is just LastName.
    """
    last = _clean(row.get("LastName"))
    first = _clean(row.get("FirstName"))
    mi = _clean(row.get("MI"))
    fm = " ".join(p for p in (first, mi) if p)
    if last and fm:
        return f"{last}, {fm}"
    return last or fm


def to_classifier_record(row: dict) -> dict:
    return {
        "contributor_name": contributor_full_name(row),
        "contributor_first_name": _clean(row.get("FirstName")),
        "contributor_middle_name": _clean(row.get("MI")),
        "contributor_last_name": _clean(row.get("LastName")),
        "contributor_suffix": _clean(row.get("Suffix")),
        "contributor_employer": _clean(row.get("Employer")),
        "contributor_occupation": _clean(row.get("Occupation")),
        "contributor_city": _clean(row.get("City")),
        "contributor_state": _clean(row.get("State")),
        "contributor_zip": _clean(row.get("Zip")),
    }


def surname_of(row: dict) -> str:
    """LastName lowercased — the surname prefilter funnel."""
    return _clean(row.get("LastName")).lower()


def filing_id_of(row: dict) -> str | None:
    """CO_ID is the recipient committee/filer id (the closest 'filing' anchor)."""
    return _clean(row.get("CO_ID")) or None


def tran_id_of(row: dict) -> str | None:
    """RecordID is CO's native per-contribution id (dedup key)."""
    return _clean(row.get("RecordID")) or None


def recipient_type_of(row: dict) -> str | None:
    """Map CO CommitteeType → the project's coarse recipient_type."""
    t = _clean(row.get("CommitteeType")).lower()
    if not t:
        return None
    if "candidate" in t:
        return "candidate"
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
    jurisdiction: str = "CO",
    source: str = "CO-TRACER",
    discovery_source: str | None = None,
) -> dict:
    iso_date = parse_co_date(row.get("ContributionDate"))
    amount = parse_amount(row.get("ContributionAmount"))
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
        "contributor_name_raw": contributor_full_name(row),
        "contributor_employer_raw": _clean(row.get("Employer")) or None,
        "contributor_occupation_raw": _clean(row.get("Occupation")) or None,
        "contributor_city": _clean(row.get("City")) or None,
        "contributor_state": _clean(row.get("State")) or None,
        "contributor_zip": _clean(row.get("Zip")) or None,
        "recipient_filer_id": recipient_filer_id,
        "recipient_name": recipient_name or _clean(row.get("CommitteeName")),
        "recipient_type": recipient_type or recipient_type_of(row),
        "recipient_party": None,
        "recipient_office": None,
        "amount": amount,
        "date": iso_date,
        "election_cycle": election_cycle_from_date(iso_date),
        "report_type": _clean(row.get("ReceiptType")) or None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

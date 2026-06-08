"""Adapter: Arizona "See The Money" transaction rows → pipeline shapes.

Arizona's campaign-finance portal (seethemoney.az.gov, AZ Secretary of State +
Citizens Clean Elections Commission) is a vendor ASP.NET app with a public JSON
API. Unlike a bulk file, contributions are pulled per CONTRIBUTOR entity via the
"Individuals" view (see scripts/fetch_az.py for the search→prime→detail dance).
Each detail row (one itemized contribution MADE BY the entity) carries:

    PublicTransactionId / TransactionId   two stable unique ids (dedup keys)
    TransactionDate                       .NET "/Date(epoch_ms)/"
    Amount                                contribution amount
    CommitteeName / CommitteeUniqueId     recipient committee (inline)
    CommitteeGroupName                    "Candidates" / "PACs" / … (recipient kind)
    CandidateFirstName/MiddleName/LastName recipient candidate (if a candidate cmte)
    TransactionFirstName/Middle/LastName   the CONTRIBUTOR (last is a messy
                                           "Last, First" string — split on comma)
    TransactionOccupation / TransactionEmployer  occupation + employer (often sparse;
                                           AZ stores them in one combined field on the
                                           web UI but the API splits them back out)
    TransactionCity / State / ZipCode      contributor location

Notes that shape this adapter:
  * Date is a .NET epoch-ms wrapper; the timestamps are AZ local-midnight, so the
    UTC calendar date of the instant is the intended contribution date.
  * The recipient is INLINE on every row, so the resolver is a stateless reader.
  * One person maps to MANY AZ filer entities (a record per name-spelling/cycle);
    the fetcher unions them, and the classifier makes the precise per-row call —
    so a noisy surname search still resolves correctly (e.g. Randy Kendrick vs the
    186 substring "Kendrick" matches).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_DOTNET_DATE = re.compile(r"/Date\((-?\d+)")


def _clean(s) -> str:
    if s is None:
        return ""
    return str(s).strip()


def parse_dotnet_date(raw) -> str | None:
    """'/Date(1256886000000)/' → 'YYYY-MM-DD' (UTC calendar date of the instant).

    AZ stores contribution dates as local-midnight epoch-ms; the UTC date of that
    instant is the intended calendar day.
    """
    s = _clean(raw)
    m = _DOTNET_DATE.search(s)
    if not m:
        return None
    try:
        ms = int(m.group(1))
    except ValueError:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return None


def parse_amount(raw) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(str(raw).replace(",", "").replace("$", ""))
    except ValueError:
        return None


def election_cycle_from_date(iso_date: str | None) -> int | None:
    if not iso_date or len(iso_date) < 4:
        return None
    try:
        return int(iso_date[:4])
    except ValueError:
        return None


def _contributor_last(row: dict) -> str:
    """TransactionLastName is a messy 'Last, First [Middle]' string → take the surname."""
    raw = _clean(row.get("TransactionLastName"))
    if "," in raw:
        return raw.split(",", 1)[0].strip()
    return raw


def contributor_full_name(row: dict) -> str:
    """'Last, First [Middle]' comma form (the classifier swaps it); org → just last."""
    last = _contributor_last(row)
    first = _clean(row.get("TransactionFirstName"))
    middle = _clean(row.get("TransactionMiddleName"))
    fm = " ".join(p for p in (first, middle) if p)
    if last and fm:
        return f"{last}, {fm}"
    return last or fm


def to_classifier_record(row: dict) -> dict:
    return {
        "contributor_name": contributor_full_name(row),
        "contributor_first_name": _clean(row.get("TransactionFirstName")),
        "contributor_middle_name": _clean(row.get("TransactionMiddleName")),
        "contributor_last_name": _contributor_last(row),
        "contributor_suffix": "",
        "contributor_employer": _clean(row.get("TransactionEmployer")),
        "contributor_occupation": _clean(row.get("TransactionOccupation")),
        "contributor_city": _clean(row.get("TransactionCity")),
        "contributor_state": _clean(row.get("TransactionState")),
        "contributor_zip": _clean(row.get("TransactionZipCode")),
    }


def surname_of(row: dict) -> str:
    return _contributor_last(row).lower()


def filing_id_of(row: dict) -> str | None:
    """AZ has no per-filing doc id on the row (ReportId is usually null); the recipient
    committee's stable id is the closest anchor."""
    cid = _clean(row.get("CommitteeUniqueId")) or _clean(row.get("CommitteeId"))
    return cid or None


def tran_id_of(row: dict) -> str | None:
    """PublicTransactionId is AZ's stable per-transaction id (dedup key)."""
    return _clean(row.get("PublicTransactionId")) or _clean(row.get("TransactionId")) or None


def recipient_type_of(row: dict) -> str | None:
    grp = _clean(row.get("CommitteeGroupName")).lower()
    if "candidate" in grp:
        return "candidate"
    if _clean(row.get("CandidateLastName")):
        return "candidate"
    if grp:
        return "committee"
    return None


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
    jurisdiction: str = "AZ",
    source: str = "AZ-SOS",
    discovery_source: str | None = None,
) -> dict:
    iso_date = parse_dotnet_date(row.get("TransactionDate"))
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
        "contributor_name_raw": contributor_full_name(row),
        "contributor_employer_raw": _clean(row.get("TransactionEmployer")) or None,
        "contributor_occupation_raw": _clean(row.get("TransactionOccupation")) or None,
        "contributor_city": _clean(row.get("TransactionCity")) or None,
        "contributor_state": _clean(row.get("TransactionState")) or None,
        "contributor_zip": _clean(row.get("TransactionZipCode")) or None,
        "recipient_filer_id": recipient_filer_id,
        "recipient_name": recipient_name or _clean(row.get("CommitteeName")),
        "recipient_type": recipient_type or recipient_type_of(row),
        "recipient_party": None,
        "recipient_office": None,
        "amount": amount,
        "date": iso_date,
        "election_cycle": election_cycle_from_date(iso_date),
        "report_type": _clean(row.get("TransactionType")) or None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

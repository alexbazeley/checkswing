"""Adapter: Pennsylvania DOS full-export contribution rows → pipeline shapes.

Pennsylvania's Department of State "Full Campaign Finance Export" publishes
comma-delimited, header-bearing files per reporting cycle (one `<YEAR>.zip` at
pa.gov). The contributions file (`contrib_<YEAR>.txt`) columns:

    CampaignFinanceID, FilerID, EYEAR, SubmittedDate, CYCLE, Section, CONTRIBUTOR,
    ADDRESS1, ADDRESS2, CITY, STATE, ZIPCODE, OCCUPATION,
    ENAME, EADDRESS1, EADDRESS2, ECITY, ESTATE, EZIPCODE,
    CONTDATE1, CONTAMT1, CONTDATE2, CONTAMT2, CONTDATE3, CONTAMT3, CONTDESC

(The adapter reads columns by name via DictReader, so the pre-2026 dos.pa.gov
"ECF" column order — which carried an extra FILERCODE — also parses unchanged.)

Notes that shape this adapter:
  * `CONTRIBUTOR` is a single "First [Middle] Last" string (no comma).
  * `ENAME` is the employer name, `OCCUPATION` the occupation — so the two-signal
    CONFIRMED bar is reachable (unlike states with name-only disclosure).
  * A single row can carry up to THREE (date, amount) pairs — the fetcher explodes
    them into separate logical contributions before this adapter sees them, so each
    record here has exactly one `_amount` / `_date`.
  * There is no per-contribution id; the fetcher stamps a content-hash `_tran` for
    stable, idempotent keying.
  * The recipient is the FilerID's filer, resolved from `* ECF Filer.txt`
    (FILERNAME + PARTY + OFFICE) — PA, like WA, carries recipient party.
"""
from __future__ import annotations

import re


def _clean(s) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    return "" if s.upper() == "NULL" else s


def parse_pa_date(raw) -> str | None:
    """PA contribution dates are 'yyyyMMdd' (e.g. 20240315) → ISO 'YYYY-MM-DD'."""
    s = _clean(raw)
    if not s:
        return None
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # Some cycles use ISO already.
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


def to_classifier_record(row: dict) -> dict:
    """Map an exploded PA contribution row → the classifier record dict.

    PA gives one CONTRIBUTOR string; the classifier tokenizes it into first/last.
    Employer = ENAME, occupation = OCCUPATION.
    """
    return {
        "contributor_name": _clean(row.get("CONTRIBUTOR")),
        "contributor_first_name": "",
        "contributor_middle_name": "",
        "contributor_last_name": "",
        "contributor_suffix": "",
        "contributor_employer": _clean(row.get("ENAME")),
        "contributor_occupation": _clean(row.get("OCCUPATION")),
        "contributor_city": _clean(row.get("CITY")),
        "contributor_state": _clean(row.get("STATE")),
        "contributor_zip": _clean(row.get("ZIPCODE")),
    }


def surname_of(row: dict) -> str:
    """Last token of the CONTRIBUTOR string, lowercased — the prefilter funnel."""
    name = _clean(row.get("CONTRIBUTOR"))
    return name.split()[-1].lower() if name else ""


def filing_id_of(row: dict) -> str | None:
    return _clean(row.get("CampaignFinanceID")) or None


def tran_id_of(row: dict) -> str | None:
    """The fetcher stamps a stable content-hash on `_tran` (no native per-row id)."""
    return _clean(row.get("_tran")) or None


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
    jurisdiction: str = "PA",
    source: str = "PA-DOS",
    discovery_source: str | None = None,
) -> dict:
    iso_date = parse_pa_date(row.get("_date"))
    amount = parse_amount(row.get("_amount"))
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
        "contributor_name_raw": _clean(row.get("CONTRIBUTOR")),
        "contributor_employer_raw": _clean(row.get("ENAME")) or None,
        "contributor_occupation_raw": _clean(row.get("OCCUPATION")) or None,
        "contributor_city": _clean(row.get("CITY")) or None,
        "contributor_state": _clean(row.get("STATE")) or None,
        "contributor_zip": _clean(row.get("ZIPCODE")) or None,
        "recipient_filer_id": recipient_filer_id,
        "recipient_name": recipient_name,
        "recipient_type": recipient_type,
        "recipient_party": _clean(row.get("_recipient_party")) or None,
        "recipient_office": _clean(row.get("_recipient_office")) or None,
        "amount": amount,
        "date": iso_date,
        "election_cycle": election_cycle_from_date(iso_date),
        "report_type": None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

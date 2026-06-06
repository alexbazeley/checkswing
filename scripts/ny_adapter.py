"""Adapter: New York State BOE contribution rows → pipeline shapes.

Source: data.ny.gov Socrata dataset `4j2b-6a2j` — "Campaign Finance Disclosure
Reports Contributions: Beginning 1999" (NY State Board of Elections), a public,
queryable SODA API. Relevant fields (one row = one itemized contribution):

    filer_id              recipient committee/candidate filer id
    cand_comm_name        recipient name (committee / candidate) — inline
    trans_number          unique transaction id (dedup key)
    sched_date            contribution date (ISO w/ time)
    org_amt               amount
    cntrbr_type_desc      contributor type (Individual / Corporate / …)
    flng_ent_first_name / _middle_name / _last_name   contributor name
    flng_ent_city / _zip / _country                   contributor address
    election_year         cycle

IMPORTANT — NY does NOT collect contributor employer or occupation, and the dataset
carries no contributor STATE (only city + zip). So the only confirming signals the
classifier can use are:
  * strong_signals.zip_codes  → CONFIRMED (an exact ZIP match — no inference), and
  * (employer/occupation/city_state are unavailable for NY).
We deliberately do NOT derive state from ZIP: a wrong derivation could manufacture a
false city/state match (a misattribution), which GOVERNANCE.md §1.9 forbids. The
honest consequence is that NY rows are CONFIRMED only via a documented strong ZIP
(e.g. Steve Cohen's NYC 10001) and otherwise UNCERTAIN — conservative by design.
"""
from __future__ import annotations

import re


def _clean(s) -> str:
    if s is None:
        return ""
    return str(s).strip()


def parse_ny_date(raw) -> str | None:
    """NY sched_date is ISO with time ('2010-08-11T00:00:00.000') → 'YYYY-MM-DD'."""
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


def election_cycle(row: dict, iso_date: str | None) -> int | None:
    ey = _clean(row.get("election_year"))
    if ey.isdigit():
        return int(ey)
    if iso_date and len(iso_date) >= 4:
        try:
            return int(iso_date[:4])
        except ValueError:
            return None
    return None


def _full_name(row: dict) -> str:
    last = _clean(row.get("flng_ent_last_name"))
    first = _clean(row.get("flng_ent_first_name"))
    middle = _clean(row.get("flng_ent_middle_name"))
    fm = " ".join(p for p in (first, middle) if p)
    if last and fm:
        return f"{last}, {fm}"   # comma form → classifier swaps to "First [Middle] Last"
    return last or fm


def to_classifier_record(row: dict) -> dict:
    # NY carries city + zip but NO state. A city-without-state match can't be
    # verified (every "New York" contributor would collide), and the classifier's
    # address-contradiction rule would otherwise demote even a strong-ZIP match to
    # UNCERTAIN. So NY rows are classified on name + strong-ZIP ONLY: we pass an
    # empty city/state (no unreliable city signal, no false contradiction) and let
    # the documented strong ZIP be the discriminator (exact-ZIP is stronger than a
    # state-less city anyway, and mirrors how the FEDERAL pipeline catches Cohen's
    # NYC giving via ZIP 10001). The real city/zip are still stored on the donation
    # row for display.
    return {
        "contributor_name": _full_name(row),
        "contributor_first_name": _clean(row.get("flng_ent_first_name")),
        "contributor_middle_name": _clean(row.get("flng_ent_middle_name")),
        "contributor_last_name": _clean(row.get("flng_ent_last_name")),
        "contributor_suffix": "",
        "contributor_employer": "",      # NY does not collect employer
        "contributor_occupation": "",    # …or occupation
        "contributor_city": "",          # suppressed for classification (no state to pair it with)
        "contributor_state": "",
        "contributor_zip": _clean(row.get("flng_ent_zip")),
    }


def surname_of(row: dict) -> str:
    return _clean(row.get("flng_ent_last_name")).lower()


def filing_id_of(row: dict) -> str | None:
    return _clean(row.get("filer_id")) or None


def tran_id_of(row: dict) -> str | None:
    return _clean(row.get("trans_number")) or None


def _recipient_type(row: dict) -> str | None:
    name = _clean(row.get("cand_comm_name")).lower()
    if not name:
        return None
    return "candidate" if (" for " in name or name.startswith("friends of")) else "committee"


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
    jurisdiction: str = "NY",
    source: str = "NYSBOE",
    discovery_source: str | None = None,
) -> dict:
    iso_date = parse_ny_date(row.get("sched_date"))
    amount = parse_amount(row.get("org_amt"))
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
        "contributor_name_raw": _full_name(row),
        "contributor_employer_raw": None,
        "contributor_occupation_raw": None,
        "contributor_city": _clean(row.get("flng_ent_city")) or None,
        "contributor_state": None,
        "contributor_zip": _clean(row.get("flng_ent_zip")) or None,
        "recipient_filer_id": recipient_filer_id,
        "recipient_name": recipient_name or _clean(row.get("cand_comm_name")),
        "recipient_type": recipient_type or _recipient_type(row),
        "recipient_party": None,
        "recipient_office": None,
        "amount": amount,
        "date": iso_date,
        "election_cycle": election_cycle(row, iso_date),
        "report_type": _clean(row.get("filing_desc")) or None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

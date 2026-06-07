"""Adapter: Washington PDC contribution rows → pipeline shapes.

Source: data.wa.gov Socrata dataset `kv7h-kjye` — "Contributions to Candidates and
Political Committees" (WA Public Disclosure Commission), a public, queryable SODA API.
Relevant fields (one row = one itemized contribution):

    id                       unique transaction id (dedup key)
    report_number            the filed report (citable filing; → my.pdc.wa.gov doc)
    filer_id / filer_name    recipient committee/candidate (inline)
    office / party           recipient office sought + party (inline)
    type                     recipient type ("Candidate" / "Political Committee")
    receipt_date             contribution date (ISO w/ time)
    amount                   amount
    election_year            cycle
    contributor_name         "LAST FIRST [MIDDLE]" (single field, no comma)
    contributor_city / _state / _zip
    contributor_occupation   occupation (gold-grade — WA itemizes it)
    contributor_employer_name employer (gold-grade)

Unlike NY (ZIP-only), WA carries employer + occupation + contributor state, so the
full two-signal CONFIRMED bar is reachable exactly like CA/PA/TX. The PDC also exposes
a per-record document URL (my.pdc.wa.gov/public/document?repno=<report_number>), so the
dashboard can deep-link the official filing.
"""
from __future__ import annotations

import re


def _clean(s) -> str:
    if s is None:
        return ""
    return str(s).strip()


def parse_wa_date(raw) -> str | None:
    """WA receipt_date is ISO with time ('2017-07-06T00:00:00.000') → 'YYYY-MM-DD'."""
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


def _split_name(raw: str) -> tuple[str, str, str]:
    """WA contributor_name is 'LAST FIRST [MIDDLE]' (no comma) → (last, first, middle)."""
    toks = _clean(raw).split()
    if not toks:
        return "", "", ""
    if len(toks) == 1:
        return toks[0], "", ""
    return toks[0], toks[1], " ".join(toks[2:])


def _composed_name(row: dict) -> str:
    """A 'Last, First Middle' comma-form string → the classifier swaps it to First Last."""
    last, first, middle = _split_name(row.get("contributor_name"))
    fm = " ".join(p for p in (first, middle) if p)
    if last and fm:
        return f"{last}, {fm}"
    return last or fm


def to_classifier_record(row: dict) -> dict:
    last, first, middle = _split_name(row.get("contributor_name"))
    return {
        "contributor_name": _composed_name(row),
        "contributor_first_name": first,
        "contributor_middle_name": middle,
        "contributor_last_name": last,
        "contributor_suffix": "",
        "contributor_employer": _clean(row.get("contributor_employer_name")),
        "contributor_occupation": _clean(row.get("contributor_occupation")),
        "contributor_city": _clean(row.get("contributor_city")),
        "contributor_state": _clean(row.get("contributor_state")),
        "contributor_zip": _clean(row.get("contributor_zip")),
    }


def surname_of(row: dict) -> str:
    """First token of contributor_name lowercased — the 'LAST FIRST' surname funnel."""
    last, _, _ = _split_name(row.get("contributor_name"))
    return last.lower()


def filing_id_of(row: dict) -> str | None:
    return _clean(row.get("report_number")) or None


def tran_id_of(row: dict) -> str | None:
    return _clean(row.get("id")) or None


def _recipient_type(row: dict) -> str | None:
    t = _clean(row.get("type")).lower()
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
    jurisdiction: str = "WA",
    source: str = "WA-PDC",
    discovery_source: str | None = None,
) -> dict:
    iso_date = parse_wa_date(row.get("receipt_date"))
    amount = parse_amount(row.get("amount"))
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
        "contributor_employer_raw": _clean(row.get("contributor_employer_name")) or None,
        "contributor_occupation_raw": _clean(row.get("contributor_occupation")) or None,
        "contributor_city": _clean(row.get("contributor_city")) or None,
        "contributor_state": _clean(row.get("contributor_state")) or None,
        "contributor_zip": _clean(row.get("contributor_zip")) or None,
        "recipient_filer_id": recipient_filer_id,
        "recipient_name": recipient_name or _clean(row.get("filer_name")),
        "recipient_type": recipient_type or _recipient_type(row),
        "recipient_party": _clean(row.get("party")) or None,
        "recipient_office": _clean(row.get("office")) or None,
        "amount": amount,
        "date": iso_date,
        "election_cycle": election_cycle(row, iso_date),
        "report_type": _clean(row.get("origin")) or None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

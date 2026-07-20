"""Adapter: Maryland MDCRIS contribution rows → pipeline shapes.

Source: `api-campaignfinance.maryland.gov/api` — the JSON API behind the Maryland
State Board of Elections' campaign-finance portal (MDCRIS,
campaignfinance.maryland.gov). Public, unauthenticated, refreshed daily.

One row = one itemized contribution. Fields used here (verified against a live
response 2026-07-19, not inferred):

    transactionId / transactionGuid   native ids (transactionGuid is the dedup key)
    reportId / reportGuid / report    the filed report (citable filing)
    committeeName / committeeType     recipient (inline; no separate filer lookup)
    committeeTypeCode                 e.g. SPAC / BALC — maps to recipient_type
    contributorName                   "Last, First [Middle]" (single field)
    contributorType(-Code)            Individual / entity — TIND is an individual
    contributorAddressLine1/2, City, State, ZipCode (ZIP is ZIP+4)
    transactionDate                   ISO with time
    transactionAmount                 amount
    isReturn / isAmended              refund + amendment flags

**MD IS ZIP/ADDRESS-GRADE, NOT GOLD-GRADE — there is no employer or occupation
field, because Maryland does not collect them.** That is the single most important
fact about this source. The two-signal CONFIRMED bar therefore has to be reached
through address/city/ZIP, which puts MD with NY rather than CA/PA/TX/WA — though it
is materially better than NY, because it carries a full street address.

The consequence is specific and documented: `lerner-mark`'s defense against the
Chesapeake Partners "Mark Lerner" doppelgänger is an EMPLOYER negative-signal block
(see owners/lerner-mark.yaml), and MD data cannot feed it. That doppelgänger is live
in MD — ~$31,250 filed from Pikesville 21208 — and is separable here ONLY by address.
This is the FL/Fisher lesson with a named, high-dollar target already in the data, so
strong ZIP signals must be seeded BEFORE ingest and audited immediately after.

Coverage note: the portal's oldest observed record is 2018-03-23, though SBE
describes the database as beginning in 1999. Treat MD coverage as ~2018→present.
"""
from __future__ import annotations

import re


def _clean(s) -> str:
    if s is None:
        return ""
    return str(s).strip()


def parse_md_date(raw) -> str | None:
    """MDCRIS transactionDate is ISO with time ('2022-07-18T00:00:00') → 'YYYY-MM-DD'."""
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
    """Derive the cycle from the contribution date.

    `electionCycleId` exists (e.g. 12023) but is an internal surrogate, not a year,
    so it is deliberately NOT used — the date is the honest source.
    """
    if iso_date and len(iso_date) >= 4:
        try:
            return int(iso_date[:4])
        except ValueError:
            return None
    return None


def normalize_zip(raw) -> str:
    """MD files ZIP+4 ('21201-2435'). Keep the 5-digit prefix for signal matching."""
    s = _clean(raw)
    m = re.match(r"^(\d{5})", s)
    return m.group(1) if m else s


def _split_name(raw: str) -> tuple[str, str, str]:
    """MDCRIS contributorName is 'Last, First [Middle]' → (last, first, middle).

    Falls back to 'First Last' order when there is no comma, which shows up on
    entity contributors and the occasional hand-entered row.
    """
    s = _clean(raw)
    if not s:
        return "", "", ""
    if "," in s:
        last, _, rest = s.partition(",")
        toks = rest.split()
        return last.strip(), (toks[0] if toks else ""), " ".join(toks[1:])
    toks = s.split()
    if len(toks) == 1:
        return toks[0], "", ""
    return toks[-1], toks[0], " ".join(toks[1:-1])


def is_individual(row: dict) -> bool:
    """True when MD classifies the contributor as a person, not an entity.

    `contributorTypeCode` 'TIND' is the individual code. Entity rows (a company,
    LLC, or the team itself) are a different scope question — see CHARTER.md — so
    the flag is exposed rather than silently filtered here.
    """
    code = _clean(row.get("contributorTypeCode")).upper()
    if code:
        return code == "TIND"
    return _clean(row.get("contributorType")).lower() == "individual"


def to_classifier_record(row: dict) -> dict:
    """Project to the shared classifier shape.

    employer/occupation are ALWAYS empty — MD does not collect them. They are
    emitted as empty strings rather than omitted so the classifier's field access
    is uniform across states.
    """
    last, first, middle = _split_name(row.get("contributorName"))
    return {
        "contributor_name": _clean(row.get("contributorName")),
        "contributor_first_name": first,
        "contributor_middle_name": middle,
        "contributor_last_name": last,
        "contributor_suffix": "",
        "contributor_employer": "",      # MD collects neither…
        "contributor_occupation": "",    # …nor this. See module docstring.
        "contributor_city": _clean(row.get("contributorCity")),
        "contributor_state": _clean(row.get("contributorState")),
        "contributor_zip": normalize_zip(row.get("contributorZipCode")),
    }


def surname_of(row: dict) -> str:
    last, _, _ = _split_name(row.get("contributorName"))
    return last.lower()


def filing_id_of(row: dict) -> str | None:
    """The report is the citable filing; prefer the stable GUID."""
    return _clean(row.get("reportGuid")) or _clean(row.get("reportId")) or None


def tran_id_of(row: dict) -> str | None:
    """transactionGuid is stable across report versions; transactionId is not."""
    return _clean(row.get("transactionGuid")) or _clean(row.get("transactionId")) or None


def _recipient_type(row: dict) -> str | None:
    """Map MD's committeeType onto the shared vocabulary.

    STATE_DONATION_SCHEMA vocabulary: candidate | committee | ballot_measure.
    MD's 'Ballot Issue' committees are the ballot_measure case — the same class as
    Fisher's CA Prop 30 giving — so they must not fall through to 'committee'.
    """
    code = _clean(row.get("committeeTypeCode")).upper()
    label = _clean(row.get("committeeType")).lower()
    if code == "BALC" or "ballot" in label:
        return "ballot_measure"
    if "candidate" in label:
        return "candidate"
    return "committee" if (code or label) else None


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
    jurisdiction: str = "MD",
    source: str = "MD-SBE",
    discovery_source: str | None = None,
) -> dict:
    iso_date = parse_md_date(row.get("transactionDate"))
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
        "contributor_name_raw": _clean(row.get("contributorName")),
        "contributor_employer_raw": None,    # MD collects neither
        "contributor_occupation_raw": None,
        "contributor_city": _clean(row.get("contributorCity")) or None,
        "contributor_state": _clean(row.get("contributorState")) or None,
        "contributor_zip": normalize_zip(row.get("contributorZipCode")) or None,
        "recipient_filer_id": recipient_filer_id or _clean(row.get("filingEntityId")) or None,
        "recipient_name": recipient_name or _clean(row.get("committeeName")),
        "recipient_type": recipient_type or _recipient_type(row),
        "recipient_party": None,             # not exposed on the contribution row
        "recipient_office": None,
        "amount": parse_amount(row.get("transactionAmount")),
        "date": iso_date,
        "election_cycle": election_cycle(row, iso_date),
        "report_type": _clean(row.get("report")) or None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

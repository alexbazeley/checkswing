"""Adapter: Minnesota CFB bulk-download contribution rows → pipeline shapes.

The Minnesota Campaign Finance and Public Disclosure Board (cfb.mn.gov) publishes
contributions as plain CSV datasets behind a `?download=<id>` link (no login/API
key). We ingest the broadest individual-contribution dataset, "Contributions
received by all entities — 2015 to present", a single comma-delimited,
header-bearing CSV whose columns (one row = one itemized contribution) are:

    Recipient reg num, Recipient, Recipient type, Recipient subtype, Amount,
    Receipt date, Year, Contributor, Contrib Reg Num, Contrib type, Receipt type,
    In kind?, In-kind descr, Contrib zip, Contrib Employer name

Notes that shape this adapter:
  * `Contributor` is a single "Last, First [Middle]" comma string (the classifier's
    normalize_name swaps the comma form to "First Last"), so — like PA — we pass the
    raw name through and let the classifier tokenize it; the surname prefilter takes
    the part BEFORE the comma (CO/CA split their name into columns; MN does not).
  * MN discloses `Contrib Employer name` and `Contrib zip` but **NO occupation, NO
    city, and NO contributor state** — so the two confirming paths are a documented
    strong employer or a documented strong ZIP (mirrors the NY model, where a strong
    ZIP is the discriminator). With no city/state, the address-contradiction rule
    never fires, which is fine: a same-named relative simply stays UNCERTAIN unless a
    strong signal hits. This matters because "Pohlad" is a large Twin Cities family
    (uncle Jim + a dozen relatives all file) — Joe is separated by his ZIP (55436)
    and Tom by his employer (Carousel Motor Group), both already in their YAMLs.
  * There is **no per-contribution id** in the export; the fetcher stamps a stable
    content-hash `_tran` for idempotent keying + dedup (like PA).
  * The recipient is INLINE on every row (`Recipient` + `Recipient type` +
    `Recipient reg num` is the recipient filer id), so the resolver is a stateless
    reader — no filer-index file (like CO). MN does not disclose recipient party/office.
  * `Receipt date` is already ISO `YYYY-MM-DD`. Returned/negative-amount rows are
    kept as-is (honest archive), netting naturally against the original.
"""
from __future__ import annotations

import re


def _clean(s) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    return "" if s.upper() == "NULL" else s


def parse_mn_date(raw) -> str | None:
    """MN 'Receipt date' is ISO 'YYYY-MM-DD'; accept 'MM/DD/YYYY' defensively."""
    s = _clean(raw)
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
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


def contributor_full_name(row: dict) -> str:
    """The raw 'Contributor' field — already 'Last, First' for individuals."""
    return _clean(row.get("Contributor"))


def surname_of(row: dict) -> str:
    """Surname = the part BEFORE the comma (MN files 'Last, First'); for an org
    contributor with no comma, the last token. Lowercased — the prefilter funnel."""
    name = contributor_full_name(row)
    if not name:
        return ""
    if "," in name:
        return name.split(",")[0].strip().lower()
    return name.split()[-1].lower()


def to_classifier_record(row: dict) -> dict:
    """Map an MN contribution row → the classifier record dict.

    MN gives one 'Last, First' Contributor string; the classifier tokenizes it
    (normalize_name handles the comma form). Employer = 'Contrib Employer name';
    no occupation / city / state are disclosed, so those stay empty (a strong ZIP
    or strong employer is the only confirming path).
    """
    return {
        "contributor_name": contributor_full_name(row),
        "contributor_first_name": "",
        "contributor_middle_name": "",
        "contributor_last_name": "",
        "contributor_suffix": "",
        "contributor_employer": _clean(row.get("Contrib Employer name")),
        "contributor_occupation": "",
        "contributor_city": "",
        "contributor_state": "",
        "contributor_zip": _clean(row.get("Contrib zip")),
    }


def filing_id_of(row: dict) -> str | None:
    """'Recipient reg num' is the recipient committee/filer id (the filing anchor)."""
    return _clean(row.get("Recipient reg num")) or None


def tran_id_of(row: dict) -> str | None:
    """The fetcher stamps a stable content-hash on `_tran` (no native per-row id)."""
    return _clean(row.get("_tran")) or None


def recipient_type_of(row: dict) -> str | None:
    """Map MN 'Recipient type' code → the project's coarse recipient_type.

    PCC = a candidate's principal campaign committee → 'candidate'; everything else
    (PTU party unit, PCF political committee/fund, etc.) → 'committee'.
    """
    t = _clean(row.get("Recipient type")).upper()
    if not t:
        return None
    return "candidate" if t == "PCC" else "committee"


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
    jurisdiction: str = "MN",
    source: str = "MN-CFB",
    discovery_source: str | None = None,
) -> dict:
    iso_date = parse_mn_date(row.get("Receipt date"))
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
        "contributor_employer_raw": _clean(row.get("Contrib Employer name")) or None,
        "contributor_occupation_raw": None,
        "contributor_city": None,
        "contributor_state": None,
        "contributor_zip": _clean(row.get("Contrib zip")) or None,
        "recipient_filer_id": recipient_filer_id,
        "recipient_name": recipient_name or _clean(row.get("Recipient")),
        "recipient_type": recipient_type or recipient_type_of(row),
        "recipient_party": None,
        "recipient_office": None,
        "amount": amount,
        "date": iso_date,
        "election_cycle": election_cycle_from_date(iso_date),
        "report_type": _clean(row.get("Receipt type")) or None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

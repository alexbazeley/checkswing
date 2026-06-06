"""Adapter: Texas Ethics Commission (TEC) bulk-CSV contribution rows → pipeline shapes.

The TEC publishes the entire campaign-finance database as one public bulk zip
(`TEC_CF_CSV.zip`, no login/key). Itemized contributions are split across
`contribs_NN.csv` (plus `cont_ss.csv` / `cont_t.csv`, identical schema). Each
contribution row carries (the columns this adapter reads):

    recordType, reportInfoIdent, filerIdent, filerTypeCd, filerName,
    contributionInfoId, contributionDt, contributionAmount,
    contributorPersentTypeCd, contributorNameOrganization,
    contributorNameLast, contributorNameSuffixCd, contributorNameFirst,
    contributorStreetCity, contributorStreetStateCd, contributorStreetPostalCode,
    contributorEmployer, contributorOccupation, contributorJobTitle

Notes that shape this adapter:
  * Names are PRE-SPLIT (`contributorNameFirst` / `contributorNameLast`), unlike
    PA's single string — so we populate first/middle/last directly. `…First`
    bundles the middle initial ("JAMES H."), so we split off the first token as
    the first name and keep the remainder as the middle (preserves the middle-
    initial discriminator the federal classifier relies on).
  * `contributorEmployer` + `contributorOccupation` are present — so the two-signal
    CONFIRMED bar is reachable (TEC is a gold-grade portal, like CA/PA).
  * `contributionInfoId` is a NATIVE per-contribution id and `reportInfoIdent` the
    filing — so keying needs no content-hash (unlike PA).
  * The recipient is INLINE on the contribution row (`filerName` / `filerTypeCd`);
    `filers.csv` enriches it with the office sought. TEC filer records carry no
    party, so recipient_party stays NULL.
"""
from __future__ import annotations

import re


def _clean(s) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    return "" if s.upper() == "NULL" else s


def parse_tx_date(raw) -> str | None:
    """TEC contribution dates are 'yyyyMMdd' (e.g. 20240315) → ISO 'YYYY-MM-DD'."""
    s = _clean(raw)
    if not s:
        return None
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
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


def _split_first_middle(first_raw: str) -> tuple[str, str]:
    """'JAMES H.' → ('JAMES', 'H.'); 'MARY' → ('MARY', '')."""
    toks = _clean(first_raw).split()
    if not toks:
        return "", ""
    return toks[0], " ".join(toks[1:])


def _composed_name(row: dict) -> str:
    """A 'First [Middle] Last [Suffix]' string for the classifier's name match.

    Falls back to the organization name for entity contributors (which won't match
    a personal owner's name_variants — correct: corporate giving isn't auto-attributed)."""
    first = _clean(row.get("contributorNameFirst"))
    last = _clean(row.get("contributorNameLast"))
    suffix = _clean(row.get("contributorNameSuffixCd"))
    parts = [p for p in (first, last, suffix) if p]
    if parts:
        return " ".join(parts)
    return _clean(row.get("contributorNameOrganization"))


def to_classifier_record(row: dict) -> dict:
    """Map a TEC contribution row → the classifier record dict."""
    first, middle = _split_first_middle(row.get("contributorNameFirst"))
    return {
        "contributor_name": _composed_name(row),
        "contributor_first_name": first,
        "contributor_middle_name": middle,
        "contributor_last_name": _clean(row.get("contributorNameLast")),
        "contributor_suffix": _clean(row.get("contributorNameSuffixCd")),
        "contributor_employer": _clean(row.get("contributorEmployer")),
        "contributor_occupation": _clean(row.get("contributorOccupation"))
        or _clean(row.get("contributorJobTitle")),
        "contributor_city": _clean(row.get("contributorStreetCity")),
        "contributor_state": _clean(row.get("contributorStreetStateCd")),
        "contributor_zip": _clean(row.get("contributorStreetPostalCode")),
    }


def surname_of(row: dict) -> str:
    """contributorNameLast lowercased — the prefilter funnel (matches PA/CA shape)."""
    return _clean(row.get("contributorNameLast")).lower()


def filing_id_of(row: dict) -> str | None:
    return _clean(row.get("reportInfoIdent")) or None


def tran_id_of(row: dict) -> str | None:
    return _clean(row.get("contributionInfoId")) or None


def recipient_type_of(filer_type_cd: str) -> str | None:
    """Map a TEC filerTypeCd → recipient_type vocabulary (candidate/committee)."""
    code = _clean(filer_type_cd).upper()
    if not code:
        return None
    if "COH" in code:          # COH / JCOH — candidate/officeholder
        return "candidate"
    if "PAC" in code or "CEC" in code or "PTY" in code:
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
    jurisdiction: str = "TX",
    source: str = "TEC",
    discovery_source: str | None = None,
) -> dict:
    iso_date = parse_tx_date(row.get("contributionDt"))
    amount = parse_amount(row.get("contributionAmount"))
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
        "contributor_employer_raw": _clean(row.get("contributorEmployer")) or None,
        "contributor_occupation_raw": (
            _clean(row.get("contributorOccupation")) or _clean(row.get("contributorJobTitle"))
        ) or None,
        "contributor_city": _clean(row.get("contributorStreetCity")) or None,
        "contributor_state": _clean(row.get("contributorStreetStateCd")) or None,
        "contributor_zip": _clean(row.get("contributorStreetPostalCode")) or None,
        "recipient_filer_id": recipient_filer_id,
        "recipient_name": recipient_name,
        "recipient_type": recipient_type,
        "recipient_party": None,    # TEC filer records carry no party
        "recipient_office": _clean(row.get("_recipient_office")) or None,
        "amount": amount,
        "date": iso_date,
        "election_cycle": election_cycle_from_date(iso_date),
        "report_type": _clean(row.get("formTypeCd")) or None,
        "raw_payload_path": raw_payload_path,
        "ingested_at": ingested_at,
    }

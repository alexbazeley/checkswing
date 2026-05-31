"""Classifier — the spec is VERIFICATION.md, this is the implementation.

Inputs: a raw FEC record + an owner YAML (parsed dict).
Outputs: a Classification with status, reason, signals_matched, attribution slug.

The classifier never modifies the owner YAML. It only reads.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence


CONFIRMED = "CONFIRMED"
PROBABLE = "PROBABLE"
UNCERTAIN = "UNCERTAIN"
# Not a classifier verdict — a manual_attributions override value (schema v7).
# A txn carrying an EXCLUDED override is dropped from an owner's classification
# entirely (the documented-human-decision negative of a CONFIRMED override): the
# record is NOT this owner and no signal can separate them from a same-named
# relative. Handled in ingest.classify loop; never produced by classify().
EXCLUDED = "EXCLUDED"

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "rev", "hon"}


@dataclass
class Classification:
    status: str
    status_reason: str
    signals_matched: list[str]
    entity_slug: str
    entity_kind: str  # "owner" | "spouse" | "child" | "parent" | "sibling" | "pac" | "business_entity"
    parent_owner_slug: str | None = None
    name_matched_variant: str | None = None


# ─── Name normalization ─────────────────────────────────────────────────────


def _strip_diacritics_simple(s: str) -> str:
    # The FEC data is ASCII in practice; we keep this simple.
    return s


def normalize_name(raw: str) -> tuple[set[str], str | None, frozenset[str]]:
    """Return (canonical_forms, suffix, middle_initials).

    Sequence:
      - Detect "Last, First" form (comma present) and rewrite to "First Last".
      - Lowercase, strip `.` and `,`, collapse whitespace.
      - Strip honorifics (mr/mrs/ms/miss/dr/prof/rev/hon) from any token position.
      - Identify trailing suffix (jr/sr/ii/iii/iv/v) and return separately.
      - Drop middle-initial tokens (single-char tokens between first and last)
        from the canonical forms so "Steven A Cohen" and "Steven Cohen" share a
        form — but ALSO return those initials separately as `middle_initials`
        so callers can use them as a discriminator (see `names_match`).
      - For hyphenated last names, generate both orderings.

    Returns:
      - `forms`: a SET of canonical first+last forms (to handle hyphenated
        swaps). Middle initials are stripped from these so matching stays
        suffix- and middle-agnostic at the form level.
      - `suffix`: the suffix token (jr/sr/ii/iii/iv/v) or None.
      - `middle_initials`: a frozenset of the single-char middle tokens. Empty
        when the name carries no middle initial. These are NOT in `forms`; they
        let `names_match` distinguish "John P." from "John S." while still
        treating a bare "John" as compatible with either (the VERIFICATION.md
        optional-middle rule).
    """
    if not raw:
        return (set(), None, frozenset())
    name = raw.strip()
    had_comma = "," in name
    if had_comma:
        left, _, right = name.partition(",")
        name = f"{right.strip()} {left.strip()}"
    name = name.lower()
    name = name.replace(".", " ").replace(",", " ")
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return (set(), None, frozenset())
    tokens = name.split()

    # Strip honorifics from any position. People file "Mr." inconsistently
    # (sometimes leading, sometimes after the last name in Last-First form).
    tokens = [t for t in tokens if t not in HONORIFICS]
    if not tokens:
        return (set(), None, frozenset())

    # Detect a suffix token anywhere in the stream, not just trailing. The
    # "Last, First Suffix" comma form (the dominant FEC format) becomes
    # "First Suffix Last" after the comma-swap above, leaving the suffix
    # mid-string (e.g. "william o jr dewitt"). Multi-char suffixes
    # (jr/sr/ii/iii/iv) are unambiguous in any position; bare "v"/"i" are only
    # treated as a suffix when trailing, since mid-string they are middle
    # initials (and get dropped below). Without this, suffixed owners filing in
    # comma form fail to match or lose the Jr./III disambiguator entirely.
    suffix: str | None = None
    kept: list[str] = []
    last_idx = len(tokens) - 1
    for i, t in enumerate(tokens):
        if t in SUFFIXES and (i == last_idx or len(t) > 1):
            suffix = t  # last suffix wins if (rarely) more than one appears
            continue
        kept.append(t)
    tokens = kept
    if not tokens:
        return (set(), suffix, frozenset())

    # Separate single-char middle tokens (initials) from the canonical form:
    # they are dropped from `forms` (so "Steven A Cohen" and "Steven Cohen"
    # share a form) but captured in `middle_initials` so callers can use them
    # as a discriminator. Multi-char middle NAMES stay in the form (they
    # already discriminate by not sharing a form) and are not initials.
    middle_initials: frozenset[str] = frozenset()
    if len(tokens) > 2:
        first = tokens[0]
        last = tokens[-1]
        middle_initials = frozenset(t for t in tokens[1:-1] if len(t) == 1)
        middle = [t for t in tokens[1:-1] if len(t) > 1]
        tokens = [first] + middle + [last]

    forms = {" ".join(tokens)}

    # Hyphenated last-name swap variants.
    last = tokens[-1]
    if "-" in last:
        parts = [p for p in last.split("-") if p]
        if len(parts) >= 2:
            forms.add(" ".join(tokens[:-1] + ["-".join(reversed(parts))]))
            forms.add(" ".join(tokens[:-1] + parts))
            forms.add(" ".join(tokens[:-1] + list(reversed(parts))))

    return (forms, suffix, middle_initials)


def _middle_initials_compatible(rec_mid: frozenset[str], v_mid: frozenset[str]) -> bool:
    """Optional-but-discriminating middle-initial rule (VERIFICATION.md).

    A record and a variant are middle-initial compatible UNLESS both carry a
    single-char middle initial and those initials are disjoint:
      - either side empty  → compatible (a bare "John" matches "John P." and
        "John S." alike; this preserves the optional-middle behavior).
      - both present + share an initial → compatible ("John P" vs "John P Q").
      - both present + disjoint → INCOMPATIBLE ("John P" vs "John S" — a
        father/son or doppelgänger split the name alone cannot separate).
    """
    if not rec_mid or not v_mid:
        return True
    return bool(rec_mid & v_mid)


def names_match(record_name: str, variants: Sequence[str]) -> tuple[bool, bool, str | None]:
    """Match record_name against any of the variants.

    Returns (canonical_match, suffix_match, matched_variant).
      canonical_match: True if any variant shares a canonical form (suffix-agnostic)
                       AND has compatible middle initials (see
                       `_middle_initials_compatible`)
      suffix_match:    True if at least one matching variant ALSO shares the suffix
      matched_variant: the first variant whose canonical form matched (None if no match)

    The two-flag return lets callers distinguish "no match → skip" from "name
    canonically matches but suffix differs → UNCERTAIN per VERIFICATION.md".

    A variant whose middle initial CONFLICTS with the record's (e.g. variant
    "John S." vs record "John P.") is skipped as if it did not match — this is
    what lets a same-named relative be routed to their own entity rather than
    misattributed. A bare variant ("John") with no middle initial still matches
    a record that carries one, so the optional-middle rule is preserved.
    """
    rec_forms, rec_suffix, rec_mid = normalize_name(record_name)
    if not rec_forms:
        return (False, False, None)

    canonical_hit: str | None = None
    suffix_hit = False
    for v in variants:
        v_forms, v_suffix, v_mid = normalize_name(v)
        if rec_forms & v_forms and _middle_initials_compatible(rec_mid, v_mid):
            if canonical_hit is None:
                canonical_hit = v
            if rec_suffix == v_suffix:
                suffix_hit = True
                # Prefer the variant that fully matches.
                canonical_hit = v
                break
    return (canonical_hit is not None, suffix_hit, canonical_hit)


def names_match_with_fallback(
    record_name: str,
    synthetic_name: str,
    variants: Sequence[str],
) -> tuple[bool, bool, str | None]:
    """Try the literal contributor_name; if no canonical match, fall back to
    a synthetic "First Middle Last" built from FEC's structured fields.

    Handles the FEC pattern where `contributor_name` is ambiguously ordered
    (e.g., "HENRY JOHN W." with no comma) but the structured first/middle/last
    fields are present and unambiguous.
    """
    canon, suf, v = names_match(record_name, variants)
    if canon:
        return canon, suf, v
    if synthetic_name:
        return names_match(synthetic_name, variants)
    return False, False, None


# ─── Signal matchers ────────────────────────────────────────────────────────


def _norm(s) -> str:
    """Normalize a string for case-insensitive substring matching.

    Steps: lowercase → replace `.` and `,` with spaces → collapse whitespace.
    Periods and commas are stripped (replaced with space, then collapsed)
    because they are typographic, not semantic — "John W. Henry and Company"
    and "JOHN W HENRY AND COMPANY INC" should match. This is symmetric with
    name normalization, which has done the same since the classifier shipped.

    Does NOT do stemming, word removal, or `&` ↔ "and" substitution — the spec
    (VERIFICATION.md) forbids those, and they would create lossy matches
    (e.g., "Point72" matching "Point Park University" because both contain "Point").
    """
    if not isinstance(s, str):
        return ""
    s = s.lower().replace(".", " ").replace(",", " ")
    return re.sub(r"\s+", " ", s).strip()


def employer_match(record_employer: str | None, signal_list: Sequence[str]) -> list[str]:
    """Return signal strings that matched (case-insensitive substring).

    Match rule per VERIFICATION.md: signal_string must be a substring of
    record_employer. No stemming, no word removal.
    """
    if not record_employer:
        return []
    hay = _norm(record_employer)
    hits = []
    for sig in signal_list or []:
        needle = _norm(sig)
        if needle and needle in hay:
            hits.append(sig)
    return hits


def occupation_match(record_occupation: str | None, signal_list: Sequence[str]) -> list[str]:
    return employer_match(record_occupation, signal_list)


def city_state_match(
    record_city: str | None,
    record_state: str | None,
    signal_cities: Sequence[str],
    signal_states: Sequence[str],
) -> str | None:
    """City+state is a single confirming signal — BOTH must match."""
    if not record_city or not record_state:
        return None
    city_lc = _norm(record_city)
    state = (record_state or "").strip().upper()
    cities_lc = {_norm(c) for c in (signal_cities or [])}
    states = {(s or "").strip().upper() for s in (signal_states or [])}
    if city_lc in cities_lc and state in states:
        return f"{city_lc}/{state}"
    return None


def zip_match(record_zip: str | None, signal_zips: Sequence[str]) -> str | None:
    if not record_zip:
        return None
    rec = str(record_zip).strip()[:5]  # ZIP+4 → ZIP5 for comparison
    for z in signal_zips or []:
        if rec == str(z).strip()[:5]:
            return rec
    return None


def address_contradicts(
    record_city: str | None,
    record_state: str | None,
    signal_cities: Sequence[str],
    signal_states: Sequence[str],
) -> bool:
    """True iff the record's address contradicts our documented residences.

    City+state is the unit (mirrors `city_state_match`): when the record has
    BOTH a city and a state, the address contradicts unless that (city, state)
    pair is a documented residence — so a same-named city in the wrong state
    (e.g. Greenwich, KS for a Greenwich, CT owner) is correctly flagged rather
    than slipping through on a generic employer match. When the record has a
    city but no state, fall back to city-only membership (no regression).

    Per VERIFICATION.md this catches family-name collisions where the employer
    might still match but the donor is a relative elsewhere. The YAML's
    `verifying_signals.cities`/`.states` ARE the documented residence set; widen
    them deliberately (with a change_log entry), not silently here.
    """
    if not record_city:
        return False  # No city to contradict.
    if record_state:
        # Pair must be a documented residence, else contradiction.
        return city_state_match(record_city, record_state, signal_cities, signal_states) is None
    city_lc = _norm(record_city)
    cities_lc = {_norm(c) for c in (signal_cities or [])}
    return city_lc not in cities_lc


# ─── Classification ─────────────────────────────────────────────────────────


def _get_record_fields(record: dict) -> dict:
    # FEC schedule_a sometimes records the name in an ambiguous order in
    # `contributor_name` (e.g., "HENRY JOHN W." with no comma — Last First
    # MiddleInitial). For those cases, FEC's structured fields
    # contributor_first_name / contributor_middle_name / contributor_last_name
    # disambiguate. We build a "First Middle Last" synthetic alongside the
    # raw name and let names_match try both.
    fn = (record.get("contributor_first_name") or "").strip()
    mn = (record.get("contributor_middle_name") or "").strip()
    ln = (record.get("contributor_last_name") or "").strip()
    # Include contributor_suffix so the synthetic carries Jr./Sr./II–IV — without
    # it, a suffixed record collapses to a no-suffix synthetic that trips the
    # suffix-mismatch demotion against a suffix-bearing variant.
    sfx = (record.get("contributor_suffix") or "").strip()
    synthetic = " ".join(p for p in (fn, mn, ln, sfx) if p)
    return {
        "name": record.get("contributor_name") or "",
        "name_synthetic": synthetic,
        "employer": record.get("contributor_employer") or "",
        "occupation": record.get("contributor_occupation") or "",
        "city": record.get("contributor_city") or "",
        "state": record.get("contributor_state") or "",
        "zip": record.get("contributor_zip") or "",
    }


def _classify_against_entity_signals(
    rf: dict,
    verifying_signals: dict,
    strong_signals: dict,
    negative_signals: dict | None = None,
) -> tuple[str, str, list[str]]:
    vs_cities = verifying_signals.get("cities") or []
    vs_states = verifying_signals.get("states") or []
    vs_employers = verifying_signals.get("employers") or []
    vs_occupations = verifying_signals.get("occupations") or []
    ss_employers = strong_signals.get("employers") or []
    ss_zips = strong_signals.get("zip_codes") or []
    ns_employers = (negative_signals or {}).get("employers") or []

    # Negative employer signal (anti-pattern). Per VERIFICATION.md, a match
    # against negative_signals.employers demotes to UNCERTAIN regardless of
    # any other signals. This catches same-name doppelgängers that the
    # operator has manually identified via review.
    negative_hits = employer_match(rf["employer"], ns_employers)
    if negative_hits:
        return (
            UNCERTAIN,
            f"matches negative employer signal: {', '.join(negative_hits)}",
            [f"negative_employer:{h}" for h in negative_hits],
        )

    strong_emp_hits = employer_match(rf["employer"], ss_employers)
    strong_zip_hit = zip_match(rf["zip"], ss_zips)
    strong_signals_matched = [f"strong_employer:{h}" for h in strong_emp_hits]
    if strong_zip_hit:
        strong_signals_matched.append(f"strong_zip:{strong_zip_hit}")

    confirming_signals_matched: list[str] = []
    emp_hits = employer_match(rf["employer"], vs_employers)
    confirming_signals_matched.extend(f"employer:{h}" for h in emp_hits)
    occ_hits = occupation_match(rf["occupation"], vs_occupations)
    confirming_signals_matched.extend(f"occupation:{h}" for h in occ_hits)
    cs_hit = city_state_match(rf["city"], rf["state"], vs_cities, vs_states)
    if cs_hit:
        confirming_signals_matched.append(f"city_state:{cs_hit}")

    # Address contradiction: city is filled but not in our documented list.
    # Demotes to UNCERTAIN regardless of other signals (VERIFICATION.md
    # negative-signal rule). This catches family-name collisions.
    if address_contradicts(rf["city"], rf["state"], vs_cities, vs_states):
        all_signals = strong_signals_matched + confirming_signals_matched
        return (
            UNCERTAIN,
            f"city/state outside documented residences (city={rf['city']!r}, state={rf['state']!r})",
            all_signals,
        )

    all_signals = strong_signals_matched + confirming_signals_matched

    if strong_signals_matched:
        return (
            CONFIRMED,
            f"strong signal: {', '.join(strong_signals_matched)}",
            all_signals,
        )
    if len(confirming_signals_matched) >= 2:
        return (
            CONFIRMED,
            f"two confirming signals: {', '.join(confirming_signals_matched)}",
            all_signals,
        )
    if len(confirming_signals_matched) == 1:
        return (
            PROBABLE,
            f"one confirming signal: {confirming_signals_matched[0]}",
            all_signals,
        )
    return (UNCERTAIN, "name match only — no confirming signals", all_signals)


def classify(
    record: dict,
    owner: dict,
    *,
    process_related_entities: bool = False,
) -> Classification | None:
    """Classify one FEC record against one owner YAML.

    Returns:
      - None if the record's name does not match the owner's name_variants
        AT ALL (canonical-form check). The record is filtered out — never
        enters the DB or the review queue.
      - Classification(status=UNCERTAIN) if the name canonically matches but
        the suffix mismatches, OR if address contradicts, OR if name matches
        but no confirming signal hits.
      - Classification(status=PROBABLE) for exactly one confirming signal.
      - Classification(status=CONFIRMED) for ≥2 confirming signals or any
        strong signal.

    Related-entity routing:
      - A record routes to a related_entity only on EXACT suffix agreement
        (canonical name match AND matching suffix). With process_related_entities
        =True it is classified against that entity; with =False it is dropped
        (out of scope this run) per the spouse-collision rule.
      - A canonical match with a DIFFERING suffix (e.g. owner "John Smith Sr"
        vs related "John Smith Jr") is NOT that entity — it falls through to the
        owner check, so the owner claims its own row instead of being misrouted
        to a Jr/Sr relative.
    """
    rf = _get_record_fields(record)
    if not rf["name"]:
        return None

    # ── Related-entity name check first (spouse-collision rule) ────────────
    related = owner.get("related_entities") or []
    for ent in related:
        if not isinstance(ent, dict):
            continue
        ent_variants = ent.get("name_variants") or []
        if not ent_variants:
            continue
        canon, suffix_ok, matched_v = names_match_with_fallback(
            rf["name"], rf.get("name_synthetic", ""), ent_variants
        )
        if not (canon and suffix_ok):
            # No match, or canonical match with a different suffix — not this
            # entity. Fall through to the owner check.
            continue
        # Name + suffix match this related entity.
        if not process_related_entities:
            # Principals-only mode — out of scope this run (we are not tracking
            # the relative yet).
            return None
        status, reason, sigs = _classify_against_entity_signals(
            rf,
            ent.get("verifying_signals") or {},
            ent.get("strong_signals") or {},
            ent.get("negative_signals") or {},
        )
        return Classification(
            status=status,
            status_reason=reason,
            signals_matched=sigs,
            entity_slug=ent.get("slug") or "",
            entity_kind=ent.get("kind") or "spouse",
            parent_owner_slug=owner.get("slug"),
            name_matched_variant=matched_v,
        )

    # ── Owner name match ───────────────────────────────────────────────────
    owner_variants = owner.get("name_variants") or []
    canon, suffix_ok, matched_variant = names_match_with_fallback(
        rf["name"], rf.get("name_synthetic", ""), owner_variants
    )
    if not canon:
        return None

    if not suffix_ok:
        # Suffix mismatch ⇒ UNCERTAIN regardless of other signals.
        return Classification(
            status=UNCERTAIN,
            status_reason="suffix mismatch (name canonically matches but suffix differs from variants)",
            signals_matched=[],
            entity_slug=owner.get("slug") or "",
            entity_kind="owner",
            parent_owner_slug=None,
            name_matched_variant=matched_variant,
        )

    status, reason, sigs = _classify_against_entity_signals(
        rf,
        owner.get("verifying_signals") or {},
        owner.get("strong_signals") or {},
        owner.get("negative_signals") or {},
    )
    return Classification(
        status=status,
        status_reason=reason,
        signals_matched=sigs,
        entity_slug=owner.get("slug") or "",
        entity_kind="owner",
        parent_owner_slug=None,
        name_matched_variant=matched_variant,
    )

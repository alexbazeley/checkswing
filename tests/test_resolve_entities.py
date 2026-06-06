"""Classifier tests — VERIFICATION.md is the spec."""
from __future__ import annotations

import pytest

from scripts.resolve_entities import (
    CONFIRMED,
    PROBABLE,
    UNCERTAIN,
    classify,
    employer_match,
    names_match,
    normalize_name,
)


# ─── Fixture owner (Cohen-like) ─────────────────────────────────────────────


@pytest.fixture
def owner():
    """Cohen-shaped owner. Variants intentionally vary in middle-initial and
    Last/First format to cover normalization."""
    return {
        "slug": "cohen-steven",
        "name": "Steven A. Cohen",
        "name_variants": [
            "Steven A Cohen",
            "Steven A. Cohen",
            "Steven Cohen",
            "Steve Cohen",
            "Cohen, Steven",
            "Cohen, Steven A",
        ],
        "verifying_signals": {
            "cities": ["greenwich", "stamford", "new york"],
            "states": ["CT", "NY"],
            "employers": ["Point72", "SAC Capital", "S.A.C. Capital", "New York Mets"],
            "occupations": ["investor", "owner", "principal", "ceo", "founder"],
        },
        "strong_signals": {
            "employers": ["Cohen Private Ventures", "Point72 Asset Management"],
            "zip_codes": [],
        },
        "negative_signals": {
            "employers": ["Elliott Management", "Elliott Mgmt"],
        },
        "related_entities": [
            {
                "kind": "spouse",
                "slug": "cohen-alexandra",
                "name": "Alexandra M. Cohen",
                "name_variants": [
                    "Alexandra Cohen",
                    "Alexandra M Cohen",
                    "Cohen, Alexandra",
                    "Cohen, Alexandra M",
                ],
                "verifying_signals": {
                    "cities": ["greenwich", "new york"],
                    "states": ["CT", "NY"],
                    "employers": ["Steven and Alexandra Cohen Foundation"],
                    "occupations": ["philanthropist", "homemaker"],
                },
                "strong_signals": {
                    "employers": ["Steven and Alexandra Cohen Foundation"],
                    "zip_codes": [],
                },
            }
        ],
    }


# ─── Normalization tests ────────────────────────────────────────────────────


class TestNormalization:
    def test_lowercase_and_strip_periods(self):
        forms, suffix, mids = normalize_name("Steven A. Cohen")
        assert "steven cohen" in forms
        assert suffix is None
        assert mids == frozenset({"a"})  # initial captured, not in forms

    def test_strip_middle_initial(self):
        forms_with, _, mids_with = normalize_name("Steven A Cohen")
        forms_no, _, mids_no = normalize_name("Steven Cohen")
        assert forms_with & forms_no  # share a canonical form
        assert mids_with == frozenset({"a"})  # initial surfaced separately
        assert mids_no == frozenset()  # no middle initial

    def test_last_comma_first_format(self):
        forms_lf, _, _ = normalize_name("Cohen, Steven A")
        forms_fl, _, _ = normalize_name("Steven A Cohen")
        assert forms_lf & forms_fl

    def test_suffix_extracted(self):
        _, suffix, _ = normalize_name("John W. Henry Jr.")
        assert suffix == "jr"
        _, suffix2, _ = normalize_name("John W. Henry")
        assert suffix2 is None

    def test_suffix_makes_canon_match_but_distinct(self):
        forms_jr, suf_jr, _ = normalize_name("John Henry Jr")
        forms_no, suf_no, _ = normalize_name("John Henry")
        # Same canonical form, but suffixes differ.
        assert forms_jr & forms_no
        assert suf_jr != suf_no

    def test_hyphenated_last_name_swap(self):
        forms_a, _, _ = normalize_name("Mary Smith-Jones")
        forms_b, _, _ = normalize_name("Mary Jones-Smith")
        assert forms_a & forms_b

    def test_hyphenated_last_name_unhyphenated(self):
        forms_hyp, _, _ = normalize_name("Mary Smith-Jones")
        forms_two, _, _ = normalize_name("Mary Smith Jones")
        assert forms_hyp & forms_two

    def test_multi_char_middle_name_is_not_an_initial(self):
        # A spelled-out middle name stays in the form and is NOT a middle
        # initial — it already discriminates by not sharing a bare form.
        forms, _, mids = normalize_name("John Powers Middleton")
        assert "john powers middleton" in forms
        assert mids == frozenset()


class TestNamesMatch:
    def test_exact_match(self):
        canon, suf, v = names_match("Steven Cohen", ["Steven Cohen"])
        assert canon and suf and v == "Steven Cohen"

    def test_case_insensitive(self):
        canon, suf, _ = names_match("STEVEN COHEN", ["steven cohen"])
        assert canon and suf

    def test_no_match(self):
        canon, _, _ = names_match("Alexandra Cohen", ["Steven Cohen", "Steve Cohen"])
        assert not canon

    def test_canonical_match_but_suffix_mismatch(self):
        canon, suf, _ = names_match("Steven Cohen Jr", ["Steven Cohen", "Steve Cohen"])
        assert canon  # name canonically matches
        assert not suf  # but suffix differs

    def test_honorific_mr_stripped(self):
        # FEC sometimes records "HENRY, JOHN W MR." — the "MR." honorific
        # should be stripped during normalization so the canonical form
        # matches "John W. Henry" variants.
        canon, suf, _ = names_match("HENRY, JOHN W MR.", ["John W. Henry"])
        assert canon and suf

    def test_honorific_dr_stripped(self):
        canon, _, _ = names_match("Dr. Jane Doe", ["Jane Doe"])
        assert canon

    def test_honorific_mrs_stripped(self):
        canon, _, _ = names_match("Mrs. Mary Smith", ["Mary Smith"])
        assert canon


class TestMiddleInitialDiscrimination:
    """The middle-initial rule: optional when either side lacks one, but a
    discriminator when both are present (VERIFICATION.md)."""

    def test_conflicting_middle_initials_do_not_match(self):
        # The motivating case: father John S. vs son John P. — same first+last,
        # different middle initial → NOT a match. This is what lets the son be
        # routed to his own entity instead of misattributed to the father.
        canon, suf, _ = names_match("MIDDLETON, JOHN P", ["John S Middleton"])
        assert not canon

    def test_shared_middle_initial_matches(self):
        canon, suf, _ = names_match("MIDDLETON, JOHN S", ["John S Middleton"])
        assert canon and suf

    def test_bare_record_matches_middle_initial_variant(self):
        # Optional rule, one direction: a record with no middle initial still
        # matches a variant that has one.
        canon, _, _ = names_match("John Middleton", ["John S Middleton"])
        assert canon

    def test_middle_initial_record_matches_bare_variant(self):
        # Optional rule, other direction: "Steven A Cohen" still matches the
        # bare "Steven Cohen" variant (preserves the pre-change behavior).
        canon, _, _ = names_match("Steven A Cohen", ["Steven Cohen"])
        assert canon

    def test_conflicting_middle_falls_through_to_bare_variant(self):
        # Safety mechanism: an owner with BOTH a middle-initial variant and a
        # bare variant still claims a record whose initial conflicts with the
        # specific variant — it matches via the bare one. This is why owners
        # carrying a bare variant lose nothing under the new rule.
        canon, _, v = names_match("Steven B Cohen", ["Steven A Cohen", "Steven Cohen"])
        assert canon
        assert v == "Steven Cohen"  # matched via the bare variant, not "A"

    def test_disjoint_when_only_variant_has_initial_is_compatible(self):
        canon, _, _ = names_match("John Smith", ["John Q Smith"])
        assert canon  # variant initial, record none → optional → match


# ─── employer_match (substring rule) ────────────────────────────────────────


class TestEmployerMatch:
    def test_signal_is_substring_of_donor_employer(self):
        hits = employer_match("Cohen Private Ventures LLC", ["Cohen Private Ventures"])
        assert hits == ["Cohen Private Ventures"]

    def test_donor_substring_of_signal_does_NOT_match(self):
        # "Private Ventures" is a substring of "Cohen Private Ventures" but
        # the rule is signal-in-donor, not the reverse.
        hits = employer_match("Private Ventures", ["Cohen Private Ventures"])
        assert hits == []

    def test_case_insensitive(self):
        hits = employer_match("POINT72 ASSET MANAGEMENT LLC", ["Point72"])
        assert hits == ["Point72"]

    def test_no_stemming_unrelated_words(self):
        # "Point72" should NOT match "Point Park University" — VERIFICATION.md
        # anti-pattern. With our substring rule, lc("Point72") = "point72",
        # which is NOT a substring of "point park university". ✓
        hits = employer_match("Point Park University", ["Point72"])
        assert hits == []

    def test_empty_employer(self):
        assert employer_match("", ["Point72"]) == []
        assert employer_match(None, ["Point72"]) == []

    def test_period_stripped_in_employer_normalization(self):
        # Henry-class bug: signal "John W. Henry and Company" must match
        # record "JOHN W HENRY AND COMPANY INC" even though one has a period
        # and the other doesn't. Periods are typographic, not semantic.
        hits = employer_match(
            "JOHN W HENRY AND COMPANY INC", ["John W. Henry and Company"]
        )
        assert hits == ["John W. Henry and Company"]

    def test_comma_stripped_in_employer_normalization(self):
        # Commas in employer strings (e.g., "JOHN W. HENRY & COMPANY, INC.")
        # should also normalize away.
        hits = employer_match(
            "JOHN W. HENRY & COMPANY, INC.", ["John W Henry & Company"]
        )
        assert hits == ["John W Henry & Company"]

    def test_period_normalization_does_not_create_spurious_matches(self):
        # The Point72 / Point Park anti-pattern: period stripping shouldn't
        # cause "Point72" to match "Point Park University".
        assert employer_match("Point Park University", ["Point72"]) == []
        # And shouldn't make "S.A.C." match arbitrary "sac" substrings outside
        # the firm context — "Backpack" contains "ac" not "sac" so OK; but
        # let's verify the strict-substring rule is still enforced.
        assert employer_match("Backpack Industries", ["SAC"]) == []


# ─── classify() — the main spec tests ──────────────────────────────────────


def _record(**overrides) -> dict:
    """Build a minimal FEC-shaped record dict."""
    base = {
        "transaction_id": "TXN_TEST",
        "contributor_name": "Steven A Cohen",
        "contributor_employer": "",
        "contributor_occupation": "",
        "contributor_city": "",
        "contributor_state": "",
        "contributor_zip": "",
    }
    base.update(overrides)
    return base


class TestClassify:
    def test_no_name_match_returns_none(self, owner):
        r = _record(contributor_name="Jane Doe")
        assert classify(r, owner) is None

    def test_two_confirming_signals_confirms(self, owner):
        # employer + city/state = 2 confirming signals
        r = _record(
            contributor_employer="Point72 Securities LLC",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == CONFIRMED
        assert result.entity_slug == "cohen-steven"
        assert any("employer:Point72" in s for s in result.signals_matched)
        assert any("city_state:greenwich/CT" in s for s in result.signals_matched)

    def test_one_strong_signal_confirms(self, owner):
        # Cohen Private Ventures is strong → CONFIRMED on its own.
        r = _record(contributor_employer="Cohen Private Ventures LLC")
        result = classify(r, owner)
        assert result is not None
        assert result.status == CONFIRMED
        assert any("strong_employer:Cohen Private Ventures" in s for s in result.signals_matched)

    def test_one_confirming_signal_probable(self, owner):
        # Employer match, but no city/state filled and no other confirming
        # signal → exactly one confirming signal → PROBABLE.
        r = _record(contributor_employer="Point72 Securities LLC")
        result = classify(r, owner)
        assert result is not None
        assert result.status == PROBABLE

    def test_name_only_uncertain(self, owner):
        # Name match, no employer / occupation / city signals → UNCERTAIN.
        r = _record(contributor_name="Steven Cohen")
        result = classify(r, owner)
        assert result is not None
        assert result.status == UNCERTAIN
        assert "name match only" in result.status_reason

    def test_suffix_mismatch_uncertain_regardless_of_signals(self, owner):
        # Even with employer match (which would normally CONFIRM with city),
        # suffix mismatch demotes to UNCERTAIN.
        r = _record(
            contributor_name="Steven Cohen Jr",
            contributor_employer="Cohen Private Ventures",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == UNCERTAIN
        assert "suffix mismatch" in result.status_reason

    def test_spouse_name_collision_principals_only_returns_none(self, owner):
        # In principals-only mode, a record matching the spouse is dropped.
        r = _record(contributor_name="Alexandra Cohen", contributor_city="Greenwich", contributor_state="CT")
        result = classify(r, owner, process_related_entities=False)
        assert result is None

    def test_spouse_name_collision_full_mode_routes_to_spouse(self, owner):
        r = _record(
            contributor_name="Alexandra Cohen",
            contributor_employer="Steven and Alexandra Cohen Foundation",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, owner, process_related_entities=True)
        assert result is not None
        assert result.entity_slug == "cohen-alexandra"
        assert result.entity_kind == "spouse"
        assert result.parent_owner_slug == "cohen-steven"
        # Strong signal employer match → CONFIRMED
        assert result.status == CONFIRMED

    def test_address_contradiction_without_documentation_uncertain(self, owner):
        # Employer matches, but city is "Chicago" (not in our docs).
        # Per VERIFICATION.md, this demotes to UNCERTAIN.
        r = _record(
            contributor_employer="Point72 Asset Management LLC",
            contributor_city="Chicago",
            contributor_state="IL",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == UNCERTAIN
        assert "city/state outside documented residences" in result.status_reason

    def test_two_confirming_signals_employer_and_occupation(self, owner):
        # employer + occupation = 2 confirming signals.
        r = _record(
            contributor_employer="Point72 Securities",
            contributor_occupation="Owner",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == CONFIRMED

    def test_one_strong_signal_with_address_contradiction_is_uncertain(self, owner):
        # Even a strong signal should be overridden by address contradiction —
        # the rule's purpose is to catch family-name collisions where the
        # employer is shared but the donor lives elsewhere.
        r = _record(
            contributor_employer="Cohen Private Ventures",
            contributor_city="Boston",
            contributor_state="MA",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == UNCERTAIN
        assert "city/state outside documented residences" in result.status_reason

    def test_last_first_format_matches(self, owner):
        r = _record(
            contributor_name="COHEN, STEVEN A",
            contributor_employer="Point72",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == CONFIRMED

    def test_empty_city_does_not_trigger_address_contradiction(self, owner):
        # Address contradiction only fires when city is filled — empty city
        # should not demote.
        r = _record(contributor_employer="Cohen Private Ventures")
        result = classify(r, owner)
        assert result is not None
        assert result.status == CONFIRMED  # strong signal still applies

    def test_state_only_match_is_not_a_confirming_signal(self, owner):
        # City+state requires BOTH — state alone (with empty city) should not
        # count as a confirming signal. But it also shouldn't trigger address
        # contradiction (empty city is treated as unknown, not contradictory).
        r = _record(contributor_state="CT", contributor_employer="Point72")
        result = classify(r, owner)
        assert result is not None
        # One confirming signal (employer) → PROBABLE.
        assert result.status == PROBABLE


class TestNegativeSignals:
    def test_negative_employer_demotes_to_uncertain(self, owner):
        # Even with strong city+state match, an Elliott Management employer
        # demotes to UNCERTAIN per the negative-signal rule.
        r = _record(
            contributor_employer="Elliott Management Corp",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == UNCERTAIN
        assert "negative employer signal" in result.status_reason
        assert any("Elliott Management" in s for s in result.signals_matched)

    def test_negative_employer_overrides_strong_signal(self, owner):
        # If a record somehow matches BOTH a strong employer AND a negative
        # employer (shouldn't happen in practice, but defensively), negative
        # wins — demote to UNCERTAIN.
        r = _record(
            contributor_employer="Elliott Mgmt and Cohen Private Ventures LLC",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == UNCERTAIN
        assert "negative" in result.status_reason

    def test_negative_employer_case_insensitive(self, owner):
        r = _record(
            contributor_employer="ELLIOTT MGMT LLC",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == UNCERTAIN

    def test_no_negative_signals_when_block_absent(self, owner):
        owner_no_neg = {k: v for k, v in owner.items() if k != "negative_signals"}
        r = _record(
            contributor_employer="Elliott Management",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        # Without the negative block, the same record passes through normally:
        # employer doesn't match positive signals, but city+state does → PROBABLE.
        result = classify(r, owner_no_neg)
        assert result is not None
        assert result.status == PROBABLE


# ─── Edge cases worth pinning down ──────────────────────────────────────────


class TestStructuredNameFallback:
    def test_synthetic_name_from_first_last_when_contributor_name_ambiguous(self, owner):
        # FEC sometimes records contributor_name as "LAST FIRST INITIAL" with no
        # comma — e.g., "COHEN STEVEN A" — which the literal normalizer reads as
        # First=Cohen, Last=A. The structured contributor_first_name +
        # contributor_last_name fields disambiguate.
        r = _record(
            contributor_name="COHEN STEVEN A",
            contributor_first_name="STEVEN",
            contributor_middle_name="A",
            contributor_last_name="COHEN",
            contributor_employer="Point72 Asset Management",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, owner)
        assert result is not None
        # Strong signal (Point72 Asset Management) → CONFIRMED.
        assert result.status == CONFIRMED

    def test_synthetic_name_not_used_when_literal_name_matches(self, owner):
        # When the literal contributor_name already matches a variant, we
        # should use that directly (not the synthetic).
        r = _record(
            contributor_name="Steven Cohen",
            contributor_first_name="STEVEN",
            contributor_last_name="COHEN",
        )
        result = classify(r, owner)
        assert result is not None  # should classify (UNCERTAIN since no signals)


class TestEdgeCases:
    def test_record_with_no_contributor_name_returns_none(self, owner):
        r = _record(contributor_name="")
        assert classify(r, owner) is None

    def test_signals_matched_is_json_serializable(self, owner):
        import json
        r = _record(contributor_employer="Cohen Private Ventures")
        result = classify(r, owner)
        # Round-trip ensures DB persistence works.
        s = json.dumps(result.signals_matched)
        assert json.loads(s) == result.signals_matched


# ─── H4: comma + suffix normalization (the dominant FEC "Last, First Suffix") ─


class TestCommaSuffixNormalization:
    def test_comma_form_suffix_detected(self):
        # "Last, First Middle Suffix" — the suffix lands mid-string after the
        # comma-swap, so trailing-only detection used to miss it (suffix=None).
        forms, suffix, mids = normalize_name("DeWitt, William O Jr")
        assert "william dewitt" in forms
        assert suffix == "jr"
        assert mids == frozenset({"o"})

    def test_comma_form_roman_numeral_suffix_detected(self):
        forms, suffix, _ = normalize_name("HENRY, JOHN WILLIAM II")
        assert "john william henry" in forms
        assert suffix == "ii"

    def test_double_comma_suffix_detected(self):
        # "Last, First M., Suffix" (Kendrick form).
        _, suffix, _ = normalize_name("Kendrick, Earl G., Jr.")
        assert suffix == "jr"

    def test_comma_record_matches_noncomma_suffixed_variant(self):
        # FEC files "DEWITT, WILLIAM O JR"; the owner YAML carries the
        # non-comma variant "William O DeWitt Jr". They must match WITH suffix
        # agreement — previously this was a no-match (the owner's own donation
        # silently skipped).
        canon, suf, _ = names_match("DEWITT, WILLIAM O JR", ["William O DeWitt Jr"])
        assert canon and suf

    def test_dewitt_jr_vs_iii_disambiguation(self):
        # The documented critical case: father (Jr.) vs son (III). Both
        # canonically match, but the suffix differs → suffix_ok False → the
        # son's filing is correctly demoted, not attributed to the father.
        canon, suf, _ = names_match(
            "DEWITT, WILLIAM O III", ["William O DeWitt Jr", "DeWitt, William O Jr"]
        )
        assert canon and not suf

    def test_bare_v_midstring_is_initial_not_suffix(self):
        # A single-letter "V" mid-string is a middle initial, not a Roman
        # numeral suffix (it is captured as a middle initial, not a suffix).
        _, suffix, mids = normalize_name("John V Smith")
        assert suffix is None
        assert mids == frozenset({"v"})

    def test_bare_v_trailing_is_suffix(self):
        _, suffix, mids = normalize_name("John Smith V")
        assert suffix == "v"
        assert mids == frozenset()


# ─── M2: state-aware address contradiction ──────────────────────────────────


class TestCrossStateContradiction:
    def test_same_city_name_wrong_state_demotes(self, owner):
        # Greenwich is a documented city and CT/NY documented states, but
        # Greenwich, KS is a different place. Employer matches, yet the wrong
        # state must demote to UNCERTAIN (city+state is the unit).
        r = _record(
            contributor_employer="Point72 Securities",
            contributor_city="Greenwich",
            contributor_state="KS",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == UNCERTAIN
        assert "city/state outside documented residences" in result.status_reason

    def test_documented_city_state_pair_not_contradicted(self, owner):
        # Greenwich, CT is a documented residence — employer + city/state → 2
        # confirming signals → CONFIRMED (no contradiction).
        r = _record(
            contributor_employer="Point72 Securities",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, owner)
        assert result is not None
        assert result.status == CONFIRMED


# ─── H4b: related-entity routing requires suffix agreement ───────────────────


@pytest.fixture
def sr_owner():
    """Owner 'John Smith Sr' with a related child 'John Smith Jr' — same
    canonical name, differing suffix (the misrouting trigger)."""
    return {
        "slug": "smith-sr",
        "name": "John Smith Sr",
        "name_variants": ["John Smith Sr", "Smith, John Sr"],
        "verifying_signals": {"employers": ["Acme"], "cities": [], "states": []},
        "strong_signals": {},
        "negative_signals": {},
        "related_entities": [
            {
                "kind": "child",
                "slug": "smith-jr",
                "name": "John Smith Jr",
                "name_variants": ["John Smith Jr", "Smith, John Jr"],
                "verifying_signals": {"employers": ["Beta"]},
                "strong_signals": {},
                "negative_signals": {},
            }
        ],
    }


class TestRelatedEntitySuffixRouting:
    def test_owner_record_routes_to_owner_not_jr_child(self, sr_owner):
        # The owner's own filing ("...Sr") must NOT be misrouted to the Jr
        # child just because the canonical names collide.
        r = _record(contributor_name="Smith, John Sr", contributor_employer="Acme Inc")
        result = classify(r, sr_owner, process_related_entities=True)
        assert result is not None
        assert result.entity_slug == "smith-sr"
        assert result.entity_kind == "owner"

    def test_child_record_routes_to_child_on_suffix_agreement(self, sr_owner):
        r = _record(contributor_name="Smith, John Jr", contributor_employer="Beta LLC")
        result = classify(r, sr_owner, process_related_entities=True)
        assert result is not None
        assert result.entity_slug == "smith-jr"
        assert result.entity_kind == "child"
        assert result.parent_owner_slug == "smith-sr"

    def test_owner_record_principals_only_still_classifies_owner(self, sr_owner):
        # Principals-only mode: the Sr record falls through the suffix-mismatched
        # child and is classified as the owner (not dropped).
        r = _record(contributor_name="John Smith Sr", contributor_employer="Acme Inc")
        result = classify(r, sr_owner, process_related_entities=False)
        assert result is not None
        assert result.entity_slug == "smith-sr"


@pytest.fixture
def mid_owner():
    """Middleton-shaped owner: father 'John S. Middleton' with a related child
    'John P. Middleton'. Same first+last, no suffix difference — separable ONLY
    by middle initial. This is the case the middle-initial rule unlocks:
    before it, the son's records collapsed onto the father and had to be
    handled with an EXCLUDE override; now they route to his own entity."""
    return {
        "slug": "middleton-john",
        "name": "John S. Middleton",
        "name_variants": ["John Middleton", "John S Middleton", "Middleton, John S"],
        "verifying_signals": {"employers": [], "cities": [], "states": []},
        "strong_signals": {"employers": ["Bradford Holdings"], "zip_codes": []},
        "negative_signals": {},
        "related_entities": [
            {
                "kind": "child",
                "slug": "middleton-john-p",
                "name": "John P. Middleton",
                "name_variants": ["John P Middleton", "Middleton, John P"],
                "verifying_signals": {},
                "strong_signals": {"employers": ["Vertigo Entertainment"]},
                "negative_signals": {},
            }
        ],
    }


class TestRelatedEntityMiddleInitialRouting:
    """The payoff of the middle-initial rule: a same-named relative who differs
    only by middle initial routes to their own entity instead of being
    misattributed to the principal (or needing an EXCLUDE workaround)."""

    def test_son_record_routes_to_son_by_middle_initial(self, mid_owner):
        r = _record(
            contributor_name="MIDDLETON, JOHN P",
            contributor_employer="Vertigo Entertainment",
        )
        result = classify(r, mid_owner, process_related_entities=True)
        assert result is not None
        assert result.entity_slug == "middleton-john-p"
        assert result.entity_kind == "child"
        assert result.parent_owner_slug == "middleton-john"
        assert result.status == CONFIRMED  # son's strong-signal employer

    def test_father_record_routes_to_owner_not_son(self, mid_owner):
        # The father's filing must NOT be swept onto the son just because the
        # bare canonical name collides — the son's "P" conflicts with "S".
        r = _record(
            contributor_name="MIDDLETON, JOHN S",
            contributor_employer="Bradford Holdings",
        )
        result = classify(r, mid_owner, process_related_entities=True)
        assert result is not None
        assert result.entity_slug == "middleton-john"
        assert result.entity_kind == "owner"
        assert result.status == CONFIRMED

    def test_son_record_principals_only_dropped_not_misattributed(self, mid_owner):
        # Principals-only mode: the son's record is dropped (out of scope), NOT
        # folded into the father's totals.
        r = _record(
            contributor_name="MIDDLETON, JOHN P",
            contributor_employer="Vertigo Entertainment",
        )
        result = classify(r, mid_owner, process_related_entities=False)
        assert result is None


class TestCityStateAloneInsufficient:
    """The `city_state_alone_insufficient` flag (OWNER_SCHEMA.md) — for common names
    like "John Fisher", a lone city_state hit must not reach PROBABLE."""

    def _flagged(self, owner):
        o = dict(owner)
        o["city_state_alone_insufficient"] = True
        return o

    def test_off_by_default_city_state_alone_is_probable(self, owner):
        # Baseline: without the flag, a documented city+state alone → PROBABLE.
        r = _record(contributor_city="Greenwich", contributor_state="CT")
        result = classify(r, owner)
        assert result is not None and result.status == PROBABLE
        assert any(s.startswith("city_state:") for s in result.signals_matched)

    def test_flag_demotes_lone_city_state_to_uncertain(self, owner):
        r = _record(contributor_city="Greenwich", contributor_state="CT")
        result = classify(r, self._flagged(owner))
        assert result is not None and result.status == UNCERTAIN
        assert "city/state alone" in result.status_reason

    def test_flag_keeps_two_signal_confirmed(self, owner):
        # city_state still counts as the second signal toward CONFIRMED.
        r = _record(
            contributor_employer="Point72 Securities LLC",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, self._flagged(owner))
        assert result is not None and result.status == CONFIRMED

    def test_flag_does_not_touch_strong_signal(self, owner):
        # A strong signal at a documented city still CONFIRMS (the Fisher/Pisces case).
        r = _record(
            contributor_employer="Cohen Private Ventures LLC",
            contributor_city="Greenwich",
            contributor_state="CT",
        )
        result = classify(r, self._flagged(owner))
        assert result is not None and result.status == CONFIRMED

    def test_flag_leaves_lone_employer_probable(self, owner):
        # The flag targets only city_state; a lone employer/occupation still → PROBABLE.
        r = _record(contributor_employer="Point72 Securities LLC")
        result = classify(r, self._flagged(owner))
        assert result is not None and result.status == PROBABLE

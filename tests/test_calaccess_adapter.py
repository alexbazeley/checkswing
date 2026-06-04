"""Tests for the CAL-ACCESS adapter (scripts/calaccess_adapter.py).

The crucial assertion is integration: a CAL-ACCESS-shaped row, mapped by the
adapter, flows through the UNCHANGED classifier (resolve_entities.classify) and
lands in the correct three-tier bucket — proving the federal classifier is reused
verbatim for state data.
"""
from __future__ import annotations

from scripts import calaccess_adapter as ca
from scripts.resolve_entities import CONFIRMED, PROBABLE, UNCERTAIN, classify


# A minimal CA-resident owner signal block (shape mirrors owners/*.yaml).
OWNER = {
    "slug": "moreno-arte",
    "name": "Arturo Moreno",
    "name_variants": ["Arturo Moreno", "Arte Moreno", "Moreno, Arturo"],
    "verifying_signals": {
        "cities": ["phoenix"],
        "states": ["AZ"],
        "employers": ["Outdoor Systems", "Anaheim Angels", "Los Angeles Angels"],
        "occupations": ["owner"],
    },
    "strong_signals": {},
    "negative_signals": {},
}


def _rcpt(**over) -> dict:
    row = {
        "ENTITY_CD": "IND",
        "CTRIB_NAML": "MORENO",
        "CTRIB_NAMF": "ARTURO",
        "CTRIB_NAMS": "",
        "CTRIB_EMP": "Outdoor Systems",
        "CTRIB_OCC": "Owner",
        "CTRIB_CITY": "Phoenix",
        "CTRIB_ST": "AZ",
        "CTRIB_ZIP4": "85016",
        "AMOUNT": "1500.00",
        "RCPT_DATE": "6/1/2018 12:00:00 AM",
        "TRAN_ID": "T1",
        "FILING_ID": "F100",
    }
    row.update(over)
    return row


def test_to_classifier_record_maps_fields():
    rec = ca.to_classifier_record(_rcpt())
    assert rec["contributor_name"] == "MORENO, ARTURO"
    assert rec["contributor_last_name"] == "MORENO"
    assert rec["contributor_first_name"] == "ARTURO"
    assert rec["contributor_employer"] == "Outdoor Systems"
    assert rec["contributor_occupation"] == "Owner"
    assert rec["contributor_city"] == "Phoenix"
    assert rec["contributor_state"] == "AZ"


def test_date_parsing():
    assert ca.parse_calaccess_date("6/1/2018 12:00:00 AM") == "2018-06-01"
    assert ca.parse_calaccess_date("2018-06-01") == "2018-06-01"
    assert ca.parse_calaccess_date("2018-06-01T00:00:00") == "2018-06-01"
    assert ca.parse_calaccess_date("not a date") is None
    assert ca.parse_calaccess_date("") is None


def test_amount_parsing():
    assert ca.parse_amount("1,500.00") == 1500.0
    assert ca.parse_amount("$2500") == 2500.0
    assert ca.parse_amount("") is None
    assert ca.parse_amount("abc") is None


def test_election_cycle_is_calendar_year():
    assert ca.election_cycle_from_date("2018-06-01") == 2018
    assert ca.election_cycle_from_date(None) is None


def test_business_name_has_no_comma_form():
    rec = ca.to_classifier_record(_rcpt(CTRIB_NAML="OUTDOOR SYSTEMS INC", CTRIB_NAMF=""))
    assert rec["contributor_name"] == "OUTDOOR SYSTEMS INC"


# ── Integration through the real classifier ─────────────────────────────────


def test_classifier_confirmed_two_signals():
    """employer + city/state → CONFIRMED."""
    rec = ca.to_classifier_record(_rcpt())
    c = classify(rec, OWNER)
    assert c is not None and c.status == CONFIRMED
    assert c.entity_slug == "moreno-arte"


def test_classifier_probable_one_signal():
    """employer matches but address is elsewhere & not contradicting via a documented
    city → exactly one confirming signal → PROBABLE. (Use a city absent from the
    signal block but in a state that is also absent, so city_state simply doesn't fire;
    address_contradicts only demotes when a documented city set exists for that state.)"""
    rec = ca.to_classifier_record(
        _rcpt(CTRIB_CITY="", CTRIB_ST="", CTRIB_OCC="Investor")
    )
    c = classify(rec, OWNER)
    assert c is not None and c.status == PROBABLE


def test_classifier_uncertain_name_only():
    rec = ca.to_classifier_record(
        _rcpt(CTRIB_EMP="", CTRIB_OCC="", CTRIB_CITY="", CTRIB_ST="")
    )
    c = classify(rec, OWNER)
    assert c is not None and c.status == UNCERTAIN


def test_classifier_filters_non_matching_name():
    rec = ca.to_classifier_record(_rcpt(CTRIB_NAML="SMITH", CTRIB_NAMF="JOHN"))
    assert classify(rec, OWNER) is None


def test_classifier_uncertain_on_address_contradiction():
    """Documented residence is Phoenix/AZ; a Greenwich/CT row with the same name +
    matching employer must demote to UNCERTAIN (family-name-collision guard)."""
    rec = ca.to_classifier_record(_rcpt(CTRIB_CITY="Greenwich", CTRIB_ST="CT"))
    c = classify(rec, OWNER)
    assert c is not None and c.status == UNCERTAIN

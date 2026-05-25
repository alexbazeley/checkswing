"""
Tests for the dashboard build pipeline:
  - scripts.parse_provenance  — PROVENANCE_LOG.md → structured entries
  - scripts.dollars            — CPI inflation adjustment + FEC committee-type labels

The parser tests lock down the shape of the changelog output. The dollars
tests cover the helpers wired into both build_data.py and the recipients
page. Both modules are intentionally minimal; these tests guard against
format drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.dollars import (
    CPI_BASE_YEAR,
    CPI_LATEST_MONTH,
    CPI_TABLE,
    committee_type_label,
    to_real,
)
from scripts.parse_provenance import (
    KNOWN_TYPES,
    parse_heading,
    parse_provenance_log,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_LOG = REPO_ROOT / "catalog" / "PROVENANCE_LOG.md"


# ─── Heading parser ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line, expected",
    [
        (
            "### 2026-05-22 — SETUP",
            {"date": "2026-05-22", "timestamp_utc": "2026-05-22",
             "type": "SETUP", "raw_type": "SETUP", "subject": ""},
        ),
        (
            "### 2026-05-22T18:00Z — NOTE — Cohen broad-fetch attempt aborted",
            {"date": "2026-05-22", "timestamp_utc": "2026-05-22T18:00Z",
             "type": "NOTE", "raw_type": "NOTE",
             "subject": "Cohen broad-fetch attempt aborted"},
        ),
        (
            "### 2026-05-25 — SIGNAL_CHANGE — cohen-steven.yaml",
            {"date": "2026-05-25", "timestamp_utc": "2026-05-25",
             "type": "SIGNAL_CHANGE", "raw_type": "SIGNAL_CHANGE",
             "subject": "cohen-steven.yaml"},
        ),
        (
            "### 2026-05-22 — SETUP (Phase 1 build)",
            {"date": "2026-05-22", "timestamp_utc": "2026-05-22",
             "type": "SETUP", "raw_type": "SETUP",
             "subject": "(Phase 1 build)"},
        ),
    ],
)
def test_parse_heading_variants(line: str, expected: dict) -> None:
    got = parse_heading(line)
    assert got == expected


def test_parse_heading_unknown_type_buckets_as_note() -> None:
    got = parse_heading("### 2026-05-22 — FUTURE_TYPE — something")
    assert got["raw_type"] == "FUTURE_TYPE"
    assert got["type"] == "NOTE"


def test_parse_heading_malformed_returns_none() -> None:
    assert parse_heading("## Not a level-3 heading") is None
    assert parse_heading("### no date") is None
    assert parse_heading("### 2026-05-22 lowercase_type") is None


# ─── Body rendering ─────────────────────────────────────────────────────────


def test_ingestion_body_renders_bullets_and_code() -> None:
    md = "\n".join([
        "### 2026-05-22 — INGESTION",
        "",
        "- **run_id**: `3ea399e7`",
        "- **entity_slug**: `cohen-steven`",
        "- **records_fetched**: `2961`",
    ])
    entries = parse_provenance_log(md)
    assert len(entries) == 1
    html = entries[0]["body_html"]
    assert "<ul>" in html and "</ul>" in html
    assert "<li>" in html
    assert "<strong>run_id</strong>" in html
    assert "<code>3ea399e7</code>" in html
    assert "<code>cohen-steven</code>" in html
    # No raw markdown markers should leak through.
    assert "**" not in html
    assert "`" not in html


def test_prose_body_renders_paragraphs_and_bold() -> None:
    md = "\n".join([
        "### 2026-05-22 — NOTE — a note",
        "",
        "First paragraph with **bold** in it.",
        "",
        "Second paragraph follows after a blank line.",
    ])
    entries = parse_provenance_log(md)
    html = entries[0]["body_html"]
    assert html.count("<p>") == 2
    assert "<strong>bold</strong>" in html
    # Adjacent text on the same line collapses to one paragraph.
    assert "First paragraph with <strong>bold</strong> in it." in html


def test_mixed_body_groups_consecutive_bullets() -> None:
    md = "\n".join([
        "### 2026-05-22 — NOTE",
        "",
        "Intro line.",
        "",
        "- one",
        "- two",
        "- three",
        "",
        "Outro line.",
    ])
    entries = parse_provenance_log(md)
    html = entries[0]["body_html"]
    # Exactly one <ul> for the run of 3 bullets, not three.
    assert html.count("<ul>") == 1
    assert html.count("<li>") == 3
    assert html.startswith("<p>Intro line.</p>")
    assert html.endswith("<p>Outro line.</p>")


def test_html_escapes_before_markup() -> None:
    """A body containing literal HTML must not become real HTML."""
    md = "\n".join([
        "### 2026-05-22 — NOTE",
        "",
        "Watch out for <script>alert('xss')</script> in **filenames**.",
    ])
    entries = parse_provenance_log(md)
    html = entries[0]["body_html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    # Bold survived around the now-escaped angle bracket text.
    assert "<strong>filenames</strong>" in html


def test_empty_body_yields_empty_html() -> None:
    md = "### 2026-05-22 — NOTE\n"
    entries = parse_provenance_log(md)
    assert entries[0]["body_html"] == ""


def test_inline_subheadings_become_h4_and_h5() -> None:
    md = "\n".join([
        "### 2026-05-22 — NOTE",
        "",
        "Intro.",
        "",
        "#### Subsection",
        "Body of subsection.",
        "",
        "##### Sub-subsection",
        "More body.",
    ])
    entries = parse_provenance_log(md)
    html = entries[0]["body_html"]
    assert "<h4>Subsection</h4>" in html
    assert "<h5>Sub-subsection</h5>" in html
    assert "<p>Body of subsection.</p>" in html


def test_preamble_before_first_heading_is_skipped() -> None:
    md = "\n".join([
        "# Top",
        "Intro prose.",
        "## Format",
        "Spec.",
        "## Entries",
        "",
        "### 2026-05-22 — NOTE",
        "First real entry.",
    ])
    entries = parse_provenance_log(md)
    assert len(entries) == 1
    assert entries[0]["date"] == "2026-05-22"


# ─── Full-corpus integrity ──────────────────────────────────────────────────


@pytest.mark.skipif(not REAL_LOG.exists(), reason="catalog/PROVENANCE_LOG.md not present")
def test_full_corpus_parses_cleanly() -> None:
    """
    Lock down the real PROVENANCE_LOG.md: every entry must have a non-empty
    date, a known-or-bucketed TYPE, and a non-empty body_html. Catches future
    format drift before it reaches production.
    """
    entries = parse_provenance_log(REAL_LOG.read_text(encoding="utf-8"))
    assert len(entries) > 0
    seen_types = set()
    for e in entries:
        assert e["date"], f"empty date in entry: {e}"
        assert e["type"] in KNOWN_TYPES, f"unbucketed type in entry: {e}"
        assert e["body_html"], (
            f"empty body_html for {e['date']} {e['raw_type']} — "
            "headings should always carry a body in this corpus"
        )
        seen_types.add(e["raw_type"])
    # The corpus today contains at least these — this guards against a parser
    # regression that silently drops common entry types.
    for must_have in {"INGESTION", "SIGNAL_CHANGE", "SETUP"}:
        assert must_have in seen_types, f"corpus missing {must_have}"


# ─── Inflation helper (scripts/dollars.py) ──────────────────────────────────


def test_cpi_table_covers_all_election_cycles() -> None:
    """The dashboard renders cycles 2000-2026. CPI lookup must succeed for all."""
    for year in range(2000, CPI_BASE_YEAR + 1):
        assert year in CPI_TABLE, f"missing CPI for {year}"
        assert CPI_TABLE[year] > 0


def test_cpi_table_monotonic_modulo_2009() -> None:
    """
    CPI rises year-over-year except for the 2008→2009 deflation. Anything
    else suggests a typo'd value.
    """
    years = sorted(CPI_TABLE)
    for prev, nxt in zip(years[:-1], years[1:]):
        if prev == 2008 and nxt == 2009:
            continue  # known small dip
        assert CPI_TABLE[nxt] >= CPI_TABLE[prev], (
            f"CPI fell from {prev} ({CPI_TABLE[prev]}) to {nxt} ({CPI_TABLE[nxt]})"
        )


def test_to_real_base_year_is_identity() -> None:
    """A donation in the CPI base year should be unchanged."""
    assert to_real(1000.0, CPI_BASE_YEAR) == pytest.approx(1000.0)


def test_to_real_2000_inflates() -> None:
    """A 2000 donation has been hit hard by inflation."""
    adjusted = to_real(1000.0, 2000)
    # Sanity: somewhere between 1.5x and 2.5x of nominal.
    assert 1500 < adjusted < 2500
    # Exact: CPI_base / CPI_2000.
    expected = 1000.0 * CPI_TABLE[CPI_BASE_YEAR] / CPI_TABLE[2000]
    assert adjusted == pytest.approx(expected)


def test_to_real_null_year_returns_nominal() -> None:
    """Missing cycle → no adjustment, treat as already-current."""
    assert to_real(1234.56, None) == pytest.approx(1234.56)


def test_to_real_unknown_year_returns_nominal() -> None:
    """Year outside the CPI table → no adjustment (no extrapolation)."""
    assert to_real(500.0, 1995) == pytest.approx(500.0)
    assert to_real(500.0, 2099) == pytest.approx(500.0)


def test_to_real_none_amount_returns_zero() -> None:
    """Defensive: None amount maps to 0.0 rather than crashing."""
    assert to_real(None, 2020) == 0.0


def test_cpi_latest_month_format() -> None:
    """Footnote text expects YYYY-MM."""
    parts = CPI_LATEST_MONTH.split("-")
    assert len(parts) == 2 and len(parts[0]) == 4 and len(parts[1]) == 2
    int(parts[0]); int(parts[1])  # both parseable


# ─── Committee type label (scripts/dollars.py) ──────────────────────────────


@pytest.mark.parametrize(
    "code, bucket",
    [
        ("P", "Candidate"),
        ("S", "Candidate"),
        ("H", "Candidate"),
        ("X", "Party"),
        ("Y", "Party"),
        ("Z", "Party"),
        ("Q", "PAC"),
        ("N", "PAC"),
        ("O", "PAC"),
        ("V", "PAC"),
        ("W", "PAC"),
        ("U", "PAC"),
        ("I", "PAC"),
        ("D", "PAC"),
        ("C", "PAC"),
        ("E", "PAC"),
    ],
)
def test_committee_type_label_known_codes(code: str, bucket: str) -> None:
    assert committee_type_label(code) == bucket


@pytest.mark.parametrize("missing", [None, "", " ", "Z9", "??", "x"])
def test_committee_type_label_missing_or_unknown(missing) -> None:
    """Unknown codes, whitespace, or None all bucket as Other.
    Lowercase is accepted (uppercased internally)."""
    if missing == "x":  # x lowercases to X (Party) — verify case-insensitivity
        assert committee_type_label(missing) == "Party"
    else:
        assert committee_type_label(missing) == "Other"

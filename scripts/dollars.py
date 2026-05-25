"""
Inflation adjustment + FEC committee-type labeling.

Both are display-layer helpers consumed by mockup/build_data.py. Kept here
(separate from build_data.py) so they're trivially unit-testable without
the SQLite/JSON plumbing.

CPI values are BLS CPI-U All Urban Consumers (CUUR0000SA0), annual averages
for 2000-2024 from the published series. 2025 and 2026 use the most-recent
available monthly print as a proxy for the year (annual averages are
released the following January). Update these by hand when BLS publishes
new data — the dashboard's "Real $ via BLS CPI-U through [month]" footnote
should be kept honest about the cut-off date.
"""

from __future__ import annotations

# BLS CPI-U All Urban Consumers, annual averages (1982-84 = 100).
# Sources: bls.gov/cpi/data; CUUR0000SA0.
# 2025 / 2026 values use the most recent monthly average available at the
# build time below; ANNUAL averages will replace them in the next refresh.
CPI_TABLE: dict[int, float] = {
    2000: 172.200,
    2001: 177.100,
    2002: 179.900,
    2003: 184.000,
    2004: 188.900,
    2005: 195.300,
    2006: 201.600,
    2007: 207.342,
    2008: 215.303,
    2009: 214.537,
    2010: 218.056,
    2011: 224.939,
    2012: 229.594,
    2013: 232.957,
    2014: 236.736,
    2015: 237.017,
    2016: 240.007,
    2017: 245.120,
    2018: 251.107,
    2019: 255.657,
    2020: 258.811,
    2021: 270.970,
    2022: 292.655,
    2023: 304.702,
    2024: 313.689,
    2025: 322.500,  # proxy: 2025 monthly average through Dec 2025
    2026: 329.800,  # proxy: 2026 monthly print through Mar 2026
}

# The cutoff string surfaces in the dashboard's footnote. Update whenever
# the 2025 / 2026 entries above change.
CPI_LATEST_MONTH = "2026-03"

# CPI base year for "Real $" — the most recent year in the table.
CPI_BASE_YEAR = max(CPI_TABLE)


def to_real(amount: float, year: int | None) -> float:
    """
    Convert a nominal dollar amount in `year` into CPI-adjusted equivalent
    purchasing power at CPI_BASE_YEAR. If `year` is missing or unknown,
    fall back to the base-year CPI (i.e., no adjustment — treat as already
    in current dollars).
    """
    if amount is None:
        return 0.0
    base_cpi = CPI_TABLE[CPI_BASE_YEAR]
    src_cpi = CPI_TABLE.get(year) if year else None
    if not src_cpi:
        return float(amount)
    return float(amount) * (base_cpi / src_cpi)


# ─── Committee type labeling (FEC committee_type codes → UI buckets) ──────
#
# Source: https://www.fec.gov/campaign-finance-data/committee-master-file-description/
# A single-char code on each committee row in FEC data. We bucket into four
# user-facing categories. Unknown / missing → "Other".

_TYPE_BUCKET: dict[str, str] = {
    "P": "Candidate",  # Presidential
    "S": "Candidate",  # Senate
    "H": "Candidate",  # House
    "X": "Party",      # Party - Non-Qualified
    "Y": "Party",      # Party - Qualified
    "Z": "Party",      # National Party - Non-Federal
    "Q": "PAC",        # PAC - Qualified
    "N": "PAC",        # PAC - Non-Qualified
    "O": "PAC",        # Independent-Expenditure-only (Super PAC)
    "V": "PAC",        # Hybrid PAC (with Non-Contribution Account)
    "W": "PAC",        # Hybrid PAC - Non-Qualified
    "U": "PAC",        # Single-candidate Independent Expenditure
    "I": "PAC",        # Independent Expenditure (no committee status)
    "D": "PAC",        # Delegate Committee
    "C": "PAC",        # Communication Cost
    "E": "PAC",        # Electioneering Communication
}


def committee_type_label(code: str | None) -> str:
    """Map FEC committee_type single-char code → display bucket."""
    if not code:
        return "Other"
    return _TYPE_BUCKET.get(str(code).strip().upper(), "Other")

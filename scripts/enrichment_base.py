"""Shared config + helpers for the committee/filing enrichment ingesters.

`ingest_committees`, `ingest_filings`, and `ingest_committee_disbursements` all
skip a FEC re-fetch when a row was refreshed within FRESHNESS_DAYS. That
constant and the age-comparison logic were copy-pasted across all three; they
live here now so the freshness policy is defined once.
"""
from __future__ import annotations

from datetime import datetime, timezone

# A committee / filing / (committee, cycle) row refreshed within this many days
# is considered current; re-running within the window skips the FEC fetch.
# Override per-call via --force-refresh on the relevant CLI command.
FRESHNESS_DAYS = 30


def fresh_within_days(
    refreshed_at: str | None,
    *,
    days: int = FRESHNESS_DAYS,
    now: datetime | None = None,
) -> bool:
    """True if `refreshed_at` is within `days` of now.

    `refreshed_at` is an ISO-8601 UTC string (e.g. "2026-05-28T00:00:00Z").
    Missing or unparseable timestamps are treated as stale (not fresh) so the
    row gets re-fetched. `now` is injectable for testing.
    """
    if not refreshed_at:
        return False
    try:
        refreshed = datetime.fromisoformat(str(refreshed_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    current = now or datetime.now(timezone.utc)
    return (current - refreshed).total_seconds() / 86400 < days

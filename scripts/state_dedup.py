"""Content-key dedup shared across state portals (§1.2).

Some portals re-report one logical contribution as multiple rows with DIFFERENT
native ids, so a native-id dedup (keying on the portal's row/transaction id)
keeps them all and inflates dollar totals:

  - **IL / TX** re-file the same contribution across multiple filings
    (overlapping reporting periods / amendments) — different `filing_id`, same
    donor+date+amount+recipient.
  - **AZ** returns the same contribution several times with DIFFERENT
    `PublicTransactionId`s (a ×N fan-out), so even a transaction-id dedup can't
    fold them.

`content_key_dedupe` collapses rows that are the same logical contribution
(donor, date, amount, recipient), keeping the highest-ranked row (latest filing)
deterministically so the citing filing is stable across re-ingests. This mirrors
the CAL-ACCESS `dedupe_receipts` "overlapping reporting periods" fold, but keyed
WITHOUT the reassigned native id (the whole point). Two genuinely-separate
identical contributions on one filing are collapsed too — an accepted, documented
trade-off for these portals (STATE_DONATION_SCHEMA.md): overstated totals are
worse than an occasional merged twin-gift.

The consolidation of the near-identical per-fetcher `dedupe` bodies into one
shared module is deferred (§8.1, on hold); this helper is the shared core the
IL/TX/AZ fetchers call today.
"""
from __future__ import annotations

from typing import Callable, Hashable, Iterable


def filing_rank(filing_id: str | None, tran_id: str | None) -> tuple:
    """Deterministic 'latest filing wins' rank. Numeric filing ids compare
    numerically (so '9' < '10'); ties fall back to the raw strings."""
    f = (filing_id or "").strip()
    t = (tran_id or "").strip()
    try:
        fnum = int(f)
    except ValueError:
        fnum = -1
    return (fnum, f, t)


def content_key_dedupe(
    rows: Iterable[dict],
    *,
    key_fn: Callable[[dict], Hashable],
    rank_fn: Callable[[dict], tuple],
) -> list[dict]:
    """Keep one row per content key — the max-`rank_fn` row. Deterministic."""
    best: dict[Hashable, tuple[tuple, dict]] = {}
    for row in rows:
        k = key_fn(row)
        r = rank_fn(row)
        if k not in best or r > best[k][0]:
            best[k] = (r, row)
    return [v[1] for v in best.values()]

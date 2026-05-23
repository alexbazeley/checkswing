# SOURCES — Approved Data Sources

This file enumerates what counts as an authoritative source for each piece of data this project records. The categories are not interchangeable. Sources in one tier do not "promote" data sourced from a different tier.

## Tier 1: Primary, authoritative

These are the only sources that can populate a donation record's substantive fields.

### OpenFEC API (`api.open.fec.gov`)
- **Use for**: every donation record. No exceptions.
- **Endpoints relied on**:
  - `/schedules/schedule_a/` — individual contributions to federal committees
  - `/schedules/schedule_e/` — independent expenditures (Phase 3+ relevance)
  - `/committees/` — committee metadata (for recipient context)
  - `/candidates/` — candidate metadata (for committee → candidate joins)
- **Authentication**: free API key from api.data.gov
- **Rate limit**: 1,000 requests/hour by default. Build a rate-limited client; do not hammer.
- **Caching policy**: every response is persisted raw to `data/raw/` before parsing (CLAUDE.md §1.4). Never re-fetch a record we already have unless we have reason to believe FEC restated it.

### FEC bulk data files (`https://www.fec.gov/data/browse-data/?tab=bulk-data`)
- **Use for**: large historical backfills where API pagination would be slow.
- Each bulk import is logged in `catalog/PROVENANCE_LOG.md` with file URL, SHA256, and import date.
- Same attribution rules apply — bulk import does not lower the verification bar.

## Tier 2: Authoritative for entity identification (NOT for donation facts)

These sources tell us **who to track** and **what signals identify them**. They are recorded in the owner YAML's `sources` block. They never populate a donation record's substantive fields.

### MLB-published ownership records
- `mlb.com/<team>` team pages, official press releases announcing ownership changes.
- Authoritative for principal owner identification.

### Major-press business reporting
- Wall Street Journal, New York Times, Bloomberg, Forbes (for ownership-stake reporting, not Forbes valuations specifically).
- The Athletic, ESPN long-form for ownership transitions and family structure.
- Used to identify spouses, family members, business entities — never to confirm a donation.

### Corporate / regulatory filings
- SEC filings (10-Ks, proxies) for publicly-traded parent companies.
- State business entity registrations for verifying corporate structures.
- These are excellent for confirming "Owner X controls Company Y" links that gate PAC attribution.

### Owner's own public profiles
- Corporate websites, LinkedIn, official biographies.
- Used to populate occupation and employer signals.
- Treated with appropriate skepticism — these are self-descriptions.

## Tier 3: Cross-reference only

May be used to **cross-check** facts already established via Tier 1 or 2. Never as the sole source for anything.

### OpenSecrets (`opensecrets.org`)
- Derivative of FEC data. Useful for sanity-checking aggregates ("does our total for Cohen roughly match theirs?").
- Their attribution choices may differ from ours; that is acceptable and not a reason to change our standard.
- Never the source of a record — only a cross-check.

### Political donation news reporting
- Tampa Bay Times, NYT, Bloomberg political reporting that names specific donations.
- May surface donations we haven't picked up; the proper response is to **find them in FEC** and ingest from there. The news article is a pointer, not the record.

### Wikipedia
- Useful for biographical facts (spouse names, business history) as a *starting point*.
- Every claim used here must be confirmed via a Tier 1 or 2 source before being recorded.
- Never cited in our YAMLs.

## Sources explicitly OUT

- **Twitter / X / Reddit / fan forums.** Not sources. Pointers at best.
- **AI-generated summaries** (including Claude's training-data recall) of who donated what. If we don't have the FEC record, we don't have the donation.
- **State campaign-finance data**, until Phase 4. Not because state data is unreliable, but because mixing federal and state coverage prematurely produces records with inconsistent provenance and confuses users.
- **Leaked or non-public donor lists.** Public FEC data only.
- **Aggregator scrapes from third parties** that don't preserve FEC transaction IDs. Without the transaction ID, idempotency (§1.5) is impossible.

## Source recording

Every owner YAML's `sources` block must record:
- The MLB-published or major-press source establishing them as a tracked principal owner.
- The corporate / biographical source establishing each related entity (spouse, business, PAC).
- The source for each `verifying_signals` value that isn't trivially derivable (e.g., a known city is fine without a citation; a less-obvious employer string should cite where we got it).

Each source entry records: `description`, `url`, `accessed` (YYYY-MM-DD), and where applicable `archive_url` (Wayback Machine snapshot).

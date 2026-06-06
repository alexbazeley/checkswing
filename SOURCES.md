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
- **Caching policy**: every response is persisted raw to `data/raw/` before parsing (GOVERNANCE.md §1.4). Never re-fetch a record we already have unless we have reason to believe FEC restated it.

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

## Phase 3 addendum — legislation, votes, legislators

Phase 3 (CHARTER.md §Phase 3) builds a neutral index of MLB-relevant federal
legislation, roll-call votes, and the legislator crosswalk, then joins it to the
donation data. That requires sources beyond FEC. Adopting them is a deliberate,
documented scope expansion (GOVERNANCE.md §5); they are held to the same tiering
discipline as donation data. The legislation index stores neutral, sourced facts
only — interpretation lives in `reports/`, never in a row (project CLAUDE.md §2,
GOVERNANCE.md §6).

### Tier 1: Primary, authoritative (populate substantive legislation fields)

- **Congress.gov API** (`api.congress.gov`) — official Library of Congress / GPO.
  Use for bill identity, title, sponsors, cosponsors, actions, and enacted status.
  Fronted by **api.data.gov**, so the same key system as FEC (`CONGRESS_API_KEY`,
  falls back to `FEC_API_KEY`).
- **House Clerk roll-call XML** (`clerk.house.gov`) and **Senate roll-call XML**
  (`senate.gov/legislative/LIS/roll_call_lists`) — the source of record for vote
  positions (who voted Yea/Nay on a given roll call). Congress.gov vote data is a
  cross-check, not the cited origin.
- **OpenFEC `/candidate/<id>/`** (already in use) — to cross-check that the FEC
  candidate ids appearing in our donation set map to the legislators the crosswalk
  claims, before any join is trusted.

### Tier 2: Authoritative for entity identification (NOT for vote/donation facts)

- **`unitedstates/congress-legislators`** (public-domain `legislators-current.yaml`
  + `legislators-historical.yaml`). The canonical open crosswalk: each legislator
  carries `id.bioguide`, `id.icpsr`, `id.govtrack`, `id.opensecrets`, and an
  `id.fec` **array** (one legislator → many FEC candidate ids), plus `terms`
  (chamber / state / district / party / dates). This is the **FEC-id → Bioguide**
  map that makes the donation↔vote join possible. Treated like owner-identity
  Tier-2 data: it tells us *who* a candidate id is, never *what* they voted or
  *whether* a donation occurred. The subset of FEC ids present in our donations is
  cross-checked against OpenFEC (Tier 1) before use.

### Tier 3: Cross-reference only

- **GovTrack** (`govtrack.us`) — a derived mirror of official congressional data.
  Useful to sanity-check a vote tally or a bill's status; never the source of record.
- **OpenSecrets**, **Wikipedia**, **Ballotpedia** — biographical / contextual
  starting points, confirmed via Tier 1/2 before anything is recorded.

### Explicitly OUT for Phase 3

- **ProPublica Congress API** — sunset in 2024; not used.
- **Editorial relevance framing inside the index.** *Which* bills are MLB-relevant
  is a curatorial selection, but each indexed bill records a **sourced, factual**
  `relevance_basis` (e.g. "amends 15 U.S.C. §26b, MLB's antitrust exemption";
  "exempts MiLB players from FLSA §13(a); text inserted as a division of H.R.1625"),
  not a characterization of motive or wrongdoing. Spin lives in `reports/`.
- **Inferring intent from temporal proximity.** A computed "donation N days before
  vote Z" is a neutral arithmetic fact stored/queried as such. The claim that the
  donation *caused* the vote is interpretation and belongs only in `reports/`.

## Phase 4 addendum — state campaign finance (multi-state)

Phase 4 (CHARTER.md §Phase 4) extends the archive to state campaign-finance
contributions, stored in the *separate* `data/state.db` and held to the same
attribution + verification + provenance discipline as the federal data
(GOVERNANCE.md §1.11). The official state portal is the **record**; an aggregator is
only a **discovery pointer**. State sources are adopted one state at a time — each is
a documented scope expansion (GOVERNANCE.md §5), wired in through the `StateSource`
registry (`scripts/state_sources.py`) so the classifier, schema, and dashboard stay
source-agnostic. Adopted so far: California (CAL-ACCESS, approved 2026-06-03),
New York (NYSBOE), and Texas (TEC, added 2026-06-06); a PA-DOS adapter is built and
registered. Other states still require sign-off.

### Tier 1: Primary, authoritative (the cited source of every state row)

- **CAL-ACCESS** (California Secretary of State / FPPC) — California's official
  disclosure system and the source of record for CA state contributions. Each
  CONFIRMED/PROBABLE `state_donations` row cites a CAL-ACCESS filing
  (`source = "CAL-ACCESS"`, `source_filing_id`, `source_tran_id`, `raw_payload_path`).
- **TEC** (Texas Ethics Commission) — Texas's official disclosure system and the
  source of record for TX state contributions. The whole database is published as one
  public bulk zip (`TEC_CF_CSV.zip`, no login/API key) at
  `prd.tecprd.ethicsefile.com/public/cf/public/TEC_CF_CSV.zip`, refreshed ~daily:
  itemized contributions split across `contribs_NN.csv` (plus `cont_ss.csv` /
  `cont_t.csv`), with `filers.csv` the recipient lookup. Receipts carry contributor
  employer + occupation + city/state/zip, so the two-signal CONFIRMED bar is reachable
  (a gold-grade portal, like CAL-ACCESS). `source = "TEC"`.
- **NYSBOE** (New York State Board of Elections, via the data.ny.gov SODA API) — the
  source of record for NY state contributions. ZIP-grade disclosure only (no employer/
  occupation/state), so CONFIRMED rests on an exact ZIP match. `source = "NYSBOE"`.
- **California Civic Data Coalition (CCDC) mirror**
  (`calaccess.californiacivicdata.org/downloads/latest/`) — a daily-refreshed,
  documented, tab-delimited republication of the raw CAL-ACCESS files (`RCPT_CD`
  receipts, `FILERNAME_CD` filer lookup). A faithful *convenience copy* of the Tier-1
  filings, not a separate analytical source; used because the SoS bulk download is
  the same data in a harder shape. Field docs:
  `calaccess.californiacivicdata.org/documentation/raw-files/rcpt-cd/`.

### Tier 2: Discovery only (NOT a source of donation facts)

- **The Accountability Project** (`publicaccountability.org`, Investigative Reporting
  Workshop) — normalized, donor-name-searchable state contributions across ~35
  states. May be used to **discover** that a candidate record exists (recorded in
  `state_donations.discovery_source`); the fact itself must then be confirmed against
  the CAL-ACCESS extract. An aggregator-only hit not found in the portal goes to the
  state review queue, never the canonical export.
- **FollowTheMoney / NIMP** (`followthemoney.org`, now part of OpenSecrets) — same
  discovery-only role. Note: unmaintained, coverage only through 2024; preferred
  second to TAP, and — because it is discovery-only — a dead aggregator degrades
  gracefully (CAL-ACCESS remains the Tier-1 spine).

### Explicitly OUT for Phase 4 (for now)

- **Blending state rows into `master.db`.** State data lives only in `data/state.db`.
- **Paper-only / non-machine-readable state portals.** A state stays out until its
  disclosure data is available in a machine-readable Tier-1 form; coverage is
  honestly partial and reports say so.
- **Treating any aggregator as the record.** Per GOVERNANCE.md §1.11/§3, aggregators
  are pointers, never the cited origin of a contribution fact.

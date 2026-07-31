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

### Raw-payload archive (durable, off-runner — §2.1)

Every API/portal response is persisted verbatim to `data/raw/` before parsing
(GOVERNANCE.md §1.4). Because `data/raw/` is gitignored and the monthly refresh
runs on ephemeral runners, each run's `data/raw/` delta is also uploaded to a
durable **Cloudflare R2** bucket (S3-compatible) via `scripts/archive_raw.sh`,
keyed to mirror the on-disk path (`data/raw/…` → `s3://<bucket>/raw/…`). A stored
`raw_payload_path` therefore resolves off-runner via `cli fetch-raw <txn>`.

| | |
|---|---|
| Bucket | `checkswing-raw` (Cloudflare R2) |
| S3 endpoint | `https://b77af9764905e334519c19d89b35b754.r2.cloudflarestorage.com` |
| Key layout | `data/raw/<slug>/<file>` → `s3://checkswing-raw/raw/<slug>/<file>` |
| Backfilled | **2026-07-31** — 10,941 objects / 5,258,518,839 bytes (4.90 GB), verified identical to local |

The bucket name and endpoint are **configuration, not credentials**, and are
recorded here deliberately: §2.1's acceptance criteria asked for the bucket to be
recorded, but only "the credentials are secret" was written down, so the runbook
could not be followed without a dashboard trip. Only the access-key pair is
secret — held as the `RAW_ARCHIVE_*` GitHub Actions secrets, never in the repo.
The same bucket also holds `deploy/master.db`, which `scripts/fetch_deploy_db.py`
pulls at Cloudflare Pages build time. Provisioning + backfill steps:
[docs/DESIGN_raw_archival_2026-07.md](docs/DESIGN_raw_archival_2026-07.md).

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
- **`unitedstates/congress-legislators` committee files** (`committees-current.yaml`
  + `committee-membership-current.yaml`). The current-congress roster of who sits
  on which committee, keyed by `thomas_id` (e.g. SSJU, HSWM) with member `bioguide`
  ids. Powers the `--via-committee` join (donations → current members of a bill's
  committee of referral). **Current snapshot only — the upstream files carry no
  membership history**, so the join is guarded to bills of the current congress
  (`committees.congress`); a present-day member is never asserted to have handled a
  historical bill. A bill's committee(s) of referral come from the Congress.gov
  `/bill/{c}/{type}/{n}/committees` endpoint (Tier 1).

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
New York (NYSBOE), Texas (TEC), Pennsylvania (PA-DOS), Illinois (ISBE), Washington
(WA-PDC), Colorado (CO-TRACER), Arizona (AZ-SOS), Minnesota (MN-CFB), and Florida
(FL-DOE, added 2026-06-08). Other states still require sign-off.

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

### State reconnaissance ledger (§5.5)

Non-adopted states that were **assessed** during Phase-4 expansion, recorded here so
the determinations survive the session memory they were made in. These are
**recon notes, not exhaustive proofs of absence** — re-verify before acting, and a
"walled" or "dead-for-roster" state can flip if a portal changes or the roster does.

| State | Status | Why (as assessed) | Assessed |
|---|---|---|---|
| MA | dead-for-roster | no tracked owner surfaced ingestable state giving; deprioritized | 2026-06 |
| WI | dead-for-roster | same — Brewers/Attanasio filings not found at a Tier-1 depth | 2026-06 |
| DC | dead-for-roster | same; DC filings sparse for the roster | 2026-06 |
| MI | dead-for-roster | portal recon did not yield a machine-readable owner-level ingest (Ilitch home state — a **coverage gap**, not a confirmed absence) | 2026-06 |
| OH | **portal-walled — CONFIRMED by header evidence** | Re-probed 2026-07-20 with a browser User-Agent + full browser headers after the MD finding. Still **403**, and the response carries `server: cloudflare` + **`cf-mitigated: challenge`** with `critical-ch: Sec-CH-UA-*` — a genuine Cloudflare **managed challenge** requiring client hints and JS, not a UA check. This is a real wall, now evidenced rather than inferred. (Castellini ×2 — *current* Reds owners — and Dolan home state; the most valuable blocked state.) | 2026-06, re-probed 2026-07-20 |
| GA | **DISPUTED — reachable, needs a real bulk/API probe** | Re-probed 2026-07-20: `https://media.ethics.ga.gov/search/Campaign/Campaign_Name.aspx` returns **HTTP 200** to a plain `curl` with a browser UA, with **no Cloudflare headers at all**. So "portal-walled" is not supported at the HTTP layer. The original verdict may have meant "no machine-readable bulk export found", which is a different (and still possible) finding — but it should not be recorded as a wall. **Needs a proper probe of whether a bulk/API path exists** before GA is written off. (McGuirk home state.) | 2026-06, re-probed 2026-07-20 |
| MO | postback-only (reachable) | Re-probed 2026-07-20: mec.mo.gov returns 200 and the contribution search 302s — **reachable, not walled**. The recorded reason (ASP.NET postback-only export, no clean bulk endpoint) is about form mechanics rather than access control, so the "walled" half of the original label was misleading. (Sherman-john, DeWitt home state.) | 2026-06, re-probed 2026-07-20 |
| MD | **adoptable — pending §5 sign-off** | MDCRIS (campaignfinance.maryland.gov) exposes an unauthenticated public JSON API — `POST api-campaignfinance.maryland.gov/api/PublicGrid/GetContributionList` with server-side `contributorName` filtering — plus per-year bulk CSV via `ExportPublicData/GetExportPublicDownloadData` (`{"Type":"CSV","TransactionTypeCode":"TCON","FilingYear":"YYYY"}`; 195 MB for 2024), refreshed daily. **The bare-curl 403 is a User-Agent check, not a wall** — adding a browser UA returns 200 (the FL lesson again). **ZIP/address-grade: no employer or occupation field** (MD does not collect them), but full street address, which corroborates better than NY's ZIP-only. Presence-check: angelos-john-p 16 recs / $62,000 (filed from 333 W. Camden St — Oriole Park — and his documented Nashville address); lerner-mark 20 recs / $19,917 (Lerner Enterprises HQ, Rockville 20852); **rubenstein-david zero** (94 Rubenstein records, no David). Coverage is **~2018→present only** — the rebuilt portal does not appear to expose the pre-2018 history the SBE page describes. **Known hazard:** the documented Chesapeake Partners "Mark Lerner" doppelgänger files $31,250 from Pikesville 21208 and is separable here **only by address**, because MD data cannot feed the employer negative-signal block that guards him federally. | 2026-07-19 |

**Owner home states with zero state coverage anywhere** (adoption gaps, not
determinations): **OH** (Castellini ×2, Dolan), **MI** (Ilitch), **MD/DC**
(Rubenstein, Lerner, Angelos), **GA** (McGuirk), **MO** (Sherman-john, DeWitt),
**WV** (Nutting), **CT** (Seidler/Feliciano). Adopting any of these remains a
GOVERNANCE §5 scope expansion requiring sign-off.

**MD was assessed 2026-07-19** and is the first entry in the table above to come
back **adoptable** rather than dead or walled — see its row for the endpoints, the
grade, and the doppelgänger hazard. Adoption itself still requires GOVERNANCE §5
sign-off and has not been done.

**A finding worth generalizing from the MD check:** the bare-`curl` 403 that would
have read as a wall was a **User-Agent check**. Between that and the FL precedent
(believed Cloudflare-walled, actually a plain `curl` + POST), several states here
were declared walled on evidence of exactly that shape.

**That re-probe has now been run (2026-07-20), and it split three ways** — which is
the useful part, because it shows "walled" was doing too much work as a label:

- **OH is genuinely walled**, and now provably so: `cf-mitigated: challenge` +
  `critical-ch: Sec-CH-UA-*` from Cloudflare. A browser UA is not enough; it wants
  client hints and JS. This is the expensive one — Castellini ×2 are current owners.
- **GA is reachable** (HTTP 200, no Cloudflare headers). Its "walled" verdict does
  not hold at the HTTP layer and should be re-opened.
- **MO is reachable** too; its real obstacle is ASP.NET postback-only export, which
  is a *form-mechanics* problem, not access control.

**The lesson to carry forward: record the mechanism, not the symptom.** "403" and
"walled" are symptoms that turned out to mean three different things — a UA check
(MD), a Cloudflare challenge (OH), and a postback form (MO). Only the second is a
wall, and only the header evidence distinguishes them.

### Local / municipal campaign finance — DEFERRED (§ Phase 4)

**Status: deferred, not out of scope.** CHARTER.md §Phase 4 is titled "State and
local" and lists "pull stadium-relevant state and local donations"; no municipal
adapter, fetcher, or registry entry has ever been built, and no local record exists
in `data/state.db`. This entry records the deferral so the gap stops being an
undocumented contradiction between the charter and the dashboard's methodology page.

**Why deferred:** municipal and county campaign-finance disclosure is per-jurisdiction,
frequently non-machine-readable, and has no registry analog to the state
`StateSource` pattern — each city or county is its own source-and-adapter problem at a
fraction of a state's dollar coverage. The Phase-4 exit criterion is stated in terms of
*states*, and eight home states remain unadopted; local work would come after that.

**What is being deferred, so the decision stays re-openable.** The stadium-subsidy
fights — editorially the highest-value money in the project — are overwhelmingly
municipal/county:

| jurisdiction | fight | already annotated at |
|---|---|---|
| Clark County / Las Vegas, NV | Athletics relocation, $380M public financing | `owners/fisher-john.yaml` (NV filings flagged post-relocation) |
| Jackson County, MO | Royals/Chiefs stadium sales-tax vote, April 2024 | `owners/sherman-john.yaml:80` |
| Tampa / St. Petersburg, FL | Rays stadium plan | `owners/zalupski-patrick.yaml:122` |
| Oakland, CA | Howard Terminal / Coliseum | — |
| Chicago, IL | "The 78" riverfront stadium proposal | — |

The editorial hooks are already recorded in the owner files; only the pipeline is
absent. **Ballot-measure money is a separate case and IS captured** wherever a *state*
portal reports it (`recipient_type='ballot_measure'`), e.g. Fisher's $1,180,000 against
CA Prop 30 and $401,436 on "No on 82" — so "local is deferred" must not be read as
"ballot measures are missing."

Assessed 2026-07-19.

### Explicitly OUT for Phase 4 (for now)

- **Blending state rows into `master.db`.** State data lives only in `data/state.db`.
- **Paper-only / non-machine-readable state portals.** A state stays out until its
  disclosure data is available in a machine-readable Tier-1 form; coverage is
  honestly partial and reports say so.
- **Treating any aggregator as the record.** Per GOVERNANCE.md §1.11/§3, aggregators
  are pointers, never the cited origin of a contribution fact.

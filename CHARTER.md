# CHARTER — MLB Owner FEC Donations Archive

## Mission

Build and continuously maintain a verified database of every federal political donation reported to the FEC by MLB principal owners and their immediate, documented entities — so that Tipping Pitches research can cite specific, accurate, current donation records when discussing owner political behavior.

## Why this exists

MLB owners are political actors. Their donations are evidence of policy preference, influence-buying, and where they want power to sit. The FEC publishes all of this. But the data is per-transaction, by-name, with no donor IDs — so reconstructing one owner's political donation history across cycles, name variations, and employer changes is **slow, error-prone work** unless someone has already done it carefully.

OpenSecrets does some of this, but inconsistently and not under TP's editorial lens (which cares about MLB-specific cross-cuts that OpenSecrets does not surface). This archive fills that gap: per-owner, with verifying provenance, queryable, kept current.

## Editorial framing

The database is **factual infrastructure**, not editorial. Every row should survive an external audit. Editorial framing — what these patterns mean, who they implicate, what they say about MLB labor relations — happens in `reports/` and on the show itself, built **on top of** the data, never inside it.

The conservative attribution standard is itself an editorial commitment: we'd rather miss a real donation than incorrectly attribute one.

## In scope (Phase 1)

- Federal political donations reported to the FEC, all years available.
- Donations by principal/majority owners of all 30 MLB franchises, plus the highest-profile minority owners where they exert public influence (case by case, documented).
- Donations by owners' spouses, where publicly identified as such in major-press reporting.
- Donations by named adult family members (children, parents) where the relationship is publicly disclosed and the family member is identified in FEC filings.
- PACs directly affiliated with teams (rare — historically only one or two examples; document case by case).
- PACs of corporate parent companies for corporate-owned teams (e.g., Liberty Media for the Braves). Attribution only when the team-ownership link is documented.
- **Recipient committee enrichment.** For any committee that received an attributed donation: factual identity (designation, type, status, party, treasurer, connected organization, first/last filing dates) and lifetime per-cycle scale (receipts, disbursements, cash on hand). Sourced from OpenFEC `/committee/<id>/` and `/committee/<id>/totals/`. Optional hand-curated external-link pointers (Wikipedia / Ballotpedia) are surfaced as cross-references only, never as fact.
  - **Beneficiaries (Phase 1b).** For any committee in scope, the top-N recipients per cycle as reported on Schedule B `by_recipient` — i.e. "who this committee in turn funded." Names and amounts only. Sourced from OpenFEC `/schedules/schedule_b/by_recipient/?committee_id=<id>&cycle=<c>`. This is factual recipient metadata; cross-referencing to specific legislation, votes, or policy outcomes remains Phase 3 (see GOVERNANCE.md §6 — committee-to-candidate names are factual, not editorial linkage). Schedule B aggregates both political and operational disbursements; the dashboard surfaces what FEC reports without filtering on purpose codes.

## Out of scope (explicit, not just "later")

- **State and local campaign finance.** Different sources per state, often paper-only. Phase 4 territory — **now active as a California pilot** in a *separate* `data/state.db` (see Phase 4 below); the federal `master.db` remains FEC-only and any report says which layer a figure comes from.
- **501(c)(4) "dark money."** Not in FEC data. Tracked elsewhere if at all.
- **Federal lobbying disclosures.** A different LD-1 / LD-2 system. Worth a sibling project; not this one.
- **Cross-referencing donations to specific legislation, votes, or regulatory outcomes.** Phase 3.
- **Charitable donations.** IRS 990s, separate project.
- **Aggregated employer-based queries** (e.g., "every donor who reports employer = Yankees") without a named-individual anchor. That is a guilt-by-association approach the project does not endorse.
- **Donations by team employees who are not owners.** Coaches, executives, players — out.
- **Privately-disclosed political activity** (e.g., leaked donor records). Public FEC data only.

## Phases

### Phase 0 — Setup
- Create the directory structure and base files (GOVERNANCE.md, this file, schemas, registry).
- Obtain FEC API key (free, instant via api.data.gov).
- Define Python environment and dependencies.
- Initialize git.
- **Exit criteria:** base files present, environment installable, FEC API key working.

### Phase 1 — Cohen pilot
- Build the full ingestion → scoring → verification → export pipeline end-to-end against a single pilot owner (Steven A. Cohen, Mets).
- Validate the matching rules produce sensible CONFIRMED / PROBABLE / UNCERTAIN distributions.
- Resolve at least the first batch of UNCERTAIN records manually to calibrate signal quality.
- **Exit criteria:** Cohen has a complete CSV export with provenance; UNCERTAIN review queue is non-empty (proof we're being conservative); zero misattributions found on manual audit of a sample of CONFIRMED rows.

### Phase 1.5 — Expand pilot to 4 more owners
- Apply the validated pipeline to Crane (Astros), Henry (Red Sox), Castellini (Reds), Steinbrenner (Yankees). These are chosen for editorial relevance (already in opp research) and matching difficulty diversity (e.g., common names vs. uncommon).
- Refine the matching logic based on what each new owner reveals.
- **Exit criteria:** five owners each with full export; matching logic is stable; no further code changes needed to add the remaining 25.

### Phase 2 — All 30 owners
- Populate `verifying_signals` for the remaining 25 owners.
- Run the pipeline league-wide.
- Add spouses + named family members where publicly documented.
- Document and track team-affiliated PACs where they exist.
- **Exit criteria:** every MLB principal owner has an export; review queue is being adjudicated on a regular cadence.

### Phase 3 — Cross-referencing
- Build a parallel index of MLB-relevant federal legislation, votes, and regulatory actions (antitrust exemption, minor league pay, stadium-related federal action, broadcast/IP cases).
- Pair donations with relevant policy timeline.
- Enable queries like "donations from owner X to legislator Y in the 90 days before vote Z."
- **Exit criteria:** at least one publishable per-episode brief generated end-to-end from the joined data.

### Phase 4 — State and local — **ACTIVE (California pilot)**
- Identify each owner's primary state/local political exposure (team's home state, owner's residence state, stadium-deal jurisdictions).
- Connect to that state's campaign finance database.
- Pull stadium-relevant state and local donations.
- Merge under the same entity model and verification standard.
- **Exit criteria:** at minimum, every team's home state covered.

**Sourcing policy (approved 2026-06-03).** State data is **hybrid-sourced**: an
aggregator may *discover* candidate records, but the **official state portal is the
primary source** and every CONFIRMED/PROBABLE row traces to an official filing — the
state-level analog of the FEC rule (GOVERNANCE.md §1.1, §1.3, §3). No aggregator
stands in as the record.

**Architecture.** State contributions live in a *separate* `data/state.db`
(mirroring the Phase-3 `legislation.db` split) so `master.db` stays federal-clean.
The same three-tier classifier (`scripts/resolve_entities.py`) is reused verbatim;
only a per-portal input adapter differs. Coverage is **state-by-state and honestly
partial** — some states are paper-only and stay out until machine-readable.

**Pilot — California (CAL-ACCESS).** California first: 5 of 30 teams, and the
gold-standard open-data portal (CAL-ACCESS, via the California Civic Data Coalition
mirror) whose receipts carry employer + occupation + city, so the two-signal
CONFIRMED bar is reachable.
- **Pilot exit criteria (mirrors the Phase-1 Cohen bar):** CA-team owners have a
  complete `state.db` export with provenance; the state review queue is non-empty
  (conservative proof); zero misattributions on a manual audit of a CONFIRMED sample.
- The CA pilot proves the pattern; a `StateAdapter` runbook then makes state #2
  (NY or TX, by team count) a new adapter + source-tiering, not a rewrite.

### Phase 5 — Maintenance and automation
- Scheduled quarterly pulls (FEC quarterly reporting cycle).
- 48-hour notice tracking in election seasons.
- Diff reports surface new donations as they appear.
- **Exit criteria:** the project requires near-zero manual maintenance to stay current.

## Decision points the user should weigh in on

- **Minority owners.** Currently out. The Brewers' Mark Attanasio is a principal; a Pohlad heir who's a 5% minority partner is not. But where do we draw the line? Recommendation: principal/managing owners only in Phase 1–2, revisit for Phase 3.
- **Pre-ownership donation history.** Do we want Cohen's donations from before he bought the Mets in 2020? Recommendation: yes, with a clear `tenure_start_date` field on the owner, so reports can filter to "post-ownership" donations when relevant.
- **Spouses as separate entities.** Alexandra Cohen is a major donor in her own right. Recommendation: track as a related entity with her own verifying_signals — never auto-attribute her donations to him; always tag clearly.
- **Backfill cadence.** FEC historical data goes back decades but with declining quality and coverage. Recommendation: aim for 2000 forward as a hard floor, opportunistic on pre-2000.

## Success looks like

- Any Tipping Pitches research question of the form "what has owner X given politically?" can be answered from this database in under five minutes.
- A donation cited on the show can be traced from the show note back to a specific FEC filing in two clicks.
- A new ingestion run, in steady state, adds zero misattributions to the canonical export.
- When the 2026 CBA fight intensifies, every relevant owner can be researched against a current donation record without lead time.
